"""Loom web app: a long asynchronous workflow, not a chat.

Three screens: Start, Engagement page (stage rail, work pane, gate panel), Inbox.
The app never runs agents in the request: it writes inputs and enqueues jobs; the worker
(`loom worker`) runs them, so runs outlive the browser tab. Set LOOM_INLINE_WORKER=1 to run
the worker as a thread inside the app for single-container setups.
"""

from __future__ import annotations

import datetime as dt
import os
import secrets
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from loom import artifacts, config, jobs, orchestrator, state
from loom.cli import inbox as inbox_rows

HERE = Path(__file__).parent


@asynccontextmanager
async def lifespan(_: FastAPI):
    config.ENGAGEMENTS_DIR.mkdir(parents=True, exist_ok=True)
    config.ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    if os.environ.get("LOOM_INLINE_WORKER") == "1":
        threading.Thread(target=jobs.work_forever, daemon=True, name="loom-worker").start()
    yield


app = FastAPI(title="Loom", docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")
# Handoffs list files as the agent saw them; show them relative to the engagement.
templates.env.filters["short"] = lambda p: str(p).split("/engagements/", 1)[-1].split("/", 1)[-1]
security = HTTPBasic(auto_error=False)

APP_USER = os.environ.get("LOOM_APP_USER", "loom")
APP_PASSWORD = os.environ.get("LOOM_APP_PASSWORD", "")

# Files a reviewer may edit at a gate (curation), relative to the engagement folder.
EDITABLE = {
    1: ["01-intake/brief.md"],
    2: ["02-research/round-{r:02d}/questions.md", "02-research/round-{r:02d}/responses.md"],
    3: ["03-framing/problems.md", "03-framing/offline-log.md", "04-workshop/design/agenda.md"],
    4: ["05-delegation/routing.md"],
}
WAIT_INPUT = {"responses": "responses", "workshop": "workshop", "adoption": "tracking"}


def user(creds: HTTPBasicCredentials | None = Depends(security)) -> str:
    if not APP_PASSWORD:
        return "reviewer"  # local development only; infra/ always sets a password
    if creds and secrets.compare_digest(creds.username, APP_USER) and secrets.compare_digest(creds.password, APP_PASSWORD):
        return creds.username
    raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})


@app.get("/healthz", response_class=PlainTextResponse)
def healthz() -> str:
    return "ok"


# ---------------------------------------------------------------- Inbox


@app.get("/", response_class=HTMLResponse)
def inbox(request: Request, who: str = Depends(user)):
    rows = inbox_rows()
    for r in rows:
        r["age"] = _age(r["since"])
    engagements = [state.load(e) for e in state.list_ids()]
    engagements.sort(key=lambda s: s["updated_at"], reverse=True)
    return templates.TemplateResponse(request, "inbox.html", {"rows": rows, "engagements": engagements,
                                                              "stages": state.STAGES, "who": who})


# ---------------------------------------------------------------- Start


@app.get("/new", response_class=HTMLResponse)
def new_form(request: Request, who: str = Depends(user)):
    return templates.TemplateResponse(request, "new.html", {"who": who})


@app.post("/new")
async def new_submit(title: str = Form(...), intent: str = Form(...), sponsor: str = Form(""),
                     client: str = Form(""), sector: str = Form(""),
                     transcripts: list[UploadFile] = File(default=[]), documents: list[UploadFile] = File(default=[]),
                     who: str = Depends(user)):
    eid = orchestrator.create(title.strip(), intent.strip(), sponsor.strip(), client.strip(), sector.strip())
    n = await _save_uploads(eid, "transcripts", transcripts) + await _save_uploads(eid, "documents", documents)
    if n:
        jobs.enqueue("inputs", eid, kind="documents")
    return RedirectResponse(f"/e/{eid}", status_code=303)


# ---------------------------------------------------------------- Engagement page


