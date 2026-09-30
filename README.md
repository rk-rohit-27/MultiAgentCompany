# Obsidian Agent Company

A runnable Python software-company workflow: research → PRD → Telegram human approval → architecture → frontend → backend → QA → up to three repairs per developer → completion or failure.

**Deployment target:** one Linux/Debian host, one service process, persistent local disks. It is suitable for a controlled pilot after configuring your services and validating the QA image. It is not a tested multi-host platform, nor a guarantee that generated software is production-ready.

## What is implemented

- Independent LangGraph employee nodes with separate model instances, output ceilings and scoped artifact permissions.
- Native `interrupt()` and `Command(resume=...)` with durable SQLite checkpoints and stable project/thread IDs.
- Persistent job queue, revision-bound random approval tokens, atomic decisions, stale-button protection and crash reconciliation.
- Telegram private-chat/user authorization, full frozen PRD attachments, APPROVE / REVISE / REJECT buttons and feedback commands.
- Durable notification outbox, retries with backoff and no model execution in Telegram handlers.
- Research through your SearXNG JSON API. Search snippets retain source URLs and are explicitly labeled as unverified page evidence.
- Exact requested Obsidian folder structure, employee wikilinks, project work orders, QA history, a dashboard refreshed every two seconds, and deduplicated company learnings.
- Next.js/React TypeScript frontend and FastAPI/Pydantic backend. A deterministic typed API client binds frontend requests to the architect's frozen contract.
- QA: Python syntax, selected Ruff correctness checks, backend pytest suite, backend OpenAPI/contract comparison, frontend TypeScript compilation, static security checks and semantic LLM review.
- No generated code executes on the controller host. Generated imports/tests/type checking run inside a resource-limited, offline Docker container.
- Three repair attempts per implicated developer. Mixed ownership repairs both; passing QA is required to mark a project completed.
- Input/output token ceilings, conservative persistent project token reservations, validated structured outputs and cached successful model responses.

## Run on a local VPS

The supported deployment target is a single Debian or Ubuntu VPS with Docker Engine, Python 3.12+, persistent local storage, and outbound HTTPS access. The service uses Telegram polling, so you do not need to expose an inbound HTTP port or configure a public domain.

Keep the VPS private behind its provider firewall. Allow SSH only for administration. The controller account needs access to Docker, which is equivalent to powerful host access; use a dedicated VPS and a dedicated `agentcompany` account.

### Install system packages

```bash
sudo apt update
sudo apt install -y python3.12 python3.12-venv python3-pip docker.io git
sudo systemctl enable --now docker
sudo useradd --system --create-home --home-dir /opt/agent-company --shell /usr/sbin/nologin agentcompany || true
sudo usermod -aG docker agentcompany
sudo mkdir -p /opt/agent-company
sudo chown -R "$USER":"$USER" /opt/agent-company
```

Copy this project into `/opt/agent-company`, then install its Python environment:

```bash
cd /opt/agent-company
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.lock
pip install --no-deps -e .
```

Create the environment file from the redacted template and edit it with real values:

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

Set `DATA_DIR`, `VAULT_DIR`, and `WORKSPACE_DIR` to folders under `/opt/agent-company` unless you have a deliberate backup or storage layout. Use newly rotated Telegram and provider credentials. Never commit `.env` or paste it into support logs.

When SearXNG runs directly on the VPS host while the controller runs in Compose, set `SEARXNG_URL=http://host.docker.internal:8080` in `.env`. If SearXNG runs in another Compose service, use that service name and port instead.

For Portainer deployments from GitHub, do not add `.env` to the repository. Portainer does not receive ignored local files from GitHub. Add `TELEGRAM_TOKEN`, `OWNER_USER_ID`, `OWNER_CHAT_ID`, and `OPENAI_API_KEY` in the stack's **Environment variables** section. The Compose file reads those values during deployment and supplies safe defaults for the remaining settings. Keep the token and API key marked as secret values in Portainer.

Initialize the vault and build the fixed QA image:

```bash
. .venv/bin/activate
python -m company.init
docker build -t company-qa:1 -f sandbox/Dockerfile .
```

Install the supplied systemd unit:

```bash
sudo cp company.service /etc/systemd/system/agent-company.service
sudo systemctl daemon-reload
sudo systemctl enable --now agent-company.service
sudo systemctl status agent-company.service
```

Read service logs with:

```bash
sudo journalctl -u agent-company.service -f
```

