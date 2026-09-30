import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field


class DecisionRequest(BaseModel):
    action: str = Field(pattern="^(approve|revise|reject)$")
    feedback: str = Field(default="", max_length=4000)


class ProjectRequest(BaseModel):
    idea: str = Field(min_length=10, max_length=2000)


def create_dashboard(cfg, store, vault, controller):
    app = FastAPI(title="Agent Company Dashboard", docs_url="/docs")

    def auth(authorization: str | None = Header(default=None), x_admin_token: str | None = Header(default=None)):
        expected = cfg.admin_token.get_secret_value() if cfg.admin_token else ""
        supplied = x_admin_token or (authorization or "").removeprefix("Bearer ")
        users = {part.split(":", 1)[1] for part in (cfg.admin_users or "").split(",") if ":" in part}
        valid = supplied == expected or (bool(users) and supplied in users)
        if not supplied or not valid:
            raise HTTPException(status_code=401, detail="Dashboard authentication required")

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/jobs")
    def jobs(_: None = Depends(auth)):
        return store.jobs()

    @app.post("/api/projects")
    def create_project(request: ProjectRequest, _: None = Depends(auth)):
        return {"id": store.enqueue(request.idea, cfg.max_jobs)}

    @app.get("/api/activity")
    def activity(_: None = Depends(auth)):
        path = vault.root / "00_Dashboard" / "🔴_LIVE_ACTIVITY.md"
        if not path.exists():
            path = next(iter((vault.root / "00_Dashboard").glob("*LIVE_ACTIVITY.md")), None)
        return {"content": path.read_text(encoding="utf-8") if path else ""}

    @app.get("/api/projects/{project}")
    def project(project: str, _: None = Depends(auth)):
        job = next((item for item in store.jobs() if item["id"] == project), None)
        if not job:
            raise HTTPException(status_code=404, detail="Project not found")
        approvals = [dict(row) for row in store.db.execute(
            "SELECT token,project,revision,payload,decision,consumed FROM approvals WHERE project=? ORDER BY revision",
            (project,),
        )]
        calls = [dict(row) for row in store.db.execute(
            "SELECT role,tokens,result IS NOT NULL AS cached FROM calls WHERE project=? ORDER BY rowid",
            (project,),
        )]
        return {"job": job, "approvals": approvals, "calls": calls}

    @app.get("/api/artifacts/{project}")
    def artifacts(project: str, _: None = Depends(auth)):
        names = []
        for root in (vault.root / "02_Specs", vault.root / "03_Architecture", vault.root / "04_Quality"):
            if root.exists():
                names.extend(str(p.relative_to(vault.root)) for p in root.rglob("*") if p.is_file() and project in p.name)
        return names

    @app.get("/api/usage")
    def usage(_: None = Depends(auth)):
        rows = [dict(r) for r in store.db.execute(
            "SELECT project,role,tokens,result IS NOT NULL AS cached FROM calls ORDER BY rowid"
        )]
        total = sum(row["tokens"] for row in rows)
        return {"total_tokens": total, "calls": rows, "estimated_cost_usd": round(total / 1_000_000 * 5, 4)}

    @app.get("/api/connections")
    async def connections(_: None = Depends(auth)):
        result = {"searxng": False, "provider": False}
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                result["searxng"] = (await client.get(cfg.searxng_url + "/search", params={"q": "test", "format": "json"})).is_success
                response = await client.get(cfg.openai_base_url.rstrip("/") + "/models", headers={"Authorization": "Bearer " + cfg.openai_api_key.get_secret_value()})
                result["provider"] = response.is_success
        except httpx.HTTPError as exc:
            result["error"] = type(exc).__name__
        return result

    @app.post("/api/approvals/{token}")
    def decision(token: str, request: DecisionRequest, _: None = Depends(auth)):
        if not store.decide(token, request.action, request.feedback):
            raise HTTPException(status_code=409, detail="Approval is stale or already decided")
        return {"ok": True}

    @app.post("/api/projects/{project}/retry")
    def retry(project: str, _: None = Depends(auth)):
        job = next((item for item in store.jobs() if item["id"] == project), None)
        if not job or job["status"] != "error":
            raise HTTPException(status_code=409, detail="Only error projects can be retried")
        store.status(project, "queued")
        return {"ok": True}

    @app.get("/api/files")
    def files(project: str | None = None, _: None = Depends(auth)):
        root = vault.workspace / project if project else vault.workspace
        root = root.resolve()
        if not root.is_relative_to(vault.workspace) or not root.exists():
            raise HTTPException(status_code=404, detail="Workspace not found")
        return [str(path.relative_to(vault.workspace)) for path in root.rglob("*") if path.is_file()]

    @app.get("/api/file")
    def file(path: str, _: None = Depends(auth)):
        try:
            target = vault.workspace / path
            target = target.resolve()
            if not target.is_relative_to(vault.workspace) or not target.is_file():
                raise ValueError
            if target.stat().st_size > 500000:
                raise HTTPException(status_code=413, detail="File too large")
            return {"path": path, "content": target.read_text(encoding="utf-8")}
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=404, detail="File not found") from None

    @app.get("/", response_class=HTMLResponse)
    def index():
        return HTML

    return app