@app.get("/e/{eid}", response_class=HTMLResponse)
def engagement(request: Request, eid: str, view: str | None = None, who: str = Depends(user)):
    st = _load(eid)
    d = state.edir(eid)
    stage = st["stage"]
    folder = d / state.STAGES[stage][1]
    handoffs = [_doc(d, p) for p in sorted(folder.rglob("*handoff*.md"), key=lambda p: p.stat().st_mtime, reverse=True)]
    arts = sorted((_doc(d, p) for p, _ in state.artifact_map(eid) if "handoff" not in p.name), key=lambda a: a["path"])
    current = [a for a in arts if a["path"].startswith(state.STAGES[stage][1])]
    selected = _doc(d, _safe(d, view)) if view else None
    gate = st.get("gate")
    ctx: dict[str, Any] = {
        "st": st, "stages": state.STAGES, "handoffs": handoffs, "artifacts": arts, "current": current,
        "selected": selected, "gate": gate, "allowed": sorted(orchestrator.ALLOWED.get(gate["gate"], set())) if gate else [],
        "blocked": bool(gate and gate.get("error")), "editable": _editable(st), "who": who,
        "queued_jobs": jobs.pending_for(eid), "log": state.read_log(eid)[-40:][::-1], "runlog": _tail(d / "run.log", 40),
        "inputs": {k: [p.name for p in orchestrator.input_files(eid, k)] for k in state.INPUT_KINDS},
        "wait_input": WAIT_INPUT.get((st.get("waiting") or {}).get("kind", "")), "items": _items(eid) if gate and gate["gate"] == 4 else [],
        "archive": config.ARCHIVE_DIR / f"{eid}.md", "children": [s for s in (state.load(i) for i in state.list_ids()) if s.get("parent") == eid],
    }
    if st["status"] == "waiting_org" and (st.get("waiting") or {}).get("kind") == "responses":
        r = st["research"]["round"]
        ctx["answered"] = len(ctx["inputs"]["responses"])
        ctx["round_questions"] = _doc(d, state.round_dir(eid, r) / "questions.md")
    return templates.TemplateResponse(request, "engagement.html", ctx)


@app.post("/e/{eid}/decide")
def decide(eid: str, request: Request, decision: str = Form(...), notes: str = Form(""),
           target_stage: int | None = Form(None), who: str = Depends(user)):
    _load(eid)
    args: dict[str, Any] = {"decision": decision, "by": who, "notes": notes}
    if target_stage:
        args["target_stage"] = target_stage
    return _enqueue_decision(eid, args, request)


@app.post("/e/{eid}/handoff")
async def handoff(eid: str, request: Request, who: str = Depends(user)):
    """Gate 4: record which spec goes to which owner and when you meet them."""
    form = await request.form()
    assignments = []
    for item in _items(eid):
        owner = str(form.get(f"owner_{item['item_id']}", "")).strip() or item["owner"]
        assignments.append({"item_id": item["item_id"], "owner": owner, "meeting": str(form.get(f"meeting_{item['item_id']}", ""))})
    return _enqueue_decision(eid, {"decision": "approve", "by": who, "notes": str(form.get("notes", "")), "assignments": assignments}, request)


@app.post("/e/{eid}/inputs")
async def inputs(eid: str, kind: str = Form(...), complete: str = Form(""), text: str = Form(""),
                 files: list[UploadFile] = File(default=[]), who: str = Depends(user)):
    _load(eid)
    if kind not in state.INPUT_KINDS:
        raise HTTPException(400, "bad kind")
    await _save_uploads(eid, kind, files)
    if text.strip():
        name = f"pasted-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"
        orchestrator.add_input(eid, kind, name, text.encode())
    if complete:
        jobs.enqueue("inputs", eid, kind=kind)
    return RedirectResponse(f"/e/{eid}", status_code=303)


@app.post("/e/{eid}/run")
def run(eid: str, who: str = Depends(user)):
    _load(eid)
    jobs.enqueue("advance", eid)
    return RedirectResponse(f"/e/{eid}", status_code=303)


@app.get("/e/{eid}/edit", response_class=HTMLResponse)
def edit_form(request: Request, eid: str, path: str, who: str = Depends(user)):
    st = _load(eid)
    if path not in _editable(st):
        raise HTTPException(403, "This file is not editable at the current gate.")
    p = _safe(state.edir(eid), path)
    text = p.read_text() if p.exists() else ""
    return templates.TemplateResponse(request, "edit.html", {"st": st, "path": path, "text": text, "errors": [], "who": who})


