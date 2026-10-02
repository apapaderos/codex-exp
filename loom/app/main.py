"""Loom web app: looks and behaves like Claude, with the harness an engagement needs.

Three columns: navigation on the left, the engagement conversation in the centre, and what
the engagement holds on the right. Everything you act on appears inline in the
conversation: progress messages, output cards, decision cards, and a composer.

The app never runs agents in the request. It writes inputs and enqueues jobs; the worker
(`loom worker`, or LOOM_INLINE_WORKER=1) runs them. Decisions are held for
LOOM_UNDO_SECONDS so they can be undone until the next step starts.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import os
import secrets
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import markdown as md
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from loom import artifacts, config, jobs, orchestrator, state

from . import narrate

HERE = Path(__file__).parent
APP_USER = os.environ.get("LOOM_APP_USER", "loom")
APP_PASSWORD = os.environ.get("LOOM_APP_PASSWORD", "")
UNDO_SECONDS = float(os.environ.get("LOOM_UNDO_SECONDS", "20"))
# "Since you were last here" only after a real absence, not between clicks.
SINCE_GAP_SECONDS = float(os.environ.get("LOOM_SINCE_GAP_SECONDS", "3600"))
TRANSCRIPT_EXT = {".vtt", ".srt"}


@asynccontextmanager
async def lifespan(_: FastAPI):
    config.ENGAGEMENTS_DIR.mkdir(parents=True, exist_ok=True)
    config.ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    if os.environ.get("LOOM_INLINE_WORKER") == "1":
        threading.Thread(target=jobs.work_forever, kwargs={"poll_seconds": 1.0}, daemon=True, name="loom-worker").start()
    yield


app = FastAPI(title="Loom", docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")
security = HTTPBasic(auto_error=False)


def render_md(text: str) -> str:
    # Agent output is untrusted: escape raw HTML before rendering markdown.
    return md.markdown(html.escape(text or "", quote=False), extensions=["tables", "fenced_code", "sane_lists"])


templates.env.filters["md"] = render_md
templates.env.globals.update(STEPS=narrate.STEPS, CHALLENGE_TYPES=narrate.CHALLENGE_TYPES, ago=narrate.ago)


def user(creds: HTTPBasicCredentials | None = Depends(security)) -> str:
    if not APP_PASSWORD:
        return "reviewer"  # local development only; infra/ always sets a password
    if creds and secrets.compare_digest(creds.username, APP_USER) and secrets.compare_digest(creds.password, APP_PASSWORD):
        return creds.username
    raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})


@app.get("/healthz", response_class=PlainTextResponse)
def healthz() -> str:
    return "ok"


# ------------------------------------------------------------------ pages


@app.get("/", response_class=HTMLResponse)
def home(request: Request, who: str = Depends(user)):
    nav = _nav()
    return templates.TemplateResponse(request, "home.html", {"nav": nav, "who": who, "active": None})


@app.get("/new", response_class=HTMLResponse)
def kickoff(request: Request, who: str = Depends(user)):
    return templates.TemplateResponse(request, "kickoff.html", {"nav": _nav(), "who": who, "active": "new"})


@app.post("/new")
async def kickoff_start(request: Request, who: str = Depends(user)):
    form = await request.form()
    challenge = str(form.get("challenge", "")).strip()
    if not challenge:
        raise HTTPException(400, "Tell me the challenge first.")
    title = str(form.get("title", "")).strip() or _title_from(challenge)
    people = [json.loads(p) for p in form.getlist("person") if p]
    sponsor = next((p["name"] for p in people if p.get("role") == "Sponsor"), "")
    eid = orchestrator.create(title, challenge, sponsor, str(form.get("client", "")).strip(), "",
                              challenge_type=str(form.get("challenge_type", "")), people=people)
    files = [f for f in form.getlist("files") if getattr(f, "filename", "")]
    kinds = form.getlist("kind")
    for i, f in enumerate(files):
        kind = kinds[i] if i < len(kinds) and kinds[i] in ("transcripts", "documents") else _intake_kind(f.filename)
        orchestrator.add_input(eid, kind, f.filename, await f.read())
    if files:
        jobs.enqueue("advance", eid)
    return RedirectResponse(f"/e/{eid}", status_code=303)


@app.get("/e/{eid}", response_class=HTMLResponse)
def engagement(request: Request, eid: str, who: str = Depends(user)):
    st = _load(eid)
    _mark_seen(eid, who)
    ctx = _live_ctx(eid, st, who)
    return templates.TemplateResponse(request, "engagement.html", {**ctx, "nav": _nav(), "who": who, "active": eid})


@app.get("/e/{eid}/live")
def live(request: Request, eid: str, v: str = "", who: str = Depends(user)):
    """Polled by the page: returns fresh fragments only when something changed."""
    st = _load(eid)
    _mark_seen(eid, who)
    ctx = _live_ctx(eid, st, who)
    version = _version(eid, st, ctx["jobs"])
    if v == version:
        return JSONResponse({"version": version})
    out = {"version": version, "mode": f"{ctx['wait_kind']}|{st['closed']}|{st['stage']}"}
    for key, tpl in (("thread", "_thread.html"), ("bar", "_bar.html"), ("panel", "_panel.html"), ("nav", "_nav.html")):
        out[key] = templates.get_template(tpl).render({**ctx, "nav": _nav(), "active": eid, "request": request})
    return JSONResponse(out)


@app.get("/archive", response_class=HTMLResponse)
def archive(request: Request, q: str = "", who: str = Depends(user)):
    rows = []
    for p in sorted(config.ARCHIVE_DIR.glob("*.md"), reverse=True):
        try:
            m, body = artifacts.read(p)
        except artifacts.ArtifactError:
            continue
        hay = (json.dumps(m, default=str) + body).lower()
        if q and not all(w in hay for w in q.lower().split()):
            continue
        rows.append({"id": p.stem, "meta": m, "body": body})
    return templates.TemplateResponse(request, "archive.html", {"nav": _nav(), "who": who, "active": "archive", "rows": rows, "q": q})


@app.get("/settings", response_class=HTMLResponse)
def settings(request: Request, who: str = Depends(user)):
    from loom import agents

    ctx = {
        "nav": _nav(), "who": who, "active": "settings",
        "team": {"user": APP_USER, "protected": bool(APP_PASSWORD)},
        "notify": bool(config.NOTIFY_WEBHOOK), "base_url": config.APP_BASE_URL,
        "runner": config.RUNNER, "undo": UNDO_SECONDS,
        "models": [(a.name, a.model) for a in agents.all_agents()],
        "skills_extra": [str(p) for p in config.EXTRA_SKILL_DIRS],
        "missing_skills": sorted({s for a in agents.all_agents() for s in a.skills if not agents.find_skill(s)}),
        "storage": str(config.ENGAGEMENTS_DIR), "archive_dir": str(config.ARCHIVE_DIR),
        "limits": (config.MAX_TURNS, config.MAX_BUDGET_USD),
    }
    return templates.TemplateResponse(request, "settings.html", ctx)


# ------------------------------------------------------------------ actions


@app.post("/e/{eid}/decide")
async def decide(request: Request, eid: str, who: str = Depends(user)):
    st = _load(eid)
    form = await request.form()
    decision = str(form.get("decision", ""))
    note = str(form.get("note", "")).strip()
    if decision == "start":
        jobs.enqueue("advance", eid)
    elif decision.startswith("continue:"):
        kind = decision.split(":", 1)[1]
        if note:
            _save_note(eid, who, note, kind)
        jobs.enqueue("inputs", eid, kind=kind)
    else:
        args: dict[str, Any] = {"decision": decision, "by": who, "notes": note}
        if form.get("target_stage"):
            args["target_stage"] = int(str(form["target_stage"]))
        if form.get("note_as") == "direction" and note:
            _set_research_direction(eid, who, note)
        if decision == "approve" and (st.get("gate") or {}).get("gate") == 4:
            args["assignments"] = [
                {"item_id": i, "owner": str(form.get(f"owner_{i}", "")).strip(), "meeting": str(form.get(f"meeting_{i}", ""))}
                for i in form.getlist("item")
            ]
        jobs.enqueue("decide", eid, delay=UNDO_SECONDS, **args)
    return RedirectResponse(f"/e/{eid}#live", status_code=303)


@app.post("/e/{eid}/undo")
def undo(eid: str, job: str = Form(...), who: str = Depends(user)):
    _load(eid)
    if jobs.cancel(job):
        with state.locked(eid):
            state.log(eid, who, "undo", None, job=job)
    return RedirectResponse(f"/e/{eid}#live", status_code=303)


@app.post("/e/{eid}/say")
async def say(eid: str, text: str = Form(""), as_kind: str = Form("note"), files: list[UploadFile] = File(default=[]),
              who: str = Depends(user)):
    """The composer: free text, files and notes at any point."""
    st = _load(eid)
    wait_kind = {"responses": "responses", "workshop": "workshop", "adoption": "tracking"}.get((st.get("waiting") or {}).get("kind", ""))
    for f in files:
        if not f.filename:
            continue
        kind = wait_kind or (_intake_kind(f.filename) if st["stage"] == 1 else "documents")
        orchestrator.add_input(eid, kind, f.filename, await f.read())
    text = text.strip()
    if text:
        if as_kind in ("responses", "workshop", "tracking") and as_kind == wait_kind:
            name = f"pasted-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"
            orchestrator.add_input(eid, as_kind, name, text.encode())
        else:
            _save_note(eid, who, text)
            jobs.enqueue("ask", eid, question=text)
    return RedirectResponse(f"/e/{eid}#live", status_code=303)


@app.get("/e/{eid}/preview", response_class=HTMLResponse)
def preview(request: Request, eid: str, path: str, who: str = Depends(user)):
    st = _load(eid)
    p = _resolve(eid, path)
    meta, body = ({}, "")
    try:
        meta, body = artifacts.read(p)
    except artifacts.ArtifactError:
        body = p.read_text(errors="replace")
    return templates.TemplateResponse(request, "_preview.html", {
        "st": st, "path": path, "title": narrate.file_title(path), "summary": narrate.summary(p),
        "meta": meta, "body": body, "editable": path == "01-intake/brief.md" and _brief_editable(eid, st),
        "notes": narrate.handoff_for(eid, path) if not path.startswith("archive/") else {"unsure": [], "check": []},
    })


@app.get("/e/{eid}/brief", response_class=HTMLResponse)
def brief_form(request: Request, eid: str, who: str = Depends(user)):
    st = _load(eid)
    meta, _ = artifacts.read(state.edir(eid) / "01-intake/brief.md")
    return templates.TemplateResponse(request, "_brief_form.html", {"st": st, "b": meta, "error": ""})


@app.post("/e/{eid}/brief", response_class=HTMLResponse)
async def brief_save(request: Request, eid: str, who: str = Depends(user)):
    st = _load(eid)
    if not _brief_editable(eid, st):
        raise HTTPException(409, "I'm working on this engagement right now; edit the brief when I'm done.")
    form = await request.form()
    p = state.edir(eid) / "01-intake/brief.md"
    with state.locked(eid):
        meta, body = artifacts.read(p)
        meta["challenge"] = str(form.get("challenge", "")).strip() or meta["challenge"]
        meta["sponsor"] = str(form.get("sponsor", "")).strip()
        meta["stakeholders"] = [_person(line) for line in str(form.get("stakeholders", "")).splitlines() if line.strip()]
        meta["constraints"] = [c.strip() for c in str(form.get("constraints", "")).splitlines() if c.strip()]
        meta["research_direction"] = str(form.get("research_direction", "")).strip()
        errors = artifacts.errors_for(meta, "brief")
        if errors:
            return templates.TemplateResponse(request, "_brief_form.html", {"st": st, "b": meta, "error": "; ".join(errors)}, status_code=422)
        artifacts.write(p, meta, body)
        state.log(eid, who, "note", st["stage"], msg="You edited the brief.")
    return RedirectResponse(f"/e/{eid}#live", status_code=303)


# ------------------------------------------------------------------ helpers


def _live_ctx(eid: str, st: dict[str, Any], who: str) -> dict[str, Any]:
    js = jobs.jobs_for(eid)
    last_seen = _seen(eid, who)
    d = state.edir(eid)
    files: dict[int, list[dict[str, str]]] = {n: [] for n in narrate.STEPS}
    for p, _schema in state.artifact_map(eid):
        if "handoff" in p.name:
            continue
        rel = str(p.relative_to(d)) if str(p).startswith(str(d)) else f"archive/{p.name}"
        files[_step_of(rel)].append({"path": rel, "title": narrate.file_title(rel)})
    for extra in ("03-framing/offline-drafts.md", "03-framing/offline-log.md", "04-workshop/design/facilitation-guide.md",
                  "04-workshop/design/materials.md"):
        if (d / extra).exists():
            files[_step_of(extra)].append({"path": extra, "title": narrate.file_title(extra)})
    if (config.ARCHIVE_DIR / f"{eid}.md").exists():
        files[6].append({"path": f"archive/{eid}.md", "title": "Archive entry"})
    for n in files:
        files[n].sort(key=lambda f: f["path"])
    brief = {}
    if (d / "01-intake/brief.md").exists():
        try:
            brief, _ = artifacts.read(d / "01-intake/brief.md")
        except artifacts.ArtifactError:
            pass
    owners = st.get("assignments") or []
    tracking = []
    for p in sorted((d / "06-tracking").glob("x*.md")):
        try:
            m, _ = artifacts.read(p)
            tracking.append({"item": m["item_id"], "status": narrate._status(m["status"]), "raw": m["status"], "kpis": m["kpis"],
                             "path": f"06-tracking/{p.name}"})
        except (artifacts.ArtifactError, KeyError):
            continue
    thread = narrate.thread(eid, st, js, last_seen)
    return {
        "st": st, "eid": eid, "jobs": js,
        "thread": thread,
        "bar": narrate.next_step(eid, st, js),
        "files": files, "brief": brief, "owners": owners, "tracking": tracking,
        "wait_kind": {"responses": "responses", "workshop": "workshop", "adoption": "tracking"}.get((st.get("waiting") or {}).get("kind", "")),
        "decision_open": bool(thread and thread[-1]["kind"] == "decision" and thread[-1].get("buttons")),
        "brief_editable": _brief_editable(eid, st),
        "children": [s["id"] for s in (state.load(i) for i in state.list_ids()) if s.get("parent") == eid],
    }


def _version(eid: str, st: dict[str, Any], js: list[dict[str, Any]]) -> str:
    log_p = state.edir(eid) / "log.jsonl"
    tick = int(time.time()) if any(j["op"] == "decide" and j["where"] == "pending" for j in js) else 0
    raw = f"{st['updated_at']}|{log_p.stat().st_size if log_p.exists() else 0}|{[(j['id'], j['where']) for j in js]}|{tick}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def _nav() -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {"Needs you": [], "Running": [], "Waiting on the org": [], "Done": []}
    for i in state.list_ids():
        st = state.load(i)
        g = narrate.group(st, jobs.jobs_for(i))
        groups[g].append({"id": i, "title": st["title"], "step": narrate.STEPS[st["stage"]][0], "updated": st["updated_at"]})
    for g in groups.values():
        g.sort(key=lambda e: e["updated"], reverse=True)
    return groups


def _step_of(rel: str) -> int:
    if rel.startswith("archive/"):
        return 6
    try:
        return int(rel[:2])
    except ValueError:
        return 1


def _resolve(eid: str, rel: str) -> Path:
    if rel.startswith("archive/"):
        base, sub = config.ARCHIVE_DIR, rel.split("/", 1)[1]
    else:
        base, sub = state.edir(eid), rel
    p = (base / sub).resolve()
    if not str(p).startswith(str(base.resolve()) + os.sep) or not p.is_file():
        raise HTTPException(404, "No such file")
    return p


def _brief_editable(eid: str, st: dict[str, Any]) -> bool:
    return (not st["closed"] and st["status"] != "running" and not jobs.jobs_for(eid)
            and (state.edir(eid) / "01-intake/brief.md").exists())


def _set_research_direction(eid: str, who: str, direction: str) -> None:
    p = state.edir(eid) / "01-intake/brief.md"
    with state.locked(eid):
        meta, body = artifacts.read(p)
        meta["research_direction"] = direction
        artifacts.write(p, meta, body)
        state.log(eid, who, "note", 1, msg="You set the research direction.")


def _save_note(eid: str, who: str, text: str, kind: str = "") -> None:
    name = f"note-{dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.md"
    orchestrator.add_input(eid, "notes", name, f"Note from {who}, {state.now()}:\n\n{text}\n".encode())
    with state.locked(eid):
        state.log(eid, who, "message", None, text=text)


def _seen(eid: str, who: str) -> str | None:
    """When this person last looked, if it was long enough ago to mark what is new."""
    p = state.edir(eid) / ".seen.json"
    try:
        data = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data.get(who + ":visit")


def _mark_seen(eid: str, who: str) -> None:
    """Track a visit: a new visit starts after SINCE_GAP_SECONDS without looking."""
    p = state.edir(eid) / ".seen.json"
    try:
        data = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        data = {}
    now = state.now()
    last = data.get(who)
    if last:
        gap = (dt.datetime.fromisoformat(now) - dt.datetime.fromisoformat(last)).total_seconds()
        if gap > SINCE_GAP_SECONDS:
            data[who + ":visit"] = last  # everything after this is "since you were last here"
    data[who] = now
    p.write_text(json.dumps(data))


def _load(eid: str) -> dict[str, Any]:
    try:
        return state.load(eid)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "No such engagement")


def _intake_kind(filename: str) -> str:
    name = filename.lower()
    return "transcripts" if Path(name).suffix in TRANSCRIPT_EXT or "transcript" in name else "documents"


def _title_from(challenge: str) -> str:
    words = challenge.replace("\n", " ").split()
    return " ".join(words[:6]).rstrip(".,;:") + ("…" if len(words) > 6 else "")


def _person(line: str) -> dict[str, str]:
    name, _, role = line.partition(",")
    return {"name": name.strip(), "role": role.strip()}
