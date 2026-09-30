import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from company.config import Settings
from company.graph import Company
from company.schemas import Brief, CodeBundle, Contract, Issue, Review, SourceFile
from company.service import Controller, authorized, graph_config
from company.store import Store
from company.vault import Vault, safe_path


class FakeAgents:
    def __init__(self):
        self.calls = []

    async def generate(self, role, project, schema, instruction, data):
        self.calls.append(role)
        if schema is Brief:
            return Brief(markdown="# Product specification\n" + "Actionable validated MVP. " * 10)
        if schema is Contract:
            return Contract(
                title="Test",
                endpoints=[
                    {
                        "method": "GET",
                        "path": "/api/ping",
                        "summary": "Ping",
                        "request_schema": None,
                        "response_schema": {"type": "object"},
                        "success_status": 200,
                    }
                ],
                data_models={},
                frontend_orders=["Build ping UI"],
                backend_orders=["Build ping API"],
                acceptance_tests=["Ping succeeds"],
            )
        if schema is Review:
            return Review(issues=[], lessons=["Validate incoming request data."])
        names = (
            ["app/layout.tsx", "app/page.tsx", "package.json", "tsconfig.json"]
            if role == "frontend"
            else ["main.py", "requirements.txt", "tests/test_api.py"]
        )
        return CodeBundle(
            files=[
                SourceFile(path=n, content="{}" if n.endswith("json") else "# harmless source\n")
                for n in names
            ]
        )


class FakeResearch:
    async def search(self, idea):
        return [{"url": "https://example.org", "snippet": "Unverified demand evidence"}]


class FakeQA:
    def __init__(self, owner=None, fail_count=0):
        self.owner, self.fail_count, self.calls = owner, fail_count, 0

    async def check(self, *args):
        self.calls += 1
        if self.calls <= self.fail_count:
            return [
                Issue(owner=self.owner, severity="high", description="Repair contract mismatch")
            ]
        return []


@pytest.fixture
def components(tmp_path):
    cfg = Settings(
        telegram_token="fake",
        owner_user_id=1,
        owner_chat_id=1,
        openai_api_key="fake",
        data_dir=tmp_path / "data",
        vault_dir=tmp_path / "vault",
        workspace_dir=tmp_path / "workspace",
    )
    cfg.prepare()
    store = Store(cfg.data_dir / "control.sqlite")
    vault = Vault(cfg.vault_dir, cfg.workspace_dir)
    agents = FakeAgents()
    yield cfg, store, vault, agents
    store.db.close()


def make_company(parts, qa=None):
    cfg, store, vault, agents = parts
    return Company(cfg, vault, agents, FakeResearch(), qa or FakeQA(), store)


async def test_persistent_approval_restart_and_revision(components):
    cfg, store, vault, agents = components
    project = store.enqueue("Build a gym membership enquiry product", 20)
    path = str(cfg.data_dir / "checkpoints.sqlite")
    async with AsyncSqliteSaver.from_conn_string(path) as saver:
        controller = Controller(cfg, store, make_company(components).compile(saver), vault)
        await controller.work_once(store.jobs()[0])
        assert store.jobs()[0]["status"] == "waiting"
        assert agents.calls == ["scout", "pm"]
        approval = store.pending(project, 1)
        assert len(store.deliveries()) == 1
        assert store.decide(approval["token"], "revise", "Add an enquiry form")
        assert not store.decide(approval["token"], "approve")
    # Close/reopen checkpointer: resume from disk, not process memory.
    async with AsyncSqliteSaver.from_conn_string(path) as saver:
        controller = Controller(cfg, store, make_company(components).compile(saver), vault)
        await controller.work_once(store.jobs()[0])
        assert store.jobs()[0]["status"] == "waiting"
        second = store.pending(project, 2)
        assert second["token"] != approval["token"]
        assert not store.decide(approval["token"], "approve")
        assert store.decide(second["token"], "approve")
        await controller.work_once(store.jobs()[0])
        assert store.jobs()[0]["status"] == "completed"
        assert agents.calls == ["scout", "pm", "pm", "architect", "frontend", "backend", "qa"]
        assert vault.read("qa", "Company_Brain.md").count(f"<!-- learned:{project} -->") == 1
        await controller.work_once(store.jobs()[0])  # terminal reconciliation must be idempotent
        assert vault.read("qa", "Company_Brain.md").count(f"<!-- learned:{project} -->") == 1