The service resumes queued work and pending approvals after a restart. Back up `data/`, `vault/`, `workspace/`, `requirements.lock`, the source tree, and the QA image version. Back up `.env` separately through a secret-management process.

To update the VPS, stop the service, copy the new source, install locked dependencies, rebuild the QA image, and restart:

```bash
sudo systemctl stop agent-company.service
cd /opt/agent-company
. .venv/bin/activate
pip install -r requirements.lock
pip install --no-deps -e .
docker build -t company-qa:1 -f sandbox/Dockerfile .
sudo systemctl start agent-company.service
```

Before accepting real work, run the local checks and create one small pilot project:

```bash
. .venv/bin/activate
pytest -q
ruff check company tests
python -m compileall -q company
```

The service does not publish generated applications. Review the generated workspace and QA report before deploying any generated project separately.

### Run with Docker Compose

Docker Compose runs the controller and keeps `data/`, `vault/`, and `workspace/` on the VPS. The controller needs the Docker socket because it starts the isolated `company-qa:1` container for generated-project validation. Treat access to `/var/run/docker.sock` as host-level administrative access and use a dedicated VPS.

Create `.env`, initialize the vault, and build the QA image first:

```bash
cp .env.example .env
chmod 600 .env
nano .env
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements.lock
python -m company.init
docker build -t company-qa:1 -f sandbox/Dockerfile .
```

Build and start the controller:

```bash
docker compose build
docker compose up -d
docker compose ps
docker compose logs -f agent-company
```

Stop or restart it with:

```bash
docker compose stop
docker compose restart
```

The Compose file uses the host Docker daemon for QA. It must run on a Linux VPS with Docker Engine and the `company-qa:1` image already built. Do not expose the Compose service directly to the public internet; Telegram polling provides the bot connection.

The dashboard is available at `http://VPS_IP:8080`. Set a long random `ADMIN_TOKEN` in Portainer and enter it in the dashboard login field. Put the dashboard behind the VPS firewall or a reverse proxy with HTTPS before exposing it publicly.

## Start on your Debian machine

Use Python 3.12+, Docker Engine and an OpenAI API key. OpenAI-compatible providers must support tool calling and the configured tokenizer/model; they have not been integration-tested here.

```bash
cd autonomous-company
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.lock
pip install --no-deps -e .
chmod 600 .env
python -m company.init
docker build -t company-qa:1 -f sandbox/Dockerfile .
```

The lock file contains the exact Python versions used by the tests. The sandbox's direct packages are pinned; its base images and transitive npm dependencies are not digest-locked. For a managed production rollout, build once, audit dependencies, then use an immutable registry image digest in `SANDBOX_IMAGE`.

Edit `.env`:

| Variable | Purpose |
| --- | --- |
| `TELEGRAM_TOKEN` | Bot token from BotFather |
| `OWNER_USER_ID` | Your numeric Telegram user ID |
| `OWNER_CHAT_ID` | Your private chat ID with the bot; normally the same as your user ID |
| `OPENAI_API_KEY` | Provider API key; a ChatGPT subscription does not supply API credits |
| `MODEL` | A tool-calling model with enough context and output capacity; default `gpt-4.1-mini` |
| `SEARXNG_URL` | Your SearXNG base URL; enable `json` in `search.formats` |
| `DATA_DIR` | Persistent queue, token ledger, outbox and checkpoint storage |
| `VAULT_DIR` | Folder to open as an Obsidian vault |
| `WORKSPACE_DIR` | Generated projects, each under its unique 16-character ID |
| `SANDBOX_IMAGE` | Operator-built QA container; never model-selected |
| `MAX_RETRIES` | Developer repairs; cannot exceed 3 |
| `CONTEXT_TOKENS` | Maximum input tokens for any individual employee call |
| `RUN_TOKEN_BUDGET` | Conservative sum of reserved input/output tokens per project |

The example uses a 48,000-token input ceiling and a 300,000-token project reservation budget. These are limits, not predicted consumption. Lower them for inexpensive pilots; exceeding a limit stops the project instead of silently truncating its requirements. Output ceilings are 3,000 Scout, 5,000 PM, 5,000 Architect, 14,000 per developer and 4,000 QA tokens. A model with a lower maximum output length needs corresponding changes to `company/agents.py`.

To find IDs without another third-party bot, send `/start` to your own bot while this service is stopped, then run the following locally. It prints IDs only; it does not print your token or send a message:

