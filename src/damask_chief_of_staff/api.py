from __future__ import annotations
import contextlib
import hashlib
import hmac
import json
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from .config import Config
from .store import Store, now, uid
from .engine import Engine, intent_for
from .supervisor import Supervisor
from . import adapters, steering, checks, importer, provider
from . import __version__


class Login(BaseModel):
    token: str = Field(max_length=200)
class Connect(BaseModel):
    root: str = Field(min_length=1, max_length=2000)
    capture_text: bool = False
class Policy(BaseModel):
    mode: str
    types: list[str] = []
    paused: bool = False
class Correction(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
class Feedback(BaseModel):
    status: str
class Steering(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
class Check(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    argv: list[str]
    timeout: int = 60
class RunCheck(BaseModel):
    check_id: str
class Import(BaseModel):
    path: str = Field(max_length=2000)
class Model(BaseModel):
    provider: str
    model: str = ""
    endpoint: str = ""
    key_env: str = "ANTHROPIC_API_KEY"
    approved: bool = False


def create_app(config=None, port=8765, background=True):
    config = config or Config()
    store = Store(config)
    engine = Engine(store)
    supervisor = Supervisor(store)
    origin = f"http://127.0.0.1:{port}"
    csrf = hmac.new(config.token.encode(), b"browser-csrf-v1", hashlib.sha256).hexdigest()
    cookie = hmac.new(config.token.encode(), ("browser-session-v1:"+origin).encode(), hashlib.sha256).hexdigest()

    @asynccontextmanager
    async def lifespan(app):
        if background:
            supervisor.start()
        yield
        supervisor.stop()

    app = FastAPI(title="Damask Chief", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.store = store
    app.state.supervisor = supervisor

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        if request.headers.get("host") != f"127.0.0.1:{port}":
            return JSONResponse({"error": "Invalid host"}, status_code=403)
        supplied_origin = request.headers.get("origin")
        if supplied_origin and supplied_origin != origin:
            return JSONResponse({"error": "Cross-origin requests are not allowed"}, status_code=403)
        try:
            length = int(request.headers.get("content-length", "0") or 0)
        except ValueError:
            return JSONResponse({"error": "Invalid content length"}, status_code=400)
        if request.method == "POST" and "content-length" not in request.headers:
            return JSONResponse({"error": "Content length required"}, status_code=411)
        if length < 0 or length > 32_768:
            return JSONResponse({"error": "Request too large"}, status_code=413)
        path = request.url.path
        if path.startswith("/api/") and path != "/api/login":
            token_ok = hmac.compare_digest(request.headers.get("x-damask-token", ""), config.token)
            session_ok = hmac.compare_digest(request.headers.get("x-damask-session", ""), cookie)
            if not (token_ok or session_ok):
                return JSONResponse({"error": "Open Damask using its launcher to sign in"}, status_code=401)
            if request.method not in ("GET", "HEAD") and not token_ok and not hmac.compare_digest(request.headers.get("x-damask-csrf", ""), csrf):
                return JSONResponse({"error": "Invalid request token"}, status_code=403)
        response = await call_next(request)
        response.headers.update({"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
        return response

    @app.exception_handler(ValueError)
    async def bad_value(request, exc):
        return JSONResponse({"error": str(exc)[:500]}, status_code=400)

    @app.get("/health")
    def health():
        return {"service": "damask-chief", "version": __version__}

    @app.post("/api/login")
    def login(body: Login):
        if not hmac.compare_digest(body.token, config.token):
            raise HTTPException(401, "Invalid launch token")
        return {"ok": True, "session": cookie}

    @app.get("/api/identity")
    def identity():
        return {"service": "damask-chief", "home": str(config.home), "version": __version__}

    @app.get("/api/state")
    def state():
        config.reload()
        projects = [{**p, "policy": steering.policy_for(store, p)} for p in store.all("projects")]
        sessions = []
        for s in store.all("sessions"):
            summaries = store.all("summaries", session_id=s["id"])
            inferred = sorted([x for x in summaries if x.get("goal") and x["intent_version"] == s.get("intent_version")], key=lambda x: x["created"])
            actions = store.all("actions", session_id=s["id"])
            sessions.append({**s, "intent": intent_for(store, s), "interpretation": inferred[-1] if inferred else None,
                "auto_allowance": max(0, 3 - sum(1 for a in actions if a.get("auto") and a["created"] > now()-900 and a["state"] not in ("cancelled", "failed")))})
        with store.connect() as db:
            review_rows = db.execute("SELECT data FROM reviews ORDER BY ts DESC LIMIT 100").fetchall()
            error = db.execute("SELECT value FROM meta WHERE key='collector_error'").fetchone()
        hook_error = config.home / "collector-error.json"
        return {"fixture_mode": config.data.get("fixture_mode", False), "csrf": csrf, "version": __version__, "projects": projects, "sessions": sessions,
            "findings": store.all("findings"), "actions": store.all("actions"), "checks": store.all("checks"),
            "reviews": [json.loads(r[0]) for r in review_rows], "model": config.data["model"],
            "collector_error": json.loads(hook_error.read_text()) if hook_error.exists() else json.loads(error[0]) if error else None,
            "storage": {"database": str(config.db), "bytes": config.db.stat().st_size, "spool_events": len(list((config.home/"spool").glob("*.json")))},
            "usage_note": "Actual provider token counts only; monetary cost and worker usage unknown"}

    @app.post("/api/projects")
    def connect(body: Connect):
        return adapters.connect(store, body.root, body.capture_text)

    @app.post("/api/projects/{pid}/disconnect")
    def disconnect(pid: str):
        return adapters.disconnect(store, pid)

    @app.post("/api/projects/{pid}/policy")
    def policy(pid: str, body: Policy):
        return steering.set_policy(store, pid, body.mode, body.types, body.paused)

    @app.post("/api/projects/{pid}/import")
    def import_session(pid: str, body: Import):
        return importer.import_transcript(store, pid, body.path)

    @app.get("/api/sessions/{sid}/trace")
    def trace(sid: str, after: int = 0):
        store.get("sessions", sid)
        events = store.events(sid, max(0, after), 200)
        return {"events": events, "next_cursor": events[-1]["seq"] if events else after,
            "summaries": sorted(store.all("summaries", session_id=sid), key=lambda s: s["created"]), "more": len(events) == 200}

    @app.get("/api/sessions/{sid}/events/{eid}")
    def event(sid: str, eid: str):
        with store.connect() as db:
            row = db.execute("SELECT * FROM events WHERE session_id=? AND id=?", (sid,eid)).fetchone()
        if not row:
            raise ValueError("Retained reference unavailable")
        return {**dict(row), "data": json.loads(row["data"])}

    @app.get("/api/sessions/{sid}/continuation")
    def continuation(sid: str):
        return engine.continuation(sid)

    @app.post("/api/sessions/{sid}/correct")
    def correct(sid: str, body: Correction):
        engine.correct(sid, body.text)
        supervisor.request(sid)
        return {"ok": True}

    @app.post("/api/sessions/{sid}/review")
    def review(sid: str):
        supervisor.request(sid)
        return {"status": "queued; review limits still apply"}

    @app.post("/api/findings/{fid}/feedback")
    def feedback(fid: str, body: Feedback):
        if body.status not in ("open", "dismissed", "resolved", "expected", "snoozed"):
            raise ValueError("Unknown feedback state")
        with store.transaction() as db:
            f = store.get("findings", fid, db)
            f.update(status=body.status, updated=now(), feedback_at=now(), decision_latency_seconds=now()-f["created"])
            if body.status == "snoozed":
                f["snoozed_until"] = now()+3600
            store.put("findings", f, db)
            for action in store.all("actions", db, session_id=f["session_id"]):
                if action.get("finding_id") == fid and action["state"] == "queued":
                    action.update(state="cancelled", reason="Finding feedback changed", updated=now())
                    store.put("actions", action, db)
            store.audit("finding.feedback", {"id": fid, "status": body.status, "decision_latency_seconds": f["decision_latency_seconds"]}, db)
        return f

    @app.post("/api/findings/{fid}/steer")
    def steer(fid: str, body: Steering):
        from .privacy import text
        return steering.propose(store, fid, approved_text=text(body.text))

    @app.post("/api/actions/{aid}/cancel")
    def cancel(aid: str):
        with store.transaction() as db:
            a = store.get("actions", aid, db)
            if a["state"] != "queued":
                raise ValueError("Only a queued message can be cancelled; emitted messages cannot be recalled")
            a.update(state="cancelled", reason="Cancelled by developer", updated=now())
            store.put("actions", a, db)
            store.audit("action.cancelled", {"id": aid}, db)
        return a

    @app.post("/api/projects/{pid}/checks")
    def register_check(pid: str, body: Check):
        return checks.register(store, pid, body.name, body.argv, body.timeout)

    @app.post("/api/sessions/{sid}/checks")
    def run_check(sid: str, body: RunCheck):
        return checks.run(store, sid, body.check_id)

    @app.post("/api/model")
    def model(body: Model):
        model = body.model_dump()
        provider.validate_route(model)
        if model["provider"] != "disabled":
            import copy
            trial = copy.copy(config)
            trial.data = copy.deepcopy(config.data)
            trial.data["model"] = model
            probe = {"intent_version": 1, "human_sources": [{"id": "probe-human", "kind": "UserPromptSubmit", "data": {"prompt": "Fix the button spacing. Keep the existing API unchanged."}}],
                "events": [{"id": "probe-edit", "kind": "PostToolUse", "data": {"tool": "Edit", "file": "button.css"}}],
                "literal_constraints": [], "registered_checks": [], "coverage": "Synthetic route probe; no repository content", "gap": "No full diff"}
            output, usage = provider.call(trial, probe)
            if not set(output.goal_source_ids) <= {"probe-human"} or not set(output.summary_source_ids) <= {"probe-human", "probe-edit"}:
                raise ValueError("Model route probe returned invalid evidence references; configuration was not enabled")
            model["verified"] = True
            model["verified_at"] = now()
        config.reload()
        config.data["model"] = model
        config.save()
        with store.transaction() as db:
            store.audit("model.route-changed", {"provider": model["provider"], "approved": model["approved"]}, db)
        return {"ok": True}

    @app.post("/api/backup")
    def backup():
        path = config.home / "backups" / ("chief-" + uid() + ".sqlite")
        store.backup(path)
        return {"path": str(path), "note": "Consistent SQLite backup; config and source transcripts are separate"}

    web = Path(__file__).parent / "web"
    app.mount("/assets", StaticFiles(directory=web), name="assets")
    @app.get("/")
    def index():
        return FileResponse(web / "index.html")
    return app
