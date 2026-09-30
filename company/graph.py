import json

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .client import render_client
from .qa import static_security
from .schemas import Brief, CodeBundle, Contract, Decision, Review, SourceFile, State
from .vault import project_slug, safe_path


class Company:
    def __init__(self, settings, vault, agents, research, qa, store):
        self.cfg, self.vault, self.agents = settings, vault, agents
        self.research, self.qa, self.store = research, qa, store

    def paths(self, state):
        slug = project_slug(state["idea"], state["project_id"])
        return {
            "research": f"01_Research/{slug}.md",
            "prd": f"02_Specs/{slug}_PRD.md",
            "contract": f"03_Architecture/{state['project_id']}/api_contract.json",
            "orders": f"03_Architecture/{state['project_id']}/Work_Orders.md",
            "quality": f"04_Quality/{state['project_id']}/bug_report.md",
        }

    def activity(self, state, role, text):
        self.vault.activity(state["project_id"], role, text, self.store.jobs())

    def memory(self, role):
        return self.vault.read(role, "Company_Brain.md")

    async def scout_node(self, state):
        self.activity(state, "scout", "Researching market evidence")
        sources = await self.research.search(state["idea"])
        result = await self.agents.generate(
            "scout",
            state["project_id"],
            Brief,
            "Write a research brief with target niche, complaints, competitors, opportunities, "
            "confidence, and explicit source URLs. Distinguish snippets from verified facts.",
            {"idea": state["idea"], "sources": sources, "memory": self.memory("scout")},
        )
        text = (
            result.markdown
            + "\n\n## Evidence register\n```json\n"
            + json.dumps(sources, indent=2)
            + "\n```"
        )
        self.vault.note("scout", self.paths(state)["research"], state["project_id"], text)
        return {
            "research": text,
            "revision": 0,
            "retries": {"frontend": 0, "backend": 0},
            "status": "research_complete",
        }

    async def pm_node(self, state):
        self.activity(state, "pm", "Writing PRD")
        revision = state["revision"] + 1
        if revision > self.cfg.max_revisions:
            raise ValueError("PRD revision ceiling exhausted")
        paths = self.paths(state)
        result = await self.agents.generate(
            "pm",
            state["project_id"],
            Brief,
            "Write a complete actionable PRD: scope, non-goals, user stories, acceptance criteria, "
            "data models, security, privacy, risks and success metrics. Stack is Next.js/React "
            "TypeScript frontend and FastAPI/Pydantic backend with SQLite. Keep MVP small. "
            "Apply human revision feedback; do not invent market validation.",
            {
                "research": self.vault.read("pm", paths["research"]),
                "previous_prd": state.get("prd", ""),
                "feedback": state.get("feedback", ""),
                "revision": revision,
                "memory": self.memory("pm"),
            },
        )
        self.vault.note(
            "pm",
            paths["prd"],
            state["project_id"],
            result.markdown,
            links=(paths["research"][:-3],),
        )
        return {
            "prd": result.markdown,
            "prd_note_hash": self.vault.digest(self.vault.read("pm", paths["prd"])),
            "prd_note": self.vault.read("pm", paths["prd"]),
            "revision": revision,
            "status": "approval_pending",
        }

    def human_approval_node(self, state):
        # No external effects before interrupt: this function is restarted on resume.
        payload = {
            "project": state["project_id"],
            "revision": state["revision"],
            "prd_path": self.paths(state)["prd"],
            "summary": state["prd"][:2300],
            "prd_hash": state["prd_note_hash"],
            "prd_text": state["prd_note"],
        }
        decision = Decision.model_validate(interrupt(payload))
        if decision.revision != state["revision"]:
            raise ValueError("Approval belongs to a stale PRD revision")
        if decision.action == "revise" and not decision.feedback.strip():
            raise ValueError("Revision requires feedback")
        return {
            "decision": decision.action,
            "feedback": decision.feedback,
            "status": "rejected" if decision.action == "reject" else "approved",
        }

    def approved_prd(self, state, role):
        text = self.vault.read(role, self.paths(state)["prd"])
        if self.vault.digest(text) != state["prd_note_hash"]:
            raise ValueError("PRD was edited outside the approved revision")
        return text

    async def architect_node(self, state):
        self.activity(state, "architect", "Defining API contract and work orders")
        paths = self.paths(state)
        contract = await self.agents.generate(
            "architect",
            state["project_id"],
            Contract,
            "Decompose the approved PRD. Use JSON Schema object request and response schemas, "
            "explicit status codes, method and path pairs, data models, frontend and backend "
            "work orders. Inline all request/response JSON schemas; never use $ref. "
            "Use path parameters and JSON request bodies only, no query-string parameters. "
            "work orders, and concrete acceptance tests. Keep routes under /api. "
            "Contract is frozen during developer repairs.",
            {"prd": self.approved_prd(state, "architect"), "memory": self.memory("architect")},
        )
        pairs = [(e.method, e.path) for e in contract.endpoints]
        if len(pairs) != len(set(pairs)):
            raise ValueError("Duplicate API operation")
        render_client(contract.model_dump())  # validate supported schema vocabulary before writing
        content = contract.model_dump_json(indent=2)
        self.vault.write("architect", paths["contract"], content)
        # Exact requested path is a latest-project convenience; agents use scoped copy.
        self.vault.write("architect", "03_Architecture/api_contract.json", content)
        orders = "# Work orders\n\n## [[05_Employees/FrontendDev]]\n" + "\n".join(
            f"- {x}" for x in contract.frontend_orders
        )
        orders += "\n\n## [[05_Employees/BackendDev]]\n" + "\n".join(
            f"- {x}" for x in contract.backend_orders
        )
        self.vault.note(
            "architect", paths["orders"], state["project_id"], orders, links=(paths["prd"][:-3],)
        )
        return {"contract": contract.model_dump(), "status": "implementing"}

    def sources(self, state, role):
        root = safe_path(self.vault.workspace, f"{state['project_id']}/{role}")
        files = {}
        for name in state.get(f"{role}_files", []):
            path = safe_path(root, name)
            if path.stat().st_size > 100000:
                raise ValueError("Source exceeds read allowance")
            files[name] = path.read_text()
        return files

    async def develop(self, state, role):
        self.activity(
            state, role, "Implementing" if not state.get("issues") else "Repairing QA issues"
        )
        paths = self.paths(state)
        extra = (
            "Supply app/layout.tsx, app/page.tsx, tsconfig.json and package.json. "
            "Use next@16.3.7, react@19.3.0, react-dom@19.3.0 and TypeScript@5.9.3. "
            "No additional npm dependencies. Import { api } from @/lib/api for ALL backend calls. "
            "The provided generated_client is immutable and will be installed at lib/api.ts. "
            'Call api with the exact operation key and options, e.g. api("GET /api/ping", {}). '
            "Do not write fetch calls or duplicate the client. API calls must use NEXT_PUBLIC_API_URL with "
            "a localhost fallback. No credentials or server secrets in the client."
            if role == "frontend"
            else "Supply main.py exporting app, requirements.txt and tests/test_api.py. "
            "Use installed FastAPI, Pydantic, sqlite3 and pytest only; no extra dependencies. "
            "Test every contract operation with TestClient, including invalid inputs and "
            "authorization where required. Store runtime DB under /tmp or APP_DATA_DIR. "
            "Do not perform database initialization before tests can isolate storage."
        )
        result = await self.agents.generate(
            role,
            state["project_id"],
            CodeBundle,
            "Produce the COMPLETE replacement source tree, including unchanged required files. "
            "Implement all work orders, acceptance tests, validation and security from PRD. "
            "Preserve API contract exactly; do not deploy. " + extra,
            {
                "prd": self.approved_prd(state, role),
                "contract": json.loads(self.vault.read(role, paths["contract"])),
                "generated_client": render_client(state["contract"]) if role == "frontend" else "",
                "orders": self.vault.read(role, paths["orders"]),
                "existing": self.sources(state, role),
                "issues": state.get("issues", []),
                "attempt": state["retries"][role],
                "memory": self.memory(role),
            },
        )
        if role == "frontend":
            result = CodeBundle(
                files=[f for f in result.files if f.path != "lib/api.ts"]
                + [SourceFile(path="lib/api.ts", content=render_client(state["contract"]))]
            )
        names = {file.path for file in result.files}
        required = (
            {"app/layout.tsx", "app/page.tsx", "package.json", "tsconfig.json"}
            if role == "frontend"
            else {"main.py", "requirements.txt", "tests/test_api.py"}
        )
        if not required.issubset(names):
            raise ValueError(f"{role} missing required files: {required - names}")
        files = self.vault.write_code(state["project_id"], role, result)
        return {f"{role}_files": files}

    async def frontend_node(self, state):
        return await self.develop(state, "frontend")

    async def backend_node(self, state):
        return await self.develop(state, "backend")

    async def qa_node(self, state):
        self.activity(state, "qa", "Checking contract, tests, types and security")
        sources = {role: self.sources(state, role) for role in ("frontend", "backend")}
        # Sandbox uses immutable contract copy outside both developers' permissions.
        from .vault import atomic_write

        contract_path = safe_path(self.vault.workspace, f"{state['project_id']}/contract.json")
        atomic_write(contract_path, json.dumps(state["contract"]), mode=0o644)
        issues = static_security(sources)
        issues += await self.qa.check(state["project_id"], state["contract"], self.vault.workspace)
        review = await self.agents.generate(
            "qa",
            state["project_id"],
            Review,
            "Review source against frozen API contract and PRD. Check authorization, validation, "
            "SQL injection, secrets, XSS, error handling and front/back request compatibility. "
            "Report actionable defects with owner and severity; never claim a test ran. "
            "Return operational lessons grounded in this code.",
            {
                "sources": sources,
                "contract": state["contract"],
                "prd": self.approved_prd(state, "qa"),
                "automated_issues": [i.model_dump() for i in issues],
                "memory": self.memory("qa"),
            },
        )
        issues += review.issues
        passed = not issues
        retries = dict(state["retries"])
        owners = set()
        for issue in issues:
            owners.update(("frontend", "backend") if issue.owner == "both" else (issue.owner,))
        exhausted = any(retries[role] >= self.cfg.max_retries for role in owners)
        if not passed and not exhausted:
            for role in owners:
                retries[role] += 1
        status = "completed" if passed else ("failed" if exhausted else "repairing")
        paths = self.paths(state)
        report = "# QA report\n\n" + json.dumps(
            {"status": status, "repairs": retries, "issues": [i.model_dump() for i in issues]},
            indent=2,
        )
        prior = self.vault.path("qa", paths["quality"], write=True)
        old = self.vault.read("qa", paths["quality"]) if prior.exists() else ""
        marker = f"<!-- qa:{self.vault.digest({'report': report, 'sources': sources})} -->"
        if marker not in old:
            self.vault.note(
                "qa",
                paths["quality"],
                state["project_id"],
                old + f"\n{marker}\n" + report,
                links=(paths["orders"][:-3],),
            )
        self.vault.write(
            "qa",
            "04_Quality/bug_report.md",
            report + f"\n\n[[{paths['quality'][:-3]}]]\n[[05_Employees/QAEngineer]]\n",
        )
        if passed:
            self.vault.learn(state["project_id"], review.lessons)
        return {
            "issues": [i.model_dump() for i in issues],
            "lessons": review.lessons,
            "qa_passed": passed,
            "retries": retries,
            "status": status,
        }

    def repair_route(self, state):
        if state["status"] in {"completed", "failed"}:
            return END
        owners = {i["owner"] for i in state["issues"]}
        if "both" in owners or {"frontend", "backend"}.issubset(owners):
            return "repair_both"
        return "frontend_repair" if "frontend" in owners else "backend_repair"

    async def repair_both(self, state):
        # Sequential repair nodes avoid parallel writes to shared state channels.
        front = await self.frontend_node(state)
        back = await self.backend_node({**state, **front})
        return {**front, **back}

    def compile(self, checkpointer):
        graph = StateGraph(State)
        for name in (
            "scout_node",
            "pm_node",
            "human_approval_node",
            "architect_node",
            "frontend_node",
            "backend_node",
            "qa_node",
            "repair_both",
        ):
            graph.add_node(name, getattr(self, name))
        graph.add_node("frontend_repair", self.frontend_node)
        graph.add_node("backend_repair", self.backend_node)
        graph.add_edge(START, "scout_node")
        graph.add_edge("scout_node", "pm_node")
        graph.add_edge("pm_node", "human_approval_node")
        graph.add_conditional_edges(
            "human_approval_node",
            lambda s: s["decision"],
            {"approve": "architect_node", "revise": "pm_node", "reject": END},
        )
        graph.add_edge("architect_node", "frontend_node")
        graph.add_edge("frontend_node", "backend_node")
        graph.add_edge("backend_node", "qa_node")
        graph.add_conditional_edges(
            "qa_node", self.repair_route, [END, "frontend_repair", "backend_repair", "repair_both"]
        )
        for node in ("frontend_repair", "backend_repair", "repair_both"):
            graph.add_edge(node, "qa_node")
        return graph.compile(checkpointer=checkpointer)