@app.post("/e/{eid}/edit", response_class=HTMLResponse)
def edit_save(request: Request, eid: str, path: str = Form(...), text: str = Form(...), who: str = Depends(user)):
    st = _load(eid)
    if path not in _editable(st):
        raise HTTPException(403, "This file is not editable at the current gate.")
    p = _safe(state.edir(eid), path)
    schema = next((s for f, s in state.artifact_map(eid) if f == p), None) or _schema_for(path)
    with state.locked(eid):
        backup = p.read_text() if p.exists() else None
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text.replace("\r\n", "\n"))
        errors = artifacts.validate_file(p, schema) if schema else []
        if errors and backup is not None:
            p.write_text(backup)
        if not errors:
            state.log(eid, who, "note", st["stage"], msg=f"edited {path} at gate")
    if errors:
        return templates.TemplateResponse(request, "edit.html", {"st": st, "path": path, "text": text, "errors": errors, "who": who}, status_code=422)
    return RedirectResponse(f"/e/{eid}?view={path}", status_code=303)


# ---------------------------------------------------------------- helpers


async def _save_uploads(eid: str, kind: str, files: list[UploadFile]) -> int:
    n = 0
    for f in files:
        if f.filename:
            orchestrator.add_input(eid, kind, f.filename, await f.read())
            n += 1
    return n


def _enqueue_decision(eid: str, args: dict[str, Any], request: Request):
    jobs.enqueue("decide", eid, **args)
    return RedirectResponse(f"/e/{eid}", status_code=303)


def _load(eid: str) -> dict[str, Any]:
    try:
        return state.load(eid)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "No such engagement")


def _safe(base: Path, rel: str) -> Path:
    p = (base / rel).resolve()
    if not str(p).startswith(str(base.resolve()) + os.sep):
        raise HTTPException(400, "bad path")
    return p


def _doc(base: Path, p: Path) -> dict[str, Any]:
    rel = str(p.relative_to(base)) if str(p).startswith(str(base)) else str(p)
    meta, body, raw = {}, "", ""
    if p.exists():
        raw = p.read_text(errors="replace")
        try:
            meta, body = artifacts.read(p)
        except artifacts.ArtifactError:
            body = raw
    return {"path": rel, "name": p.name, "meta": meta, "body": body, "raw": raw, "exists": p.exists()}


def _editable(st: dict[str, Any]) -> list[str]:
    if st["closed"] or st["status"] not in ("awaiting_gate", "waiting_org"):
        return []
    gate = (st.get("gate") or {}).get("gate")
    if gate is None and (st.get("waiting") or {}).get("kind") == "responses":
        gate = 2
    if gate is None and st["stage"] == 4:
        gate = 3
    return [f.format(r=st["research"]["round"]) for f in EDITABLE.get(gate, [])]


def _schema_for(path: str) -> str | None:
    for key, schema in (("brief.md", "brief"), ("questions.md", "questions"), ("responses.md", "responses"),
                        ("problems.md", "problems"), ("agenda.md", "agenda"), ("routing.md", "routing")):
        if path.endswith(key):
            return schema
    return None


def _items(eid: str) -> list[dict[str, Any]]:
    out = []
    for p in orchestrator._specs(eid):
        try:
            meta, _ = artifacts.read(p)
            out.append({"item_id": meta["item_id"], "owner": meta["owner"], "type": meta["type"],
                        "dod": meta["definition_of_done"], "path": str(p.relative_to(state.edir(eid)))})
        except (artifacts.ArtifactError, KeyError):
            continue
    return out


def _tail(p: Path, n: int) -> list[str]:
    return p.read_text(errors="replace").splitlines()[-n:][::-1] if p.exists() else []


def _age(ts: str) -> str:
    try:
        delta = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(ts)
    except ValueError:
        return ""
    if delta.days:
        return f"{delta.days}d"
    return f"{delta.seconds // 3600}h" if delta.seconds >= 3600 else f"{delta.seconds // 60}m"
