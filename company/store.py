"""One-process SQLite control plane. Never share these DBs across hosts/NFS."""

import json
import secrets
import sqlite3
import time
from contextlib import contextmanager


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY, idea TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
          error TEXT, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS approvals (
          token TEXT PRIMARY KEY, project TEXT NOT NULL, revision INTEGER NOT NULL,
          payload TEXT NOT NULL, decision TEXT, consumed INTEGER NOT NULL DEFAULT 0,
          UNIQUE(project,revision));
        CREATE TABLE IF NOT EXISTS outbox (
          key TEXT PRIMARY KEY, payload TEXT NOT NULL, sent INTEGER NOT NULL DEFAULT 0,
          attempts INTEGER NOT NULL DEFAULT 0, next_try REAL NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS calls (
          key TEXT PRIMARY KEY, project TEXT NOT NULL, role TEXT NOT NULL,
          tokens INTEGER NOT NULL, result TEXT);
        """)

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def enqueue(self, idea, limit):
        project = secrets.token_hex(8)
        with self.transaction():
            count = self.db.execute(
                "SELECT count(*) FROM jobs WHERE status IN ('queued','running','waiting')"
            ).fetchone()[0]
            if count >= limit:
                raise ValueError("Queue capacity reached")
            self.db.execute(
                "INSERT INTO jobs(id,idea,created) VALUES (?,?,?)", (project, idea, time.time())
            )
        return project

    def jobs(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM jobs ORDER BY created")]

    def status(self, project, status, error=None):
        self.db.execute("UPDATE jobs SET status=?,error=? WHERE id=?", (status, error, project))

    def approval(self, project, revision, payload):
        with self.transaction():
            row = self.db.execute(
                "SELECT * FROM approvals WHERE project=? AND revision=?", (project, revision)
            ).fetchone()
            if row:
                return dict(row)
            token = secrets.token_hex(12)
            self.db.execute(
                "INSERT INTO approvals(token,project,revision,payload) VALUES(?,?,?,?)",
                (token, project, revision, json.dumps(payload)),
            )
            self.outbox(
                f"approval:{project}:{revision}",
                {
                    "kind": "approval",
                    "token": token,
                    "project": project,
                    "revision": revision,
                    **payload,
                },
            )
        return dict(self.db.execute("SELECT * FROM approvals WHERE token=?", (token,)).fetchone())

    def decide(self, token, action, feedback=""):
        if action not in {"approve", "revise", "reject"}:
            raise ValueError("Invalid decision")
        with self.transaction():
            row = self.db.execute("SELECT * FROM approvals WHERE token=?", (token,)).fetchone()
            if not row or row["decision"] or row["consumed"]:
                return False
            job = self.db.execute(
                "SELECT status FROM jobs WHERE id=?", (row["project"],)
            ).fetchone()
            if not job or job["status"] != "waiting":
                return False
            latest = self.db.execute(
                "SELECT max(revision) FROM approvals WHERE project=?", (row["project"],)
            ).fetchone()[0]
            if latest != row["revision"]:
                return False
            decision = {"action": action, "revision": row["revision"], "feedback": feedback}
            self.db.execute(
                "UPDATE approvals SET decision=? WHERE token=?", (json.dumps(decision), token)
            )
            # Prevent delivering queued obsolete controls after a decision.
            self.db.execute(
                "UPDATE outbox SET sent=1 WHERE key=?",
                (f"approval:{row['project']}:{row['revision']}",),
            )
        return True

    def pending(self, project, revision):
        row = self.db.execute(
            "SELECT * FROM approvals WHERE project=? AND revision=?", (project, revision)
        ).fetchone()
        return dict(row) if row else None

    def consume(self, token):
        self.db.execute("UPDATE approvals SET consumed=1 WHERE token=?", (token,))

    def outbox(self, key, payload):
        self.db.execute(
            "INSERT OR IGNORE INTO outbox(key,payload) VALUES(?,?)", (key, json.dumps(payload))
        )

    def deliveries(self):
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT * FROM outbox WHERE sent=0 AND next_try<=? ORDER BY rowid LIMIT 10",
                (time.time(),),
            )
        ]

    def delivered(self, key):
        self.db.execute("UPDATE outbox SET sent=1 WHERE key=?", (key,))

    def delivery_failed(self, key, attempts):
        self.db.execute(
            "UPDATE outbox SET attempts=attempts+1,next_try=? WHERE key=?",
            (time.time() + min(300, 2 ** min(attempts + 1, 8)), key),
        )

    def reserve_call(self, key, project, role, tokens, budget):
        with self.transaction():
            row = self.db.execute("SELECT * FROM calls WHERE key=?", (key,)).fetchone()
            if row and row["result"]:
                return json.loads(row["result"])
            # Previous uncertain call remains charged; retry also reserves its worst-case cost.
            total = self.db.execute(
                "SELECT coalesce(sum(tokens),0) FROM calls WHERE project=?", (project,)
            ).fetchone()[0]
            if total + tokens > budget:
                raise ValueError("Project token budget exhausted")
            if row:
                self.db.execute("UPDATE calls SET tokens=tokens+? WHERE key=?", (tokens, key))
            else:
                self.db.execute(
                    "INSERT INTO calls(key,project,role,tokens) VALUES(?,?,?,?)",
                    (key, project, role, tokens),
                )
        return None

    def cache_call(self, key, value):
        self.db.execute("UPDATE calls SET result=? WHERE key=?", (json.dumps(value), key))