```bash
python - <<'PY'
import os, httpx
from dotenv import load_dotenv
load_dotenv('.env')
result = httpx.get(
    'https://api.telegram.org/bot' + os.environ['TELEGRAM_TOKEN'] + '/getUpdates',
    timeout=20).json()
for item in result.get('result', []):
    message = item.get('message', {})
    if message.get('chat', {}).get('type') == 'private':
        print('OWNER_USER_ID:', message.get('from', {}).get('id'))
        print('OWNER_CHAT_ID:', message['chat']['id'])
PY
```

Open `vault/` in Obsidian, then start the service:

```bash
agent-company
```

Send commands in your authorized private chat:

```text
/new Research a small membership enquiry product for independent gyms in Jammu
/status
/revise <project_id> Keep WhatsApp enquiries; remove payments from the MVP
/retry <project_id>
```

`/new` queues work. At the approval checkpoint, the bot attaches the entire PRD and shows the three buttons. APPROVE starts implementation; REJECT ends that project; REVISE asks for feedback. `/revise` accepts either a project ID or the button's approval token. No response means execution remains paused indefinitely.

`/retry` is for an infrastructure/model/configuration error, using the last checkpoint. It does not reset exhausted QA repair counts, increase a token budget or bypass approvals. Correct the underlying configuration first. Errors deliberately show exception classes rather than arbitrary provider error text, which may contain credentials.

Editing the PRD in Obsidian does not silently change its approved meaning. The checkpoint stores its exact text and hash; Telegram attaches this frozen snapshot. Approval of a changed vault file is refused. Use REVISE with the requested changes, or restore the original note before approving. During implementation, PRD edits stop the next employee that reads it. To replace a scope after implementation has begun, create a new project; this implementation does not support mid-build scope migration.

## Employee and filesystem permissions

| Employee | Reads | Writes |
| --- | --- | --- |
| Scout | Company Brain, research | Research notes |
| PM | Company Brain, research, specs | PRD notes |
| Architect | Company Brain, specs, architecture | API contract, work orders |
| FrontendDev | Company Brain, specs, architecture, quality; its own code | Its project frontend source tree |
| BackendDev | Company Brain, specs, architecture, quality; its own code | Its project backend source tree |
| QAEngineer | Company Brain, all project notes and both source trees | Quality notes, company learnings |
| Controller | Queue/checkpoints/control metadata | Dashboard, immutable client/contract copies, notifications |

Employees receive task-scoped contexts, not unrestricted shells or generic filesystem tools. Permissions are enforced by the application; the employee nodes do not run as distinct operating-system accounts. Local humans and other processes that own these folders remain trusted.

Developer output is a complete source snapshot. Obsolete files are removed during a repair. The frontend cannot replace the generated `lib/api.ts`; it must import its typed `api()` function. Request method, operation key, path parameters, JSON body types, response types and expected success status come from the same contract as backend QA. Direct `fetch`, XMLHttpRequest, Axios or WebSocket usage outside that client is flagged. This is not a formal proof against deliberately obfuscated code.

Contracts use inline JSON schemas, JSON bodies and path parameters. Query-string parameters, streaming responses, file uploads, additional npm/Python libraries and external-service integration tests are outside the current MVP contract vocabulary. Extend the schema/client/QA image together when your product requires them.

## Obsidian layout

```text
vault/
├── 00_Dashboard/🔴_LIVE_ACTIVITY.md
├── 01_Research/
├── 02_Specs/
├── 03_Architecture/
│   ├── api_contract.json
│   └── <project_id>/api_contract.json + Work_Orders.md
├── 04_Quality/
│   ├── bug_report.md
│   └── <project_id>/bug_report.md
├── 05_Employees/
│   ├── Scout.md
│   ├── PM.md
│   ├── Architect.md
│   ├── FrontendDev.md
│   ├── BackendDev.md
│   └── QAEngineer.md
└── Company_Brain.md
```

Research/PRD names include the idea slug and unique project ID. Project-specific contracts and QA reports prevent projects from overwriting one another. The exact requested `03_Architecture/api_contract.json` and `04_Quality/bug_report.md` are latest-project convenience files; agent execution uses the scoped versions. Wikilinks connect research → PRD → work orders → QA → assigned employees → company memory.

## Persistence and recovery

A filesystem lock permits only one controller per data directory. SQLite WAL with full synchronization stores control state; LangGraph uses `AsyncSqliteSaver` and synchronous checkpoint durability. Run on a local filesystem, not NFS and not a shared multi-host volume.

