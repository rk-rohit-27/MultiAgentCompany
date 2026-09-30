import asyncio
import json
import re
import secrets
from pathlib import Path

from .schemas import Issue


class SandboxQA:
    """All generated imports, tests and TypeScript builds execute in an isolated container."""

    def __init__(self, settings):
        self.cfg = settings
        self.runner = Path(__file__).resolve().parent / "sandbox_runner.py"

    async def check(self, project_id, contract, workspace):
        root = workspace.resolve() / project_id
        if any(p.is_symlink() for p in root.rglob("*")):
            return [Issue(owner="both", severity="critical", description="Symlink in source tree")]
        name = "company-qa-" + secrets.token_hex(8)
        args = [
            "docker",
            "run",
            "--rm",
            "--name",
            name,
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--pids-limit=128",
            "--memory=1g",
            "--cpus=1",
            "--user=65534:65534",
            "--tmpfs=/tmp:rw,nosuid,nodev,size=512m,mode=1777",
            "--mount",
            f"type=bind,src={root},dst=/input,readonly",
            "--mount",
            f"type=bind,src={self.runner},dst=/runner.py,readonly",
            self.cfg.sandbox_image,
            "python",
            "/runner.py",
        ]
        # CLI and image are operator-controlled. No shell, host env or docker socket in sandbox.
        process = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        try:
            out, err = await asyncio.wait_for(process.communicate(), self.cfg.sandbox_timeout)
        except (TimeoutError, asyncio.CancelledError):
            cleanup = await asyncio.create_subprocess_exec(
                "docker",
                "rm",
                "-f",
                name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await cleanup.wait()
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        if process.returncode:
            raise RuntimeError("QA infrastructure failed: " + err.decode(errors="replace")[-1500:])
        if len(out) > 200000:
            raise ValueError("QA output too large")
        return [Issue.model_validate(i) for i in json.loads(out)["issues"]]


def static_security(files):
    issues = []
    patterns = [
        r"(?i)-----BEGIN .*PRIVATE KEY",
        r"\bsk-[A-Za-z0-9]{20,}",
        r"(?i)(?:eval|exec)\s*\(",
        r"(?i)shell\s*=\s*True",
        r"(?i)dangerouslySetInnerHTML",
        r"(?i)verify\s*=\s*False",
    ]
    for role, sources in files.items():
        for name, content in sources.items():
            if (
                role == "frontend"
                and name != "lib/api.ts"
                and re.search(r"\b(fetch|XMLHttpRequest|axios|WebSocket)\b", content)
            ):
                issues.append(
                    Issue(
                        owner="frontend",
                        severity="high",
                        description=f"{name}: backend calls must use the generated API client",
                    )
                )
            for pattern in patterns:
                if re.search(pattern, content):
                    issues.append(
                        Issue(
                            owner=role,
                            severity="high",
                            description=f"{name}: unsafe pattern {pattern}",
                        )
                    )
    return issues
