"""The conversation: turns the engine's event log into plain-language messages, output
cards and decision cards, and works out the one thing to do now.

The interface never shows system words (gate, schema, agent, stage). Internal stage numbers
map to plain step names; decisions map to buttons that name their outcome.
"""

from __future__ import annotations

import datetime as dt
import time
from pathlib import Path
from typing import Any

from loom import artifacts, config, state
from loom.orchestrator import input_files

STEPS = {
    1: ("Understand", "I read what you gave me and pull out what matters."),
    2: ("Research", "I look for context, then draft questions for your team."),
    3: ("Frame", "I sort the problems: settle by email, or solve in the room."),
    4: ("Workshop", "I design the session; afterwards, you upload what came out."),
    5: ("Hand over", "I turn each action into a spec someone can own."),
    6: ("Track", "I set targets and follow whether the change lands."),
}

DOING = {
    "intake-voice": "Reading the transcripts",
    "intake-docs": "Reading the documents",
    "research-secondary": "Searching past engagements, then the web",
    "research-primary": "Drafting questions for your team",
    "framing": "Sorting the problems",
    "workshop-designer": "Designing the session",
    "workshop-capture": "Reading what came out of the room",
    "delegation": "Sorting the workshop actions",
    "spec-decision": "Writing a decision spec",
    "spec-process": "Writing a process spec",
    "spec-tool": "Writing a tool spec",
    "tracking": "Setting targets and checking progress",
    "archivist": "Writing the archive entry",
}

CHALLENGE_TYPES = ["Operating model", "Customer experience", "Employee experience", "Digital product",
                   "Strategy", "AI adoption", "Process", "Culture"]


# ------------------------------------------------------------------ files in plain words


def file_title(rel: str) -> str:
    name = Path(rel).name
    titles = {
        "signal-voice.md": "What was said in the meetings",
        "signal-docs.md": "What the documents say",
        "brief.md": "The brief",
        "secondary.md": "What is already known",
        "questions.md": "Questions for the team",
        "responses.md": "Replies from the team",
        "problems.md": "The problems, sorted",
        "offline-drafts.md": "Draft messages for the email problems",
        "offline-log.md": "Agreements reached by email",
        "agenda.md": "Workshop session design",
        "facilitation-guide.md": "Facilitation guide",
        "materials.md": "Materials list",
        "outcomes.md": "What came out of the room",
        "routing.md": "Who handles which action",
    }
    if name in titles:
        t = titles[name]
        if "round-" in rel:
            t += f" (round {int(rel.split('round-')[1][:2])})"
        return t
    if "/decisions/" in rel:
        return f"Decision spec {Path(rel).stem}"
    if "/processes/" in rel:
        return f"Process spec {Path(rel).stem}"
    if "/tools/" in rel:
        return f"Tool spec {Path(rel).stem}"
    if rel.startswith("06-tracking/"):
        return f"Targets for {Path(rel).stem}"
    if "archive" in rel:
        return "Archive entry"
    return name


