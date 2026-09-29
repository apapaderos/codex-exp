"""The orchestrator: a small state machine, not a clever agent.

It runs on three triggers only: a gate decision, new inputs, or responses arriving.
Each trigger calls advance(), which dispatches agents until the engagement reaches a
gate or a wait, then stops. It never decides content and never passes a gate itself.

Stage map (see docs/architecture.md for the diagram):

  1 intake      intake-voice + intake-docs -> brief merge          -> GATE 1
  2 research    round-NN: research-secondary -> research-primary    -> GATE 2
                  send          -> WAITING responses -> (normalise) -> next round
                  another_round -> next round
                  approve       -> stage 3
  3 framing     framing (no gate of its own)                        -> stage 4
  4 workshop    workshop-designer                                   -> GATE 3
                  approve -> WAITING workshop (people run the room)
                  inputs  -> workshop-capture                        -> stage 5
  5 delegation  delegation -> spec-decision/process/tool per item    -> GATE 4
  6 tracking    tracking -> WAITING adoption; updates re-run tracking
                  all KPIs met -> GATE 5 (close) -> archivist -> closed
                  restart      -> archivist + new engagement (parent link)
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any, Callable

from . import artifacts, brief, notify, refs, state
from .agents import Task
from .runners import Runner, get_runner
from .state import GATES, edir, log, now, round_dir

L = logging.getLogger("loom.orchestrator")
ACTOR = "orchestrator"

SPEC_DIRS = {"decision": "decisions", "process": "processes", "tool": "tools"}
SPEC_AGENTS = {"decision": "spec-decision", "process": "spec-process", "tool": "spec-tool"}
GATE_FOR_STAGE = {1: 1, 2: 2, 3: 3, 4: 3, 5: 4, 6: 5}
ALLOWED = {
    1: {"approve", "revise"},
    2: {"approve", "revise", "reject", "another_round", "send"},
    3: {"approve", "revise", "reject"},
    4: {"approve", "revise", "reject"},
    5: {"close", "restart", "revise"},
}


class Blocked(Exception):
    """A step could not produce valid output; the stage stops at its gate with an error."""


# --------------------------------------------------------------------------- triggers


def create(title: str, intent: str, sponsor: str = "", client: str = "", sector: str = "",
           parent: str | None = None, eid: str | None = None) -> str:
    base = eid or f"{now()[:7]}-{state.slugify(title)}"
    eid, n = base, 2
    while edir(eid).exists():
        eid, n = f"{base}-{n}", n + 1
    d = edir(eid)
    for sub in ["00-inputs/" + k for k in state.INPUT_KINDS] + [
        "01-intake", "02-research", "03-framing", "04-workshop/design", "04-workshop/capture",
        "05-delegation/decisions", "05-delegation/processes", "05-delegation/tools", "06-tracking",
    ]:
        (d / sub).mkdir(parents=True, exist_ok=True)
    st = state.new_state(eid, title, intent, sponsor, client, sector, parent)
    state.save(st)
    log(eid, ACTOR, "created", 1, title=title, parent=parent)
    return eid


def add_input(eid: str, kind: str, filename: str, data: bytes) -> Path:
    if kind not in state.INPUT_KINDS:
        raise ValueError(f"input kind must be one of {state.INPUT_KINDS}")
    name = Path(filename).name.replace(" ", "_") or "upload.txt"
    dest = edir(eid) / "00-inputs" / kind / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    log(eid, "human", "inputs", kind=kind, file=name)
    return dest


def inputs_arrived(eid: str, kind: str, runner: Runner | None = None) -> dict[str, Any]:
    """Trigger: new files in 00-inputs/ (or responses) and the human says they are complete."""
    with state.locked(eid):
        st = state.load(eid)
        w = st.get("waiting") or {}
        if kind in ("transcripts", "documents") and st["stage"] == 1 and st["status"] in ("queued", "awaiting_gate"):
            st["gate"] = None
            state.set_status(st, "queued")
        elif kind == "responses" and w.get("kind") == "responses":
            _end_wait(st, "responses")
            st["stages"]["2"]["step"] = "responses"
            state.set_status(st, "queued")
        elif kind == "workshop" and w.get("kind") == "workshop":
            _end_wait(st, "workshop")
            st["stages"]["4"]["step"] = "capture"
            state.set_status(st, "queued")
        elif kind == "tracking" and w.get("kind") == "adoption":
            _end_wait(st, "adoption")
            state.set_status(st, "queued")
        else:
            log(eid, ACTOR, "note", st["stage"], msg=f"{kind} inputs stored; no transition from stage {st['stage']} {st['status']}")
            return st
        state.save(st)
    return advance(eid, runner)


def decide(eid: str, decision: str, by: str, notes: str = "", target_stage: int | None = None,
           assignments: list[dict[str, str]] | None = None, runner: Runner | None = None) -> dict[str, Any]:
    """Trigger: a human gate decision. Equivalent to writing pending_decision into state.json."""
    with state.locked(eid):
        st = state.load(eid)
        pd: dict[str, Any] = {"decision": decision, "by": by, "at": now(), "notes": notes}
        if st.get("gate"):
            pd["gate"] = st["gate"]["gate"]
        if target_stage:
            pd["target_stage"] = target_stage
        if assignments:
            pd["assignments"] = assignments
        st["pending_decision"] = pd
        state.save(st)
    return advance(eid, runner)


def advance(eid: str, runner: Runner | None = None, on_event: Callable[[str], None] | None = None) -> dict[str, Any]:
    runner = runner or get_runner()
    with state.locked(eid):
        for _ in range(100):
            st = state.load(eid)
            if st.get("pending_decision"):
                _apply_decision(st, runner)
                continue
            if st["closed"] or st["status"] not in ("queued", "running"):
                return st
            if st["stage"] == 1 and not any(_intake_files(eid).values()):
                return st  # nothing to work on yet; the inputs trigger will wake us
            try:
                _run_stage(st, runner, on_event or _runlog(eid))
            except Blocked as b:
                st = state.load(eid)
                _open_gate(st, GATE_FOR_STAGE[st["stage"]], error=str(b))
        raise RuntimeError("advance() did not settle; check log.jsonl")


# --------------------------------------------------------------------------- stages


def _run_stage(st: dict[str, Any], runner: Runner, on_event: Callable[[str], None]) -> None:
    n = st["stage"]
    if st["status"] != "running":
        state.set_status(st, "running")
        state.save(st)
        log(st["id"], ACTOR, "stage", n, status="running", name=state.STAGES[n][0])
    {1: _stage_intake, 2: _stage_research, 3: _stage_framing, 4: _stage_workshop,
     5: _stage_delegation, 6: _stage_tracking}[n](st, runner, on_event)


def _stage_intake(st, runner, on_event):
    eid, notes = st["id"], st.get("notes", "")
    files = _intake_files(eid)
    if files["transcripts"]:
        _dispatch(runner, on_event, Task(
            agent="intake-voice", eid=eid, stage=1, notes=notes,
            instructions="Turn the uploaded meeting transcripts into structured signal. Follow loom-voice-intake. "
                         "Keep low-confidence passages, score them honestly, and list every doubt under uncertain[]. "
                         "Never guess a misheard word.",
            reads=[_rel(p) for p in files["transcripts"]],
            writes=[_e(eid, "01-intake/signal-voice.md")],
            handoff=_e(eid, "01-intake/handoff-intake-voice.md"),
        ), [("01-intake/signal-voice.md", "signal-voice")])
    if files["documents"]:
        _dispatch(runner, on_event, Task(
            agent="intake-docs", eid=eid, stage=1, notes=notes,
            instructions="Turn the uploaded documents, slides, diagrams and images into structured signal. "
                         "Follow loom-doc-intake. Every claim cites a page, slide or diagram region; describe every visual in text.",
            reads=[_rel(p) for p in files["documents"]],
            writes=[_e(eid, "01-intake/signal-docs.md")],
            handoff=_e(eid, "01-intake/handoff-intake-docs.md"),
        ), [("01-intake/signal-docs.md", "signal-docs")])
    brief.merge(eid, st)
    _check(eid, [("01-intake/brief.md", "brief")])
    log(eid, ACTOR, "merged", 1, file="01-intake/brief.md")
    _open_gate(state.load(eid), 1)


def _stage_research(st, runner, on_event):
    eid, n = st["id"], st["research"]["round"]
    rd = round_dir(eid, n)
    rd.mkdir(parents=True, exist_ok=True)
    rr = lambda f: _rel(rd / f)  # noqa: E731
    step = st["stages"]["2"].get("step")

    if step == "responses":
        if artifacts.validate_file(rd / "responses.md", "responses"):
            raw = input_files(eid, "responses")
            if not raw:
                raise Blocked(f"No responses found for round {n}: upload to 00-inputs/responses/ or write {rr('responses.md')}.")
            _dispatch(runner, on_event, Task(
                agent="research-primary", eid=eid, stage=2, extra={"mode": "normalise", "round": n},
                instructions=f"NORMALISE mode. Turn the raw survey/email responses into responses.md for round {n}: "
                             "one answer per respondent per question, keyed to the question ids in questions.md. "
                             "Quote, never paraphrase into something stronger. Do not write new questions.",
                reads=[rr("questions.md"), *[_rel(p) for p in raw]],
                writes=[rr("responses.md")], handoff=rr("handoff-normalise.md"),
            ), [(rd / "responses.md", "responses")])
        _check_refs(eid, rd / "responses.md", "question_id", rd / "questions.md")
        st = state.load(eid)
        st["research"]["round"] = n + 1
        st["research"]["prompts"] = []
        st["stages"]["2"].pop("step", None)
        state.save(st)
        log(eid, ACTOR, "note", 2, msg=f"responses for round {n} in; starting round {n + 1}")
        return  # advance() loops and runs the next round

    prev: list[str] = []
    if n > 1:
        pd = round_dir(eid, n - 1)
        prev = [_rel(pd / f) for f in ("secondary.md", "questions.md", "responses.md") if (pd / f).exists()]
    prompts = "\n".join(st["research"].get("prompts") or [])
    _dispatch(runner, on_event, Task(
        agent="research-secondary", eid=eid, stage=2, extra={"round": n},
        notes="\n".join(x for x in [st.get("notes", ""), prompts] if x),
        instructions=f"Research round {n}. Search archive/ first (grep the frontmatter), then the web. "
                     "Mark every finding from_archive true or false. List the gaps you could not close; "
                     "they are what primary research will ask the organisation. Never state a figure you did not open a source for.",
        reads=[_e(eid, "01-intake/brief.md"), *_signals(eid), state.agent_path(state.config.ARCHIVE_DIR) + "/", *prev],
        writes=[rr("secondary.md")], handoff=rr("handoff-research-secondary.md"),
    ), [(rd / "secondary.md", "secondary")])
    _dispatch(runner, on_event, Task(
        agent="research-primary", eid=eid, stage=2, extra={"mode": "questions", "round": n},
        notes=st.get("notes", ""),
        instructions=f"Round {n}. Write the questions for people in the organisation: one gap per question, each with gap_ref. "
                     "Do not ask what the archive or earlier responses already answered. Choose survey or email and size the audience.",
        reads=[rr("secondary.md"), _e(eid, "01-intake/brief.md"), *prev],
        writes=[rr("questions.md")], handoff=rr("handoff-research-primary.md"),
    ), [(rd / "questions.md", "questions")])
    _check_refs(eid, rd / "questions.md", "gap_ref", rd / "secondary.md")
    _open_gate(state.load(eid), 2)


def _stage_framing(st, runner, on_event):
    eid = st["id"]
    _dispatch(runner, on_event, Task(
        agent="framing", eid=eid, stage=3, notes=st.get("notes", ""),
        instructions="Challenge the framing first (reframe), then split every problem into offline or co-creation using "
                     "loom-problem-triage. Every problem gets an owner and evidence_refs that resolve to an id in an intake "
                     "or research file. Weight low-confidence claims less. For each offline problem, draft the message "
                     "that closes it in offline-drafts.md.",
        reads=[_e(eid, "01-intake/brief.md"), *_signals(eid), *_research_files(eid)],
        writes=[_e(eid, "03-framing/problems.md"), _e(eid, "03-framing/offline-drafts.md")],
        handoff=_e(eid, "03-framing/handoff-framing.md"),
    ), [("03-framing/problems.md", "problems")])
    meta, _ = artifacts.read(edir(eid) / "03-framing/problems.md")
    bad = refs.broken_refs(eid, [r for p in meta["offline"] + meta["cocreate"] for r in p["evidence_refs"]])
    if bad:
        raise Blocked("problems.md has evidence_refs that do not resolve: " + ", ".join(bad))
    offline_log = edir(eid) / "03-framing/offline-log.md"
    if not offline_log.exists():
        offline_log.write_text("# Offline agreements\n\nLog each offline problem once its message is sent and agreed:\n"
                               "`- p1 · sent YYYY-MM-DD to <owner> · agreed YYYY-MM-DD · <what was agreed>`\n")
    st = state.load(eid)
    state.set_status(st, "done")
    st["stage"], st["notes"] = 4, ""
    st["stages"]["4"]["step"] = "design"
    state.set_status(st, "queued")
    state.save(st)
    log(eid, ACTOR, "stage", 3, status="done")


def _stage_workshop(st, runner, on_event):
    eid = st["id"]
    if st["stages"]["4"].get("step") == "capture":
        wfiles = input_files(eid, "workshop")
        if not wfiles:
            raise Blocked("No workshop capture inputs in 00-inputs/workshop/.")
        _dispatch(runner, on_event, Task(
            agent="workshop-capture", eid=eid, stage=4, notes=st.get("notes", ""),
            instructions="Capture what came out of the room: photos of walls, facilitator notes, meeting transcripts. "
                         "Reuse the intake methods. Every action gets a type (decision, process or tool), an owner and a due date. "
                         "If the room did not name an owner or date, write 'TBC' as owner or the agreed follow-up date and flag it in unsure.",
            reads=[*[_rel(p) for p in wfiles], _e(eid, "04-workshop/design/agenda.md"), _e(eid, "03-framing/problems.md")],
            writes=[_e(eid, "04-workshop/capture/outcomes.md")],
            write_dirs=[_e(eid, "04-workshop/capture")],
            handoff=_e(eid, "04-workshop/capture/handoff-workshop-capture.md"),
        ), [("04-workshop/capture/outcomes.md", "outcomes")])
        st = state.load(eid)
        state.set_status(st, "done")
        st["stage"], st["notes"] = 5, ""
        state.set_status(st, "queued")
        state.save(st)
        log(eid, ACTOR, "stage", 4, status="done")
        return

    _dispatch(runner, on_event, Task(
        agent="workshop-designer", eid=eid, stage=4, notes=st.get("notes", ""),
        instructions="Design the Solve-for-X session for the co-creation problems only (offline problems never enter the room). "
                     "Structure it as Reframe, Explore, Shape. Every co-creation problem id must appear in problems_in_scope. "
                     "Add a facilitation guide and materials list in the design folder.",
        reads=[_e(eid, "03-framing/problems.md"), _e(eid, "01-intake/brief.md")],
        writes=[_e(eid, "04-workshop/design/agenda.md")],
        write_dirs=[_e(eid, "04-workshop/design")],
        handoff=_e(eid, "04-workshop/design/handoff-workshop-designer.md"),
    ), [("04-workshop/design/agenda.md", "agenda")])
    pmeta, _ = artifacts.read(edir(eid) / "03-framing/problems.md")
    ameta, _ = artifacts.read(edir(eid) / "04-workshop/design/agenda.md")
    missing = {p["id"] for p in pmeta["cocreate"]} - set(ameta["problems_in_scope"])
    if missing:
        raise Blocked("agenda.md does not cover co-creation problems: " + ", ".join(sorted(missing)))
    _open_gate(state.load(eid), 3)


def _stage_delegation(st, runner, on_event):
    eid, notes = st["id"], st.get("notes", "")
    _dispatch(runner, on_event, Task(
        agent="delegation", eid=eid, stage=5, notes=notes,
        instructions="Route every workshop action to exactly one spec agent. Split compound actions into x<N>a, x<N>b. "
                     "The runtime dispatches the spec agents from your routing.md; do not dispatch them yourself.",
        reads=[_e(eid, "04-workshop/capture/outcomes.md")],
        writes=[_e(eid, "05-delegation/routing.md")],
        handoff=_e(eid, "05-delegation/handoff-delegation.md"),
    ), [("05-delegation/routing.md", "routing")])
    ometa, _ = artifacts.read(edir(eid) / "04-workshop/capture/outcomes.md")
    rmeta, _ = artifacts.read(edir(eid) / "05-delegation/routing.md")
    unrouted = {a["id"] for a in ometa["actions"]} - {i["from_action"] for i in rmeta["items"]}
    if unrouted:
        raise Blocked("routing.md leaves actions unrouted: " + ", ".join(sorted(unrouted)))
    for item in rmeta["items"]:
        folder = f"05-delegation/{SPEC_DIRS[item['type']]}"
        rel = f"{folder}/{item['item_id']}.md"
        _dispatch(runner, on_event, Task(
            agent=SPEC_AGENTS[item["type"]], eid=eid, stage=5, notes=notes, extra={"item": item},
            instructions=f"Write the implementation spec for item {item['item_id']} ({item['type']}): {item['summary']}. "
                         f"Owner: {item['owner']}, due {item['due']}. The owner must be able to act on it without asking the EC team. "
                         "Walk every claim back with evidence_refs.",
            reads=[_e(eid, "05-delegation/routing.md"), _e(eid, "04-workshop/capture/outcomes.md"),
                   _e(eid, "03-framing/problems.md"), _e(eid, "01-intake/brief.md")],
            writes=[_e(eid, rel)],
            handoff=_e(eid, f"{folder}/{item['item_id']}.handoff.md"),
        ), [(rel, "spec")])
        smeta, _ = artifacts.read(edir(eid) / rel)
        if smeta["item_id"] != item["item_id"]:
            raise Blocked(f"{rel}: item_id {smeta['item_id']} does not match routing {item['item_id']}")
    _open_gate(state.load(eid), 4)


def _stage_tracking(st, runner, on_event):
    eid = st["id"]
    specs = _specs(eid)
    updates = input_files(eid, "tracking")
    writes = [_e(eid, f"06-tracking/{p.stem}.md") for p in specs]
    _dispatch(runner, on_event, Task(
        agent="tracking", eid=eid, stage=6, notes=st.get("notes", ""),
        instructions="For each spec, pick measurable KPIs with a baseline, a target and a date, and write one OKR. "
                     "If adoption updates are present, update current values and status (met only when every KPI hit its target).",
        reads=[_rel(p) for p in specs] + [_rel(p) for p in updates] + [w for w in writes if _from_root(w).exists()],
        writes=writes, handoff=_e(eid, "06-tracking/handoff-tracking.md"),
    ), [(f"06-tracking/{p.stem}.md", "tracking") for p in specs])
    st = state.load(eid)
    statuses = [artifacts.read(edir(eid) / f"06-tracking/{p.stem}.md")[0]["status"] for p in specs]
    if statuses and all(s == "met" for s in statuses):
        _open_gate(st, 5)
    else:
        _start_wait(st, "adoption", items=len(statuses), met=statuses.count("met"))


def _archive(st: dict[str, Any], runner: Runner) -> None:
    eid = st["id"]
    _dispatch(runner, _runlog(eid), Task(
        agent="archivist", eid=eid, stage=6,
        instructions="Write the archive entry for this engagement: context, decisions taken, the story behind it, the "
                     "workshop output, action items, what landed and what did not, and lessons worth keeping. "
                     "Frontmatter must make it findable by client, sector and challenge type.",
        reads=[_e(eid, "state.json"), _e(eid, "01-intake/brief.md"), _e(eid, "03-framing/problems.md"),
               _e(eid, "03-framing/offline-log.md"), _e(eid, "04-workshop/capture/outcomes.md"),
               *[_rel(p) for p in _specs(eid)], *[_rel(p) for p in sorted((edir(eid) / "06-tracking").glob("x*.md"))]],
        writes=[state.agent_path(state.config.ARCHIVE_DIR / f"{eid}.md")],
        handoff=_e(eid, "06-tracking/handoff-archivist.md"),
    ), [(state.config.ARCHIVE_DIR / f"{eid}.md", "archive")])


# --------------------------------------------------------------------------- gates


def _open_gate(st: dict[str, Any], gate: int, error: str | None = None) -> None:
    st["gate"] = {"gate": gate, "opened_at": now(), "question": GATES[gate], "error": error}
    st["waiting"] = None
    state.set_status(st, "awaiting_gate")
    state.save(st)
    log(st["id"], ACTOR, "gate_opened", st["stage"], gate=gate, error=error)
    notify.send("gate_opened", st, f"Gate {gate} is open. {GATES[gate]}" + (f" (blocked: {error})" if error else ""))


def _start_wait(st: dict[str, Any], kind: str, **detail: Any) -> None:
    st["gate"] = None
    st["waiting"] = {"kind": kind, "since": now(), "detail": detail}
    state.set_status(st, "waiting_org")
    state.save(st)
    log(st["id"], ACTOR, "waiting", st["stage"], kind=kind, **detail)


def _end_wait(st: dict[str, Any], kind: str) -> None:
    since = (st.get("waiting") or {}).get("since")
    st["waiting"] = None
    log(st["id"], ACTOR, "wait_ended", st["stage"], kind=kind, since=since)
    notify.send("wait_ended", st, f"Waiting on {kind} has ended; Loom is running again.")


def _apply_decision(st: dict[str, Any], runner: Runner) -> None:
    eid, pd = st["id"], dict(st["pending_decision"])
    st["pending_decision"] = None
    gate = (st.get("gate") or {}).get("gate")
    d = pd.get("decision")
    at_close = st["stage"] == 6 and st["status"] in ("waiting_org", "awaiting_gate")
    if at_close and d in ("close", "restart"):
        gate = 5
    blocked = bool((st.get("gate") or {}).get("error"))

    def refuse(msg: str) -> None:
        if st.get("gate"):
            st["gate"]["refused"] = msg
        state.save(st)
        log(eid, pd.get("by", "human"), "invalid", st["stage"], decision=d, reason=msg)

    if gate is None or st["status"] not in ("awaiting_gate", "waiting_org"):
        return refuse(f"No gate is open (stage {st['stage']} is {st['status']}).")
    if pd.get("gate") not in (None, gate) and not at_close:
        return refuse(f"Decision is for gate {pd.get('gate')} but gate {gate} is open.")
    allowed = {"revise", "reject"} if blocked and gate != 1 else ({"revise"} if blocked else ALLOWED[gate])
    if d not in allowed:
        return refuse(f"'{d}' is not allowed at gate {gate}{' while blocked' if blocked else ''}; use one of {sorted(allowed)}.")

    rec = {"gate": gate, "decision": d, "by": pd.get("by") or "human", "at": pd.get("at") or now(), "notes": pd.get("notes", "")}
    for k in ("target_stage", "assignments"):
        if pd.get(k):
            rec[k] = pd[k]
    if gate == 2:
        rec["round"] = st["research"]["round"]

    n, notes = st["stage"], pd.get("notes", "")
    rd = round_dir(eid, st["research"]["round"])

    if d == "approve":
        if gate == 1:
            problems = artifacts.validate_file(edir(eid) / "01-intake/brief.md", "brief")
            meta, _ = artifacts.read(edir(eid) / "01-intake/brief.md") if not problems else ({}, "")
            if problems or not str(meta.get("research_direction", "")).strip():
                return refuse("Set research_direction in 01-intake/brief.md (and fix any schema problems) before approving. " + "; ".join(problems))
            _next_round_if_used(st)
            _advance_to(st, 2)
        elif gate == 2:
            _advance_to(st, 3)
        elif gate == 3:
            st["stages"]["4"]["step"] = "capture"
            st["notes"] = ""
            state.save(st)
            _start_wait(st, "workshop", agenda=_e(eid, "04-workshop/design/agenda.md"))
        elif gate == 4:
            st["assignments"] = pd.get("assignments") or _default_assignments(eid)
            _advance_to(st, 6)
    elif d == "send":
        problems = artifacts.validate_file(rd / "questions.md", "questions")
        if problems:
            return refuse("questions.md is not valid after curation: " + "; ".join(problems))
        q, _ = artifacts.read(rd / "questions.md")
        st["notes"] = ""
        state.save(st)
        _start_wait(st, "responses", round=st["research"]["round"], audience=q["audience"], channel=q["channel"],
                    questions=len(q["questions"]))
    elif d == "another_round":
        st["research"]["round"] += 1
        st["research"]["prompts"] = [notes] if notes else []
        st["notes"] = ""
        st["gate"] = None
        state.set_status(st, "queued")
    elif d == "revise":
        st["notes"] = notes
        st["gate"] = None
        state.set_status(st, "queued")
    elif d == "reject":
        target = int(pd.get("target_stage") or max(1, n - 1))
        if target >= n:
            return refuse(f"reject needs target_stage earlier than the current stage {n}.")
        _rewind(st, target, notes)
    elif d in ("close", "restart"):
        st["gate"], st["waiting"] = None, None
        state.save(st)
        try:
            _archive(st, runner)
        except Blocked as b:
            st = state.load(eid)
            st["decisions"].append(rec)
            _open_gate(st, 5, error=str(b))
            return
        st = state.load(eid)
        state.set_status(st, "done")
        st["closed"] = True
        if d == "restart":
            child = create(f"{st['title']} (follow-up)", notes or f"Follow-up to {eid}", st.get("sponsor", ""),
                           st.get("client", ""), st.get("sector", ""), parent=eid)
            rec["notes"] = (notes + f"\nfollow-up engagement: {child}").strip()
        log(eid, rec["by"], "closed", 6, decision=d)
    st["decisions"].append(rec)
    state.save(st)
    log(eid, rec["by"], "gate_decision", st["stage"], **{k: v for k, v in rec.items() if k not in ("by", "at")})


def _advance_to(st: dict[str, Any], stage: int) -> None:
    state.set_status(st, "done")
    st["stage"], st["gate"], st["waiting"], st["notes"] = stage, None, None, ""
    state.set_status(st, "queued")


def _rewind(st: dict[str, Any], target: int, notes: str) -> None:
    for k in range(target, 7):
        st["stages"][str(k)] = {"status": "queued"}
    st["stage"], st["gate"], st["waiting"], st["notes"] = target, None, None, notes
    if target == 2:
        _next_round_if_used(st, prompts=[notes] if notes else [])
    if target == 4:
        # The approved design already ran in the room; going back to 4 means re-capturing.
        st["stages"]["4"]["step"] = "capture" if input_files(st["id"], "workshop") else "design"
    state.set_status(st, "queued")


def _next_round_if_used(st: dict[str, Any], prompts: list[str] | None = None) -> None:
    """Research history is never overwritten: re-entering stage 2 opens a fresh round."""
    if (round_dir(st["id"], st["research"]["round"]) / "secondary.md").exists():
        st["research"]["round"] += 1
    if prompts is not None:
        st["research"]["prompts"] = prompts


def _default_assignments(eid: str) -> list[dict[str, str]]:
    out = []
    for p in _specs(eid):
        meta, _ = artifacts.read(p)
        out.append({"item_id": meta["item_id"], "owner": meta["owner"], "meeting": ""})
    return out


# --------------------------------------------------------------------------- dispatch


def _dispatch(runner: Runner, on_event: Callable[[str], None], task: Task,
              outputs: list[tuple[str | Path, str]]) -> None:
    """Run one agent, validate its artifacts plus handoff, retry once with the errors."""
    eid = task.eid
    checks = list(outputs) + [(_from_root(task.handoff), "handoff")]
    for attempt in (1, 2):
        log(eid, ACTOR, "dispatched", task.stage, agent=task.agent, attempt=attempt, **_extra(task))
        res = runner.run(task, on_event)
        if not res.ok:
            log(eid, task.agent, "agent_failed", task.stage, error=res.error)
            if attempt == 2:
                raise Blocked(f"{task.agent} failed: {res.error}")
            continue
        problems = _problems(eid, checks)
        detail = {"cost_usd": res.cost_usd} if res.cost_usd is not None else {}
        if not problems:
            log(eid, task.agent, "agent_done", task.stage, **detail)
            log(eid, ACTOR, "validated", task.stage, files=[_short(eid, p) for p, _ in checks])
            return
        log(eid, ACTOR, "invalid", task.stage, agent=task.agent, problems=problems[:20])
        if attempt == 2:
            raise Blocked(f"{task.agent} output does not validate: " + "; ".join(problems[:5]))
        task.notes = (task.notes + "\n\n" if task.notes else "") + "Your previous output failed validation. Fix exactly these problems:\n" + "\n".join(f"- {p}" for p in problems)


def _problems(eid: str, checks: list[tuple[str | Path, str]]) -> list[str]:
    out = []
    for p, schema in checks:
        path = Path(p) if Path(p).is_absolute() else edir(eid) / p
        out += artifacts.validate_file(path, schema)
    return out


def _check(eid: str, checks: list[tuple[str, str]]) -> None:
    problems = _problems(eid, checks)
    if problems:
        raise Blocked("; ".join(problems))


def _check_refs(eid: str, path: Path, field: str, target: Path) -> None:
    meta, _ = artifacts.read(path)
    tmeta, _ = artifacts.read(target)
    ids = refs._ids(tmeta)
    items = meta.get("questions") if field == "gap_ref" else meta.get("answers")
    bad = sorted({i[field] for i in items or [] if i.get(field) not in ids})
    if bad:
        raise Blocked(f"{path.name}: {field} values do not exist in {target.name}: {', '.join(bad)}")


# --------------------------------------------------------------------------- helpers


def _e(eid: str, rel: str) -> str:
    return state.agent_path(edir(eid) / rel)


def _rel(p: Path) -> str:
    return state.agent_path(p)


def _short(eid: str, p: str | Path) -> str:
    path = Path(p)
    try:
        return str(path.relative_to(edir(eid)))
    except ValueError:
        return state.agent_path(path) if path.is_absolute() else str(path)


def _from_root(p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else state.config.ROOT / path


def _extra(task: Task) -> dict[str, Any]:
    x = dict(task.extra)
    if "item" in x:
        x["item"] = x["item"]["item_id"]
    return x


def input_files(eid: str, kind: str) -> list[Path]:
    d = edir(eid) / "00-inputs" / kind
    return sorted(p for p in d.glob("*") if p.is_file() and not p.name.startswith(".")) if d.exists() else []


def _intake_files(eid: str) -> dict[str, list[Path]]:
    base = edir(eid) / "00-inputs"
    out: dict[str, list[Path]] = {"transcripts": [], "documents": []}
    for k in out:
        out[k] = input_files(eid, k)
    # Loose files dropped straight into 00-inputs/: transcripts by extension, the rest are documents.
    for p in sorted(base.glob("*")):
        if p.is_file() and not p.name.startswith("."):
            is_t = p.suffix.lower() in (".vtt", ".srt") or "transcript" in p.name.lower()
            out["transcripts" if is_t else "documents"].append(p)
    return out


def _signals(eid: str) -> list[str]:
    return [_e(eid, f) for f in ("01-intake/signal-voice.md", "01-intake/signal-docs.md") if (edir(eid) / f).exists()]


def _research_files(eid: str) -> list[str]:
    out = []
    for rd in sorted((edir(eid) / "02-research").glob("round-*")):
        out += [_rel(rd / f) for f in ("secondary.md", "questions.md", "responses.md") if (rd / f).exists()]
    return out


def _specs(eid: str) -> list[Path]:
    out: list[Path] = []
    for t in SPEC_DIRS.values():
        out += [p for p in sorted((edir(eid) / "05-delegation" / t).glob("x*.md")) if not p.name.endswith(".handoff.md")]
    return out


def _runlog(eid: str) -> Callable[[str], None]:
    """Agent progress streams into run.log while it works; nobody has to watch it."""
    path = edir(eid) / "run.log"

    def write(line: str) -> None:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{now()} {line.strip()}\n")

    return write


def reset_inputs_dir(eid: str, kind: str) -> None:
    shutil.rmtree(edir(eid) / "00-inputs" / kind, ignore_errors=True)
    (edir(eid) / "00-inputs" / kind).mkdir(parents=True, exist_ok=True)
