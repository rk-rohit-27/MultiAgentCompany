import asyncio
import io
import json
import logging
import re
import signal
import sys

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackQueryHandler, CommandHandler

from .agents import Agents, Research
from .config import Settings
from .graph import Company
from .qa import SandboxQA
from .store import Store
from .vault import Vault, safe_path

log = logging.getLogger("company")


def authorized(update, cfg):
    return (
        update.effective_user is not None
        and update.effective_chat is not None
        and update.effective_user.id == cfg.owner_user_id
        and update.effective_chat.id == cfg.owner_chat_id
        and update.effective_chat.type == "private"
    )


def graph_config(project):
    return {"configurable": {"thread_id": project}, "recursion_limit": 100}


class Controller:
    def __init__(self, cfg, store, graph, vault):
        self.cfg, self.store, self.graph, self.vault = cfg, store, graph, vault
        self.stop = asyncio.Event()

    async def help(self, update, context):
        if authorized(update, self.cfg):
            await update.effective_message.reply_text(
                "Use /new <product idea>, /status, /revise <project id> <feedback>, "
                "or /retry <project id> after fixing an infrastructure error."
            )

    async def new(self, update, context):
        if not authorized(update, self.cfg):
            return
        idea = " ".join(context.args).strip()
        if not 10 <= len(idea) <= 2000:
            await update.effective_message.reply_text("Use /new <product idea, 10–2000 characters>")
            return
        try:
            project = self.store.enqueue(idea, self.cfg.max_jobs)
            await update.effective_message.reply_text(
                f"Queued {project}. Use /status to follow it."
            )
        except ValueError as e:
            await update.effective_message.reply_text(str(e))

    async def status(self, update, context):
        if not authorized(update, self.cfg):
            return
        text = "\n".join(f"{j['id']} — {j['status']}" for j in self.store.jobs()[-20:])
        await update.effective_message.reply_text(text or "No projects. Use /new <idea>.")

    async def retry(self, update, context):
        if not authorized(update, self.cfg):
            return
        project = context.args[0] if context.args else ""
        job = next((j for j in self.store.jobs() if j["id"] == project), None)
        if not job or job["status"] != "error":
            await update.effective_message.reply_text(
                "Use /retry <id> for an infrastructure error."
            )
            return
        self.store.status(project, "queued")
        await update.effective_message.reply_text("Retry queued from the last checkpoint.")

    async def revise(self, update, context):
        if not authorized(update, self.cfg):
            return
        if len(context.args) < 2:
            await update.effective_message.reply_text(
                "Use /revise <project id or approval token> <feedback>"
            )
            return
        feedback = " ".join(context.args[1:])
        if len(feedback) > 4000:
            await update.effective_message.reply_text("Feedback limit is 4000 characters.")
            return
        identifier = context.args[0]
        token = identifier
        if re.fullmatch(r"[a-f0-9]{16}", identifier):
            rows = self.store.db.execute(
                "SELECT token FROM approvals WHERE project=? ORDER BY revision DESC LIMIT 1",
                (identifier,),
            ).fetchone()
            token = rows["token"] if rows else ""
        accepted = self.store.decide(token, "revise", feedback)
        await update.effective_message.reply_text(
            "Revision queued." if accepted else "Stale approval."
        )

    async def callback(self, update, context):
        query = update.callback_query
        if not authorized(update, self.cfg):
            await query.answer("Unauthorized", show_alert=True)
            return
        match = re.fullmatch(r"(approve|revise|reject):([a-f0-9]{24})", query.data or "")
        if not match:
            await query.answer("Invalid button")
            return
        action, token = match.groups()
        if action == "revise":
            await query.answer()
            await query.message.reply_text(
                f"Send /revise {token} followed by your requested changes."
            )
            return
        accepted = self.store.decide(token, action)
        await query.answer("Decision saved" if accepted else "Already decided or stale")
        if accepted:
            await query.edit_message_reply_markup(reply_markup=None)

    async def work_once(self, job):
        project = job["id"]
        cfg = graph_config(project)
        snapshot = await self.graph.aget_state(cfg)
        pending = [i for task in snapshot.tasks for i in task.interrupts]
        old_approval = None
        if pending:
            payload = pending[0].value
            approval = self.store.approval(project, payload["revision"], payload)
            if not approval["decision"]:
                self.store.status(project, "waiting")
                self.vault.activity(project, "human", "Waiting for CEO approval", self.store.jobs())
                return
            prd_path = safe_path(self.vault.root, payload["prd_path"])
            if (
                json.loads(approval["decision"])["action"] == "approve"
                and self.vault.digest(prd_path.read_text()) != payload["prd_hash"]
            ):
                raise ValueError("PRD changed after approval was requested")
            old_approval = approval["token"]
            value = Command(resume={pending[0].id: json.loads(approval["decision"])})
        elif snapshot.values:
            if not snapshot.next:
                self.finish(project, snapshot.values)
                return
            value = None  # replay pending checkpoint, not initial state
        else:
            value = {"project_id": project, "idea": job["idea"]}
        self.store.status(project, "running")
        result = await self.graph.ainvoke(value, cfg, durability="sync")
        if old_approval:
            self.store.consume(old_approval)
        # Always inspect committed checkpoint. Never depend on in-memory result after a crash.
        snapshot = await self.graph.aget_state(cfg)
        pending = [i for task in snapshot.tasks for i in task.interrupts]
        if pending:
            payload = pending[0].value
            self.store.approval(project, payload["revision"], payload)
            self.store.status(project, "waiting")
            self.vault.activity(project, "human", "Waiting for CEO approval", self.store.jobs())
        else:
            self.finish(project, result)

    def finish(self, project, state):
        status = state.get("status", "error")
        if status not in {"completed", "failed", "rejected"}:
            status = "error"
        self.store.status(project, status)
        self.store.outbox(
            f"terminal:{project}:{status}",
            {
                "kind": "message",
                "text": f"Project {project}: {status}. "
                f"QA repairs: {state.get('retries', {})}. "
                "Review the vault and generated workspace. Nothing is deployed.",
            },
        )
        self.vault.activity(project, "qa", status, self.store.jobs())

    async def worker(self):
        while not self.stop.is_set():
            for job in self.store.jobs():
                if self.stop.is_set():
                    return
                if job["status"] not in {"queued", "running", "waiting"}:
                    continue
                try:
                    await self.work_once(job)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    # Log class only: exception text from API providers can contain secrets.
                    log.error("Project %s failed with %s", job["id"], type(e).__name__)
                    self.store.status(job["id"], "error", type(e).__name__)
                    self.store.outbox(
                        f"error:{job['id']}:{type(e).__name__}",
                        {
                            "kind": "message",
                            "text": f"Project {job['id']} stopped: {type(e).__name__}. "
                            "Check local configuration/logs, then /retry <id>. QA has not passed.",
                        },
                    )
                    self.vault.activity(
                        job["id"], "human", "Infrastructure error", self.store.jobs()
                    )
            try:
                await asyncio.wait_for(self.stop.wait(), 1)
            except TimeoutError:
                pass

    async def deliver_once(self, bot):
        for row in self.store.deliveries():
            payload = json.loads(row["payload"])
            try:
                if payload["kind"] == "approval":
                    token = payload["token"]
                    buttons = InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(label, callback_data=f"{act}:{token}")
                                for label, act in (
                                    ("APPROVE", "approve"),
                                    ("REVISE", "revise"),
                                    ("REJECT", "reject"),
                                )
                            ]
                        ]
                    )
                    # Full PRD is attached; summary alone is insufficient for informed approval.
                    with io.BytesIO(payload["prd_text"].encode()) as document:
                        await bot.send_document(
                            self.cfg.owner_chat_id,
                            document,
                            filename=f"PRD-{payload['project']}-r{payload['revision']}.md",
                        )
                    await bot.send_message(
                        self.cfg.owner_chat_id,
                        f"CEO decision: {payload['project']} / revision {payload['revision']}\n\n"
                        + payload["summary"]
                        + "\n\nRead the attached PRD before approving.",
                        reply_markup=buttons,
                    )
                else:
                    await bot.send_message(self.cfg.owner_chat_id, payload["text"])
                self.store.delivered(row["key"])
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("Notification delivery failed: %s", type(e).__name__)
                self.store.delivery_failed(row["key"], row["attempts"])

    async def notifier(self, bot):
        while not self.stop.is_set():
            self.vault.refresh(self.store.jobs())
            await self.deliver_once(bot)
            try:
                await asyncio.wait_for(self.stop.wait(), 2)
            except TimeoutError:
                pass