def summary(path: Path) -> str:
    """One or two lines on what a file holds, from its frontmatter."""
    try:
        m, _ = artifacts.read(path)
    except (artifacts.ArtifactError, OSError):
        return ""
    a = m.get("artifact")
    n = lambda k: len(m.get(k) or [])  # noqa: E731
    if a == "signal-voice":
        low = sum(1 for c in m.get("claims") or [] if c.get("confidence", 1) < 0.5)
        return (f"{pl(n('claims'), 'point')} from {pl(n('speakers'), 'speaker') if n('speakers') else 'unnamed speakers'}. "
                f"{pl(n('uncertain'), 'passage')} I couldn't hear clearly" + (f", {pl(low, 'point')} I'm unsure of." if low else "."))
    if a == "signal-docs":
        return f"{pl(n('claims'), 'point')} from {pl(n('sources'), 'document')}, {pl(n('diagrams'), 'visual')} described."
    if a == "brief":
        d = m.get("research_direction") or "not set yet"
        return f"{m.get('challenge', '')} · Research direction: {d}"
    if a == "secondary":
        arch = sum(1 for f in m.get("findings") or [] if f.get("from_archive"))
        return f"{pl(n('findings'), 'finding')} ({arch} from past engagements). {pl(n('gaps'), 'thing')} only your team can answer."
    if a == "questions":
        return f"{pl(n('questions'), 'question')} for {m.get('audience', 'the team')}, by {m.get('channel', 'survey')}."
    if a == "responses":
        return f"{pl(m.get('respondents', 0), 'person', 'people')} replied, {pl(n('answers'), 'answer')}."
    if a == "problems":
        return f"{n('offline')} to settle by email, {n('cocreate')} to solve in the room."
    if a == "agenda":
        return f"A {m.get('duration', '')} session: Reframe, Explore, Shape, covering {pl(n('problems_in_scope'), 'problem')}."
    if a == "outcomes":
        return f"{pl(n('decisions'), 'decision')} and {pl(n('actions'), 'action')} from the room."
    if a == "routing":
        return f"{pl(n('items'), 'action')}, each with one owner."
    if a == "spec":
        return f"{m.get('definition_of_done', '')} Owner: {m.get('owner', '')}."
    if a == "tracking":
        kpis = m.get("kpis") or []
        measured = sum(1 for k in kpis if k.get("current") not in (None, ""))
        first = kpis[0]["metric"] if kpis else ""
        first = first if len(first) <= 90 else first[:87].rstrip() + "…"
        return (f"{_status(m.get('status'))} · {pl(len(kpis), 'target')}, {measured} with a reading so far."
                + (f" First: {first}" if first else ""))
    if a == "archive":
        return f"Archived as {m.get('challenge_type')} in {m.get('sector')}. {len(m.get('lessons') or [])} lessons kept."
    return ""


def pl(k: int, word: str, plural: str | None = None) -> str:
    return f"{k} {word if k == 1 else (plural or word + 's')}"


def _status(s: str | None) -> str:
    return {"not_started": "Not started", "in_progress": "In progress", "adopted": "Adopted", "at_risk": "At risk",
            "stalled": "Stalled", "met": "Target met"}.get(s or "", s or "")


def handoff_for(eid: str, rel: str) -> dict[str, Any]:
    """The 'unsure' and 'please check' notes that came with a file."""
    d = state.edir(eid)
    p = d / rel
    candidates = [p.with_suffix(".handoff.md")] + sorted(p.parent.glob("handoff-*.md"))
    for c in candidates:
        if not c.exists():
            continue
        try:
            m, _ = artifacts.read(c)
        except artifacts.ArtifactError:
            continue
        produced = " ".join(m.get("produced") or [])
        if rel in produced or c.name.endswith(".handoff.md"):
            return {"unsure": m.get("unsure") or [], "check": m.get("check") or []}
    return {"unsure": [], "check": []}


# ------------------------------------------------------------------ the thread