@pytest.mark.parametrize("owner", ["frontend", "backend", "both"])
async def test_responsible_repair_and_ceiling(components, owner):
    cfg, store, vault, agents = components
    qa = FakeQA(owner, fail_count=99)
    company = make_company(components, qa)
    project = store.enqueue("Create a small software product", 20)
    async with AsyncSqliteSaver.from_conn_string(str(cfg.data_dir / "cp.sqlite")) as saver:
        graph = company.compile(saver)
        conf = graph_config(project)
        state = await graph.ainvoke(
            {"project_id": project, "idea": "Create a small software product"}, conf
        )
        assert "__interrupt__" in state
        result = await graph.ainvoke(Command(resume={"action": "approve", "revision": 1}), conf)
        assert result["status"] == "failed"
        assert qa.calls == 4  # initial attempt plus three repairs
        for role in ("frontend", "backend"):
            expected = 4 if owner in {role, "both"} else 1
            assert agents.calls.count(role) == expected
        assert "learned:" not in vault.read("qa", "Company_Brain.md")


async def test_reject_never_codes(components):
    cfg, store, vault, agents = components
    project = store.enqueue("Create a small software product", 20)
    async with AsyncSqliteSaver.from_conn_string(str(cfg.data_dir / "cp.sqlite")) as saver:
        controller = Controller(cfg, store, make_company(components).compile(saver), vault)
        await controller.work_once(store.jobs()[0])
        assert store.decide(store.pending(project, 1)["token"], "reject")
        await controller.work_once(store.jobs()[0])
        assert store.jobs()[0]["status"] == "rejected"
        assert "frontend" not in agents.calls


async def test_revision_cap(components):
    cfg, store, vault, agents = components
    cfg.max_revisions = 1
    project = store.enqueue("Create a small software product", 20)
    async with AsyncSqliteSaver.from_conn_string(str(cfg.data_dir / "cp.sqlite")) as saver:
        controller = Controller(cfg, store, make_company(components).compile(saver), vault)
        await controller.work_once(store.jobs()[0])
        store.decide(store.pending(project, 1)["token"], "revise", "Change scope")
        with pytest.raises(ValueError, match="revision ceiling"):
            await controller.work_once(store.jobs()[0])


async def test_decision_crash_window_after_resume(components):
    cfg, store, vault, agents = components
    project = store.enqueue("Create a small software product", 20)
    async with AsyncSqliteSaver.from_conn_string(str(cfg.data_dir / "cp.sqlite")) as saver:
        controller = Controller(cfg, store, make_company(components).compile(saver), vault)
        await controller.work_once(store.jobs()[0])
        row = store.pending(project, 1)
        store.decide(row["token"], "approve")
        # Simulate process death after checkpoint commit but before control-plane reconciliation.
        await controller.graph.ainvoke(
            Command(resume=json.loads(store.pending(project, 1)["decision"])),
            graph_config(project),
            durability="sync",
        )
        await controller.work_once(store.jobs()[0])
        assert store.jobs()[0]["status"] == "completed"
        assert agents.calls.count("frontend") == 1


async def test_notification_retry_does_not_resume(components):
    cfg, store, vault, agents = components
    store.enqueue("Create a small software product", 20)
    async with AsyncSqliteSaver.from_conn_string(str(cfg.data_dir / "cp.sqlite")) as saver:
        controller = Controller(cfg, store, make_company(components).compile(saver), vault)
        await controller.work_once(store.jobs()[0])
        bot = SimpleNamespace(
            send_document=AsyncMock(side_effect=RuntimeError("offline")), send_message=AsyncMock()
        )
        await controller.deliver_once(bot)
        assert store.jobs()[0]["status"] == "waiting"
        assert agents.calls == ["scout", "pm"]
        row = store.db.execute("SELECT * FROM outbox").fetchone()
        assert row["sent"] == 0 and row["attempts"] == 1


@pytest.mark.parametrize("relative", ["../outside.py", "/etc/passwd", "x/../../bad", "x\\bad"])
def test_path_escape_denied(tmp_path, relative):
    with pytest.raises(PermissionError):
        safe_path(tmp_path, relative)


def test_scoped_permissions_symlinks_and_budget(components, tmp_path):
    cfg, store, vault, agents = components
    with pytest.raises(PermissionError):
        vault.write("scout", "Company_Brain.md", "overwrite")
    with pytest.raises(PermissionError):
        vault.read("frontend", "01_Research/anything.md")
    if os.name == "nt":
        pytest.skip("Creating symlinks requires SeCreateSymbolicLinkPrivilege on Windows")
    (vault.root / "01_Research" / "escape.md").symlink_to(tmp_path / "outside")
    with pytest.raises(PermissionError):
        vault.write("scout", "01_Research/escape.md", "bad")
    bundle = CodeBundle(files=[SourceFile(path="../backend/main.py", content="bad")])
    with pytest.raises(PermissionError):
        vault.write_code("a" * 16, "frontend", bundle)
    assert store.reserve_call("key", "p", "pm", 80, 100) is None
    with pytest.raises(ValueError, match="budget"):
        store.reserve_call("next", "p", "qa", 30, 100)
    store.cache_call("key", {"markdown": "cached"})
    assert store.reserve_call("key", "p", "pm", 80, 100) == {"markdown": "cached"}