async def serve(cfg):
    cfg.prepare()
    # OS lock is held for process lifetime; no stale lease recovery races.
    lock = (cfg.data_dir / "service.lock").open("a")
    try:
        if sys.platform == "win32":
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise RuntimeError("Only one service process may use this data directory") from None
        else:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise RuntimeError("Only one service process may use this data directory") from None
    store = Store(cfg.data_dir / "control.sqlite")
    vault = Vault(cfg.vault_dir, cfg.workspace_dir)
    async with AsyncSqliteSaver.from_conn_string(str(cfg.data_dir / "checkpoints.sqlite")) as saver:
        await saver.setup()
        company = Company(
            cfg, vault, Agents(cfg, store), Research(cfg.searxng_url), SandboxQA(cfg), store
        )
        controller = Controller(cfg, store, company.compile(saver), vault)
        app = Application.builder().token(cfg.telegram_token.get_secret_value()).build()
        for name, handler in (
            ("start", controller.help),
            ("help", controller.help),
            ("new", controller.new),
            ("status", controller.status),
            ("revise", controller.revise),
            ("retry", controller.retry),
        ):
            app.add_handler(CommandHandler(name, handler))
        app.add_handler(CallbackQueryHandler(controller.callback))

        async def handler_error(update, context):
            log.error("Telegram handler failed: %s", type(context.error).__name__)

        app.add_error_handler(handler_error)
        loop = asyncio.get_running_loop()
        if sys.platform != "win32":
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, controller.stop.set)
        async with app:
            await app.start()
            await app.updater.start_polling(
                allowed_updates=["message", "callback_query"], drop_pending_updates=False
            )
            tasks = [
                asyncio.create_task(controller.worker()),
                asyncio.create_task(controller.notifier(app.bot)),
            ]
            stop_task = asyncio.create_task(controller.stop.wait())
            try:
                done, _ = await asyncio.wait(
                    [*tasks, stop_task], return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    if task is not stop_task:
                        task.result()  # Surface background failures so systemd can restart.
                        raise RuntimeError("Background worker exited unexpectedly")
            finally:
                stop_task.cancel()
                await asyncio.gather(stop_task, return_exceptions=True)
                controller.stop.set()
                # Cancel long model/sandbox calls; persisted checkpoints are resumed on restart.
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                await app.updater.stop()
                await app.stop()
    store.db.close()
    lock.close()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # Third-party INFO logs can include HTTP URLs containing Telegram bot credentials.
    for name in ("httpx", "httpcore", "telegram", "openai"):
        logging.getLogger(name).setLevel(logging.WARNING)
    asyncio.run(serve(Settings()))


if __name__ == "__main__":
    main()