Telegram callbacks only record decisions in the control database. The worker loads the committed LangGraph checkpoint, resumes the exact pending interrupt by its ID and reconciles results. A crash after a graph commit but before job-status update is recovered by inspecting the checkpoint, without rerunning the completed project.

The approval node has no side effects before `interrupt()`. Notifications are outbox records created after the interrupt checkpoint exists. Job/revision uniqueness prevents duplicate logical approval records. Telegram delivery is **at least once**: a crash after sending but before recording delivery can duplicate the attachment or message. Buttons remain single-use; Telegram does not provide an atomic transaction with this database.

Atomic file replacement with fsync protects individual notes/source files. A whole source bundle and LangGraph checkpoint are not one transaction; a crash during a source write is repaired by replaying the node and rewriting the complete cached bundle. Successful model outputs are cached by project, role, schema, prompt, input and model. Unknown outcomes may be re-requested after a crash, and their conservative token reservations remain charged.

Stop the service before backups. Back up `data/`, `vault/`, `workspace/`, source/lock files and the QA image digest together; handle `.env` separately as a secret. Restarting the service resumes queued/running projects and preserves pending approvals. Do not delete checkpoint databases while retaining approval records.

## QA execution and trust boundaries

`SandboxQA` invokes Docker without a shell and uses an operator-selected image. The generated project and trusted runner are read-only bind mounts. Containers run as UID/GID 65534 with no network, no capabilities, no new privileges, a read-only root, a bounded temporary filesystem, 1 CPU, 1 GiB RAM, 128 processes and a timeout. Timeout/shutdown explicitly removes the container. It receives no API keys, controller environment, vault, host home directory or Docker socket.

The controller user needs Docker access. On a standard Docker daemon this is powerful host access, so run the controller under a dedicated trusted account and keep the generated-code container separate. Docker containers share the host kernel; use a dedicated VM or gVisor-class runtime for hostile workloads or multiple untrusted tenants.

Generated sources are readable by the container; they must never contain secrets. Vault/control files use restricted file permissions. No generated dependency scripts execute on the host or during QA. The QA image preinstalls allowed libraries, then directly runs the trusted compiler/test commands. Model-selected requirements and package scripts are not executed.

The current checks are syntax/type/contract/test/security checks. They do **not** include a Next.js production build, browser E2E accessibility/performance testing, independent coverage measurement, a full SAST audit, or a dependency vulnerability audit. Generated backend tests and semantic LLM review cannot prove security or business correctness. `completed` means this configured QA suite passed; review artifacts before release. Deployment/publishing is deliberately a separate operation with no implementation in this service.

## Run as a service

`company.service` is a systemd template for `/opt/agent-company` and a dedicated `agentcompany` user. Create the account/directories, install the project and Docker image, set file ownership, edit `.env`, and grant that account Docker access before installing the unit:

```bash
sudo cp company.service /etc/systemd/system/agent-company.service
sudo systemctl daemon-reload
sudo systemctl enable --now agent-company.service
sudo journalctl -u agent-company.service -f
```

Use the same paths in `.env` and the service's `ReadWritePaths`. The unit assumes a working Docker service and local persistent folders. Download the tokenizer cache during setup or allow outbound access on its first model call. Polling requires outbound Telegram connectivity and no public webhook port. Only one process may poll this bot token.

## Verification

```bash
pytest -q
ruff check company tests
python -m compileall -q company
```

The delivered controller tests cover persistent restart/revision, rejection, responsible developer routing, repair ceilings, committed-checkpoint crash recovery, notification retry, stale/double decisions, private-chat authorization, path traversal/symlink denial, token reservation/caching, PRD tampering, source permissions, typed-client generation, sandbox command restrictions and container cleanup on timeout.

Real model calls, real Telegram delivery and Docker execution need your configured environment. Docker was unavailable where this archive was produced, so the image and generated-project QA were not executed here. Before relying on it, build the image successfully, create a small pilot project, revise and approve its PRD, observe actual QA results and restart with an approval pending.

## API references used

- LangGraph interrupt semantics: https://docs.langchain.com/oss/python/langgraph/interrupts
- LangGraph persistence: https://docs.langchain.com/oss/python/langgraph/persistence
- Python Telegram Bot application lifecycle: https://docs.python-telegram-bot.org/en/stable/telegram.ext.application.html

See `VALIDATION.md` for the exact validation performed on this deliverable.