def thread(eid: str, st: dict[str, Any], jobs: list[dict[str, Any]], last_seen: str | None) -> list[dict[str, Any]]:
    d = state.edir(eid)
    items: list[dict[str, Any]] = []
    seen_steps: set[int] = set()
    divider_done = last_seen is None

    def add(item: dict[str, Any], ts: str) -> None:
        nonlocal divider_done
        if not divider_done and ts > last_seen:
            items.append({"kind": "divider", "text": "Since you were last here"})
            divider_done = True
        item["ts"] = ts
        items.append(item)

    events = state.read_log(eid)
    items.append({"kind": "understood", "st": st, "ts": st["created_at"],
                  "inputs": [p.name for k in ("transcripts", "documents") for p in input_files(eid, k)]})
    pending_inputs: list[str] = []
    after_wait, last_decision = False, None
    for i, e in enumerate(events):
        if e["event"] == "wait_ended":
            after_wait = True
        if e["event"] == "gate_decision":
            last_decision = (e.get("detail") or {}).get("decision")
        ev, det, ts = e["event"], e.get("detail") or {}, e["ts"]
        if ev == "inputs" and det.get("kind") != "notes":
            pending_inputs.append(det.get("file", ""))
            nxt = events[i + 1] if i + 1 < len(events) else None
            if not nxt or nxt["event"] != "inputs":
                add({"kind": "you", "text": "Added " + ", ".join(pending_inputs)}, ts)
                pending_inputs = []
        elif ev == "message":
            add({"kind": "you", "text": det.get("text", "")}, ts)
        elif ev == "reply":
            add({"kind": "loom", "text": det.get("text", "")}, ts)
        elif ev == "stage" and det.get("status") == "running":
            n = e.get("stage", 1)
            name, line = STEPS[n]
            if n not in seen_steps:
                add({"kind": "step", "n": n, "name": name, "text": line, "anchor": f"step-{n}"}, ts)
            elif after_wait:
                add({"kind": "step", "n": n, "name": name, "text": {
                    2: "Next research round, using the team's replies.",
                    4: "Reading what came out of the room.",
                    6: "Checking progress against the targets."}.get(n, line)}, ts)
            elif last_decision == "another_round":
                add({"kind": "step", "n": n, "name": name, "text": "Another research round, on what you asked for."}, ts)
            else:
                add({"kind": "step", "n": n, "name": name, "text": f"Running {name} again with your notes."}, ts)
            seen_steps.add(n)
            after_wait, last_decision = False, None
        elif ev == "merged":
            add(_card(eid, det.get("file", "01-intake/brief.md")), ts)
        elif ev == "validated":
            for f in det.get("files") or []:
                if "handoff" in Path(f).name:
                    continue
                add(_card(eid, f), ts)
        elif ev == "gate_decision" and e["actor"] != "orchestrator":
            add({"kind": "you", "text": choice_label(det.get("gate"), det.get("decision"), det.get("target_stage")),
                 "note": det.get("notes", "")}, ts)
        elif ev == "invalid" and e["actor"] not in ("orchestrator",):
            add({"kind": "loom", "text": "I couldn't do that yet: " + plain(det.get("reason", ""))}, ts)
        elif ev == "waiting":
            add({"kind": "loom", "text": waiting_text(det)}, ts)
        elif ev == "wait_ended":
            add({"kind": "loom", "text": {"responses": "Thanks, I have the replies. Starting the next research round.",
                                          "workshop": "Thanks, I have what came out of the room. Reading it now.",
                                          "adoption": "Thanks for the updates. Checking progress against the targets."}
                 .get(det.get("kind"), "The wait is over. Carrying on.")}, ts)
        elif ev == "closed":
            add({"kind": "loom", "text": "This engagement is closed. Everything is in the archive, so the next one starts warm."}, ts)
        elif ev == "note" and e["actor"] not in ("orchestrator",) and det.get("msg"):
            add({"kind": "quiet", "text": det["msg"]}, ts)

    # Live tail: what is happening right now.
    now_ts = state.now()
    for j in jobs:
        if j["op"] == "decide" and j["where"] == "pending":
            a = j["args"]
            left = max(0, int(j.get("not_before", 0) - time.time()))
            add({"kind": "you", "text": choice_label((st.get("gate") or {}).get("gate") or (5 if st["stage"] == 6 else None),
                                                     a.get("decision"), a.get("target_stage")),
                 "note": a.get("notes", ""), "undo": j["id"], "left": left}, now_ts)
        elif j["op"] == "ask":
            add({"kind": "typing"}, now_ts)
    running = [j for j in jobs if j["op"] in ("advance", "decide", "inputs") and (j["where"] == "running" or j.get("not_before", 0) <= time.time())]
    if running or st["status"] == "running":
        active = active_runs(eid, events) if st["status"] == "running" else []
        if not active:
            add({"kind": "progress", "text": "Getting started…"}, now_ts)
        for r in active:
            add({"kind": "progress", "text": r["doing"] + "…", "activity": r["activity"], "elapsed": r["elapsed"]}, now_ts)
    elif not any(j["op"] == "decide" and j["where"] == "pending" for j in jobs):
        card = decision_card(eid, st)
        if card:
            add(card, now_ts)
    return items