HTML = """<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>Agent Company</title><style>
:root{color-scheme:light}body{font:15px system-ui;margin:0;background:#f5f7fb;color:#182230}body.dark{background:#111827;color:#e5e7eb}header{background:#172033;color:white;padding:18px 24px}main{padding:20px;max-width:1300px;margin:auto}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}.card{background:white;border:1px solid #dce2ea;border-radius:10px;padding:16px;box-shadow:0 2px 8px #17203312;margin:12px 0}.dark .card{background:#1f2937;border-color:#374151}button{padding:8px 12px;border:0;border-radius:6px;background:#2563eb;color:#fff;cursor:pointer;margin:3px}button.danger{background:#dc2626}input,textarea{padding:9px;border:1px solid #cbd5e1;border-radius:6px;width:100%;box-sizing:border-box;margin:5px 0}pre{white-space:pre-wrap;background:#0f172a;color:#dbeafe;padding:14px;border-radius:8px;max-height:500px;overflow:auto}.muted{color:#64748b}.status{font-weight:700}.ok{color:#16a34a}.bad{color:#dc2626}</style></head><body><header><b>Agent Company</b><span id=health style='float:right'>connecting</span></header><main><div class=card><input id=token type=password placeholder='Dashboard token'><button onclick='save()'>Connect</button><button onclick='document.body.classList.toggle("dark")'>Dark mode</button><button onclick='connections()'>Test connections</button><span id=conn></span></div><div class=card><input id=idea placeholder='New product idea (10+ characters)'><button onclick='create()'>Create project</button><input id=search placeholder='Search projects' oninput='render()'></div><div class=card><h3>Live activity</h3><pre id=activity>Loading...</pre></div><h2>Projects</h2><div id=jobs class=grid></div><div id=detail></div></main><script>
let token=localStorage.agentToken||'';let jobs=[];document.querySelector('#token').value=token;function save(){token=document.querySelector('#token').value;localStorage.agentToken=token;load()}async function api(path,opt={}){opt.headers={...(opt.headers||{}),'X-Admin-Token':token,'Content-Type':'application/json'};let r=await fetch(path,opt);if(!r.ok)throw Error((await r.json()).detail||r.status);return r.json()}async function load(){try{jobs=await api('/api/jobs');document.querySelector('#health').textContent='online';render();let a=await api('/api/activity');document.querySelector('#activity').textContent=a.content}catch(e){document.querySelector('#health').textContent=e.message}}function render(){let q=document.querySelector('#search').value.toLowerCase();document.querySelector('#jobs').innerHTML=jobs.filter(j=>(j.id+' '+j.idea+' '+j.status).toLowerCase().includes(q)).map(j=>`<div class=card><h3>${esc(j.id)}</h3><div>${esc(j.idea)}</div><p class=status>${esc(j.status)}</p>${j.error?`<p class=muted>${esc(j.error)}</p>`:''}<button onclick="show('${j.id}')">Open</button>${j.status==='error'?`<button onclick="retry('${j.id}')">Retry</button>`:''}</div>`).join('')}async function create(){await api('/api/projects',{method:'POST',body:JSON.stringify({idea:document.querySelector('#idea').value})});document.querySelector('#idea').value='';load()}async function show(id){let d=await api('/api/projects/'+id);let p=d.approvals.find(a=>!a.decision&&!a.consumed);document.querySelector('#detail').innerHTML=`<div class=card><h2>${esc(id)}</h2><h3>Token usage and QA state</h3><pre>${esc(JSON.stringify(d.calls,null,2))}</pre>${p?`<pre>${esc(p.payload)}</pre><textarea id=feedback placeholder='Revision feedback'></textarea><button onclick="decide('${p.token}','approve')">Approve</button><button onclick="decide('${p.token}','revise')">Revise</button><button class=danger onclick="decide('${p.token}','reject')">Reject</button>`:''}</div>`}async function decide(t,a){await api('/api/approvals/'+t,{method:'POST',body:JSON.stringify({action:a,feedback:document.querySelector('#feedback')?.value||''})});load()}async function retry(id){await api('/api/projects/'+id+'/retry',{method:'POST'});load()}async function connections(){let c=await api('/api/connections');document.querySelector('#conn').textContent=' SearXNG: '+c.searxng+' Provider: '+c.provider}function esc(s){return String(s??'').replace(/[&<>"']/g,x=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]))}load();setInterval(load,5000)</script></body></html>"""