@pytest.mark.parametrize(
    "user,chat,kind,expected",
    [
        (1, 1, "private", True),
        (2, 1, "private", False),
        (1, 2, "private", False),
        (1, 1, "group", False),
    ],
)
def test_telegram_authorization(components, user, chat, kind, expected):
    cfg = components[0]
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=user), effective_chat=SimpleNamespace(id=chat, type=kind)
    )
    assert authorized(update, cfg) is expected


async def test_modified_prd_cannot_be_approved(components):
    cfg, store, vault, agents = components
    project = store.enqueue("Create a small software product", 20)
    async with AsyncSqliteSaver.from_conn_string(str(cfg.data_dir / "cp.sqlite")) as saver:
        company = make_company(components)
        controller = Controller(cfg, store, company.compile(saver), vault)
        await controller.work_once(store.jobs()[0])
        approval = store.pending(project, 1)
        payload = json.loads(approval["payload"])
        vault.write("pm", payload["prd_path"], "Edited after approval prompt")
        store.decide(approval["token"], "approve")
        with pytest.raises(ValueError, match="changed"):
            await controller.work_once(store.jobs()[0])
        assert "frontend" not in agents.calls


def test_generated_sources_readable_by_sandbox(components):
    vault = components[2]
    bundle = CodeBundle(files=[SourceFile(path="app/page.tsx", content="hello")])
    vault.write_code("a" * 16, "frontend", bundle)
    root = vault.workspace / ("a" * 16)
    if os.name != "nt":
        assert root.stat().st_mode & 0o777 == 0o755
        assert (root / "frontend" / "app").stat().st_mode & 0o777 == 0o755
        assert (root / "frontend" / "app/page.tsx").stat().st_mode & 0o777 == 0o644


def test_client_is_typed_and_path_encoded():
    from company.client import render_client

    source = render_client(
        {
            "endpoints": [
                {
                    "method": "POST",
                    "path": "/api/items/{id}",
                    "request_schema": {
                        "type": "object",
                        "properties": {"title": {"type": "string"}},
                        "required": ["title"],
                    },
                    "response_schema": {
                        "type": "object",
                        "properties": {"id": {"type": "integer"}},
                        "required": ["id"],
                    },
                    "success_status": 201,
                }
            ]
        }
    )
    assert '"POST /api/items/{id}"' in source
    assert '"title": string' in source
    assert '"id": number' in source
    assert "encodeURIComponent" in source
    assert "response.status !== route.status" in source


async def test_sandbox_flags_and_infrastructure_failure(components, monkeypatch):
    from company.qa import SandboxQA

    cfg, store, vault, agents = components
    project = "a" * 16
    (vault.workspace / project).mkdir()
    captured = []

    class Process:
        returncode = 0

        async def communicate(self):
            return b'{"issues": []}', b""

    async def spawn(*args, **kwargs):
        captured.extend(args)
        return Process()

    monkeypatch.setattr("company.qa.asyncio.create_subprocess_exec", spawn)
    assert await SandboxQA(cfg).check(project, {}, vault.workspace) == []
    assert "--network=none" in captured
    assert "--read-only" in captured
    assert "--user=65534:65534" in captured
    assert "--cap-drop=ALL" in captured
    assert not any("docker.sock" in str(x) for x in captured)
    assert any("readonly" in str(x) for x in captured)


async def test_sandbox_timeout_cleans_container(components, monkeypatch):
    from company.qa import SandboxQA

    cfg, store, vault, agents = components
    project = "b" * 16
    (vault.workspace / project).mkdir()
    spawned = []

    class Process:
        returncode = None

        def kill(self):
            self.returncode = -9

        async def communicate(self):
            raise TimeoutError

        async def wait(self):
            return 0

    async def spawn(*args, **kwargs):
        spawned.append(args)
        return Process()

    monkeypatch.setattr("company.qa.asyncio.create_subprocess_exec", spawn)
    with pytest.raises(TimeoutError):
        await SandboxQA(cfg).check(project, {}, vault.workspace)
    assert spawned[1][:3] == ("docker", "rm", "-f")