def active_runs(eid: str, events: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Agents working right now, with the latest thing each one did, in plain words."""
    open_runs: dict[str, dict[str, Any]] = {}
    for e in events:
        det = e.get("detail") or {}
        if e["event"] == "dispatched":
            key = det.get("agent", "") + (f":{det['item']}" if det.get("item") else "")
            open_runs[key] = e
        elif e["event"] in ("agent_done", "agent_failed"):
            key = e["actor"] + (f":{det['item']}" if det.get("item") else "")
            open_runs.pop(key, None)
        elif e["event"] in ("gate_opened", "waiting", "closed"):
            open_runs.clear()
    if not open_runs:
        return []
    lines = _tail(state.edir(eid) / "run.log", 400)
    out = []
    for key, e in open_runs.items():
        agent, _, item = key.partition(":")
        doing = DOING.get(agent, "Working") + (f" for {item}" if item else "")
        latest = next((ln for ln in reversed(lines) if f"[{key}] " in ln and ln[:25] >= e["ts"][:19]), "")
        out.append({"doing": doing, "activity": activity(latest.split(f"[{key}] ", 1)[-1]) if latest else "",
                    "elapsed": _elapsed(e["ts"])})
    return out


def activity(line: str) -> str:
    """Turn a tool call line from the run log into a short plain sentence."""
    line = line.strip()
    if not line:
        return ""
    verb, _, rest = line.partition(" ")
    if verb == "Read" and ("schemas/" in rest or rest.endswith(".json")):
        return "Checking the expected format"
    if verb == "Read" and rest.endswith("SKILL.md"):
        return "Reviewing its method"
    if verb == "Read" and "/00-inputs/" in rest:
        return f"Reading {Path(rest).name}"
    name = Path(rest).name if "/" in rest else rest
    pretty = file_title(rest) if rest.endswith(".md") else name
    return {
        "Read": f"Reading {pretty}", "Write": f"Writing {pretty}", "Edit": f"Revising {pretty}",
        "MultiEdit": f"Revising {pretty}", "Grep": "Searching the files", "Glob": "Looking through the files",
        "WebSearch": f"Searching the web: {rest[:90]}", "WebFetch": f"Opening {rest.split('/')[2] if rest.startswith('http') else rest[:60]}",
    }.get(verb, "Thinking it through")


def _elapsed(ts: str) -> str:
    try:
        secs = int((dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(ts)).total_seconds())
    except ValueError:
        return ""
    return f"{secs // 60}m {secs % 60:02d}s" if secs >= 60 else f"{secs}s"


def _tail(p: Path, n: int) -> list[str]:
    try:
        with open(p, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 120 * n))
            return fh.read().decode("utf-8", "replace").splitlines()[-n:]
    except OSError:
        return []


def usage(eid: str) -> float:
    return round(sum((e.get("detail") or {}).get("cost_usd") or 0 for e in state.read_log(eid)), 2)


def _card(eid: str, f: str) -> dict[str, Any]:
    d = state.edir(eid)
    p = Path(f) if Path(f).is_absolute() else (d / f if (d / f).exists() else config.ROOT / f)
    rel = str(p.relative_to(d)) if str(p).startswith(str(d)) else f"archive/{p.name}"
    return {"kind": "output", "title": file_title(rel), "summary": summary(p), "path": rel,
            "notes": handoff_for(eid, rel) if str(p).startswith(str(d)) else {"unsure": [], "check": []}}


def waiting_text(det: dict[str, Any]) -> str:
    k = det.get("kind")
    if k == "responses":
        return (f"Over to you: send the {det.get('questions', '')} questions to {det.get('audience', 'the team')} "
                f"by {det.get('channel', 'survey')}. I'll wait for the replies, however long it takes.")
    if k == "workshop":
        return "The session is ready. I'll wait while you run the workshop. Afterwards, upload photos of the walls, notes and the transcript."
    if k == "adoption":
        return (f"The specs are with their owners. {det.get('met', 0)} of {det.get('items', 0)} targets met so far. "
                "Upload updates from the owners as they arrive and I'll check progress.")
    return "Waiting on the organisation."


def plain(text: str) -> str:
    """Engine messages can carry system words; keep the gist, drop the jargon."""
    t = text
    for a, b in (("research_direction in 01-intake/brief.md", "a research direction"),
                 ("(and fix any schema problems) ", ""), ("schema", "format"), ("gate", "step"),
                 ("stage", "step"), ("agent", "step"), ("frontmatter", "header")):
        t = t.replace(a, b)
    return t


# ------------------------------------------------------------------ decisions


def choice_label(gate: int | None, decision: str | None, target: int | None = None) -> str:
    labels = {
        (1, "approve"): "Research this direction",
        (1, "revise"): "Read it again with my notes",
        (2, "send"): "Send questions",
        (2, "another_round"): "Dig deeper",
        (2, "revise"): "Change the questions",
        (2, "approve"): "Enough research, frame it",
        (2, "reject"): "Redirect",
        (3, "approve"): "The session is ready",
        (3, "revise"): "Adjust the session",
        (3, "reject"): "Sort the problems again",
        (4, "approve"): "Record the hand-over",
        (4, "revise"): "Rewrite the specs",
        (4, "reject"): "Re-read the workshop output",
        (5, "close"): "Close and archive",
        (5, "restart"): "Start a follow-up engagement",
        (5, "revise"): "Check progress again",
    }
    if decision == "reject" and target:
        return f"Go back to {STEPS[target][0]}"
    return labels.get((gate or 0, decision or ""), (decision or "").replace("_", " ").capitalize())


def decision_card(eid: str, st: dict[str, Any]) -> dict[str, Any] | None:
    """The live card at the end of the thread, or None when nothing needs the person."""
    if st["closed"]:
        return None
    g = st.get("gate")
    w = st.get("waiting") or {}
    d = state.edir(eid)

    if st["stage"] == 1 and st["status"] == "queued":
        files = [p.name for k in ("transcripts", "documents") for p in input_files(eid, k)]
        if not files:
            return {"kind": "decision", "id": "inputs", "question": "What do you have? Drop transcripts, documents or diagrams below.",
                    "buttons": [], "why": "I need at least one transcript or document to understand the challenge."}
        return {"kind": "decision", "id": "start", "question": f"I have {len(files)} file{'s' if len(files) > 1 else ''}. Ready to start?",
                "buttons": [btn("Start", "start", "I'll read everything and come back with a brief.", primary=True)],
                "why": "Nothing happens until you start, so you can add more files first."}

    if g and g.get("error"):
        n = st["stage"]
        b = [btn("Try again", "revise", f"I'll run {STEPS[n][0]} again, with your notes if you add any.", primary=True)]
        if n > 1:
            b.append(btn(f"Go back to {STEPS[n - 1][0]}", "reject", f"I'll redo {STEPS[n - 1][0]} and come forward again.", target=n - 1))
        return {"kind": "decision", "id": "blocked", "question": f"Something went wrong in {STEPS[n][0]}. Want me to try again?",
                "detail": plain(g["error"]), "buttons": b, "refused": plain(g.get("refused") or ""),
                "why": "A step produced something that didn't pass my checks, twice. I stopped rather than pass on bad work."}

    if g and st["status"] == "awaiting_gate":
        gate = g["gate"]
        refused = plain(g.get("refused") or "")
        if gate == 1:
            brief, _ = artifacts.read(d / "01-intake/brief.md")
            has_dir = bool(str(brief.get("research_direction", "")).strip())
            return {"kind": "decision", "id": "g1", "refused": refused,
                    "question": "Is this the right picture? Tell me where research should point.",
                    "hint": "Type the direction below, for example: how peers onboard in weeks, and where our handovers break.",
                    "buttons": [btn("Research this direction", "approve", "I'll search past engagements and the web, then draft questions for your team.",
                                    primary=True, needs_note=not has_dir, note_as="direction"),
                                btn("Read it again", "revise", "I'll re-read everything with your notes and redo the brief.", needs_note=True)],
                    "why": "Research is only as good as where it points. You know the client; I only know the files."}
        if gate == 2:
            r = st["research"]["round"]
            return {"kind": "decision", "id": "g2", "refused": refused,
                    "question": f"Round {r} is done. Send these questions, or dig further first?",
                    "buttons": [btn("Send questions", "send", "You send them; I wait for replies and use them in the next round.", primary=True),
                                btn("Dig deeper", "another_round", "I'll run another round on what you type below, before asking anyone.", needs_note=True),
                                btn("Change the questions", "revise", "I'll redo this round's questions with your notes.", needs_note=True),
                                btn("Enough research, frame it", "approve", "I'll stop researching and sort the problems.")],
                    "more": [btn("Redirect", "reject", "I'll go back to Understand and redo the brief with your notes.", needs_note=True, target=1)],
                    "why": "Nobody can tell when research is enough except you. I'll keep going until you call it."}
        if gate == 3:
            return {"kind": "decision", "id": "g3", "refused": refused,
                    "question": "Here is how I sorted the problems and the session I designed. Ready to run it?",
                    "buttons": [btn("The session is ready", "approve", "I'll wait while you run the workshop, then read what you bring back.", primary=True),
                                btn("Adjust the session", "revise", "I'll redesign the session with your notes.", needs_note=True),
                                btn("Sort the problems again", "reject", "I'll redo Frame with your notes, then redesign the session.", needs_note=True, target=3)],
                    "why": "The split decides who is in the room. Once the workshop runs, it's hard to undo."}
        if gate == 4:
            items = []
            for t in ("decisions", "processes", "tools"):
                for p in sorted((d / "05-delegation" / t).glob("x*.md")):
                    if p.name.endswith(".handoff.md"):
                        continue
                    m, _ = artifacts.read(p)
                    items.append({"item_id": m["item_id"], "owner": m["owner"], "type": m["type"],
                                  "dod": m["definition_of_done"], "path": str(p.relative_to(d))})
            return {"kind": "decision", "id": "g4", "refused": refused, "handover": items,
                    "question": "Each action is now a spec. Who owns which, and when will you meet them?",
                    "buttons": [btn("Record the hand-over", "approve", "I'll set targets for each spec and follow whether the change lands.", primary=True),
                                btn("Rewrite the specs", "revise", "I'll rewrite the specs with your notes.", needs_note=True)],
                    "more": [btn("Re-read the workshop output", "reject", "I'll read what came out of the room again and redo the specs.", target=4)],
                    "why": "I write the specs; explaining them to the owners is yours. Recording the meetings lets me track them."}
        if gate == 5:
            return close_card(refused, met=True)

    if st["status"] == "waiting_org":
        k = w.get("kind")
        if k == "responses":
            n = len(input_files(eid, "responses"))
            return {"kind": "decision", "id": "responses", "wait": True,
                    "question": "Paste or upload the replies below as they come in. " + (
                        "None yet." if not n else f"{n} {'batch' if n == 1 else 'batches'} of replies so far."),
                    "questions_path": f"02-research/round-{st['research']['round']:02d}/questions.md",
                    "buttons": [btn("That's all the replies, continue", "continue:responses",
                                    "I'll organise the replies and start the next research round.", primary=n > 0)],
                    "why": "Loom never contacts anyone. You send the questions; I use what comes back."}
        if k == "workshop":
            n = len(input_files(eid, "workshop"))
            return {"kind": "decision", "id": "workshop", "wait": True,
                    "question": "After the workshop, upload photos, notes and the transcript below. " + (
                        "Nothing yet." if not n else f"{n} file{'s' if n > 1 else ''} so far."),
                    "buttons": [btn("That's everything from the room", "continue:workshop",
                                    "I'll read it and turn every action into a spec.", primary=n > 0)],
                    "why": "People run the workshop, not Loom. What comes out of the room is what I work from next."}
        if k == "adoption":
            card = close_card("", met=False)
            card["buttons"].insert(0, btn("Updates added, check progress", "continue:tracking",
                                          "I'll compare the updates against each target.", primary=True))
            card["question"] = "Upload updates from the owners below. When the targets land, close it or start a follow-up."
            card["wait"] = True
            return card
    return None


def close_card(refused: str, met: bool) -> dict[str, Any]:
    return {"kind": "decision", "id": "close", "refused": refused,
            "question": "Every target is met. Close it, or start a follow-up?" if met else "",
            "buttons": [btn("Close and archive", "close", "I'll write the archive entry. The engagement becomes read-only.",
                            primary=met, confirm="Close this engagement and archive it? This can't be undone once it starts."),
                        btn("Start a follow-up engagement", "restart", "I'll archive this one and open a new engagement from your notes.",
                            needs_note=True, confirm="Archive this engagement and start a follow-up?")],
            "why": "When the change has landed, new problems usually follow. A follow-up starts with everything learned here."}


def btn(label: str, decision: str, next_line: str, primary: bool = False, needs_note: bool = False,
        target: int | None = None, confirm: str | None = None, note_as: str = "notes") -> dict[str, Any]:
    return {"label": label, "decision": decision, "next": next_line, "primary": primary, "needs_note": needs_note,
            "target": target, "confirm": confirm, "note_as": note_as}


# ------------------------------------------------------------------ next-step bar


def next_step(eid: str, st: dict[str, Any], jobs: list[dict[str, Any]]) -> dict[str, str]:
    pend = [j for j in jobs if j["op"] == "decide" and j["where"] == "pending" and j.get("not_before", 0) > time.time()]
    if pend:
        left = max(1, int(pend[-1]["not_before"] - time.time()))
        return {"tone": "info", "text": f"Starting in {left}s. Undo if you changed your mind."}
    if st["closed"]:
        return {"tone": "done", "text": "This engagement is closed and archived. Nothing needs you."}
    busy = any(j["op"] in ("advance", "decide", "inputs") for j in jobs) or st["status"] in ("running",)
    if busy:
        return {"tone": "busy", "text": f"Nothing needs you. I'm working on {STEPS[st['stage']][0]}; you'll get a notification when it's your turn."}
    card = decision_card(eid, st)
    w = (st.get("waiting") or {}).get("kind")
    if w == "responses":
        return {"tone": "wait", "text": "I'm waiting on replies from the team. Paste or upload them below when they arrive."}
    if w == "workshop":
        return {"tone": "wait", "text": "Run the workshop. Afterwards, upload what came out of the room below."}
    if w == "adoption":
        return {"tone": "wait", "text": "I'm waiting on updates from the spec owners. Upload them below when they arrive."}
    if card:
        primary = next((b for b in card["buttons"] if b["primary"]), None)
        if card["id"] == "inputs":
            return {"tone": "you", "text": "Drop in transcripts or documents to start."}
        if card["id"] == "g1":
            return {"tone": "you", "text": "Your turn: check the brief and tell me where research should point."}
        return {"tone": "you", "text": "Your turn: " + (card["question"] or (primary or {}).get("label", "decide"))}
    return {"tone": "info", "text": "Nothing needs you right now."}


# ------------------------------------------------------------------ navigation


def group(st: dict[str, Any], jobs: list[dict[str, Any]]) -> str:
    if st["closed"]:
        return "Done"
    if any(j["op"] in ("advance", "decide", "inputs") for j in jobs) or st["status"] == "running":
        return "Running"
    if st["status"] == "awaiting_gate" or (st["stage"] == 1 and st["status"] == "queued"):
        return "Needs you"
    if st["status"] == "waiting_org":
        return "Waiting on the org"
    return "Running"


def ago(ts: str) -> str:
    try:
        delta = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(ts)
    except ValueError:
        return ""
    if delta.days:
        return f"{delta.days}d"
    return f"{delta.seconds // 3600}h" if delta.seconds >= 3600 else f"{max(1, delta.seconds // 60)}m"
