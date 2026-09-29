"""state.json and log.jsonl: the only files the orchestrator writes.

state.json is rewritten atomically; log.jsonl is append-only, so an engagement can be
replayed or resumed after days away.
"""

from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from . import artifacts, config

STAGES = {
    1: ("intake", "01-intake"),
    2: ("research", "02-research"),
    3: ("framing", "03-framing"),
    4: ("workshop", "04-workshop"),
    5: ("delegation", "05-delegation"),
    6: ("tracking", "06-tracking"),
}

GATES = {
    1: "Input: is the signal right, and where should research point?",
    2: "Research: another round, curated questions out, or done?",
    3: "Workshop: is the problem split right, is the session ready to run?",
    4: "Delegation: which specs go to which owners, and when you meet them?",
    5: "Close: have the KPI goals landed? Close and archive, or restart with new problems.",
}

INPUT_KINDS = ("transcripts", "documents", "responses", "workshop", "tracking")


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:48] or "engagement"


def edir(eid: str) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]+", eid):
        raise ValueError(f"bad engagement id: {eid!r}")
    return config.ENGAGEMENTS_DIR / eid


def agent_path(p: Path) -> str:
    """How an agent (cwd = LOOM_ROOT) should refer to a path."""
    p = Path(p).resolve()
    try:
        return str(p.relative_to(config.ROOT))
    except ValueError:
        return str(p)


def round_dir(eid: str, n: int) -> Path:
    return edir(eid) / "02-research" / f"round-{n:02d}"


@contextmanager
def locked(eid: str) -> Iterator[None]:
    """One writer per engagement at a time (app, worker and CLI may race)."""
    d = edir(eid)
    d.mkdir(parents=True, exist_ok=True)
    with open(d / ".lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def load(eid: str) -> dict[str, Any]:
    return json.loads((edir(eid) / "state.json").read_text())


def save(st: dict[str, Any]) -> None:
    st["updated_at"] = now()
    errs = artifacts.errors_for(st, "state")
    if errs:
        raise ValueError("state.json would be invalid: " + "; ".join(errs))
    path = edir(st["id"]) / "state.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(st, indent=2) + "\n")
    os.replace(tmp, path)


def log(eid: str, actor: str, event: str, stage: int | None = None, **detail: Any) -> None:
    line: dict[str, Any] = {"ts": now(), "actor": actor, "event": event}
    if stage is not None:
        line["stage"] = stage
    if detail:
        line["detail"] = detail
    with open(edir(eid) / "log.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, ensure_ascii=False) + "\n")


def read_log(eid: str) -> list[dict[str, Any]]:
    p = edir(eid) / "log.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def new_state(eid: str, title: str, intent: str, sponsor: str = "", client: str = "",
              sector: str = "", parent: str | None = None) -> dict[str, Any]:
    t = now()
    return {
        "id": eid,
        "title": title,
        "intent": intent,
        "sponsor": sponsor,
        "client": client,
        "sector": sector,
        "parent": parent,
        "created_at": t,
        "updated_at": t,
        "stage": 1,
        "status": "queued",
        "closed": False,
        "stages": {str(i): {"status": "queued"} for i in STAGES},
        "research": {"round": 1, "prompts": []},
        "gate": None,
        "waiting": None,
        "notes": "",
        "pending_decision": None,
        "decisions": [],
        "assignments": [],
    }


def set_status(st: dict[str, Any], status: str, stage: int | None = None) -> None:
    stage = stage or st["stage"]
    entry = st["stages"][str(stage)]
    if status == "running" and entry.get("status") != "running":
        entry["started_at"] = now()
    if status == "done":
        entry["finished_at"] = now()
    entry["status"] = status
    if stage == st["stage"]:
        st["status"] = status


def list_ids() -> list[str]:
    if not config.ENGAGEMENTS_DIR.exists():
        return []
    return sorted(p.name for p in config.ENGAGEMENTS_DIR.iterdir() if (p / "state.json").exists())


def validate_engagement(eid: str) -> list[str]:
    """The small validator: state.json, every log line, and every artifact on disk."""
    problems = []
    d = edir(eid)
    try:
        st = load(eid)
        problems += [f"state.json: {m}" for m in artifacts.errors_for(st, "state")]
    except Exception as e:  # noqa: BLE001 - report, don't crash the validator
        return [f"state.json: {e}"]
    for i, line in enumerate((d / "log.jsonl").read_text().splitlines() if (d / "log.jsonl").exists() else [], 1):
        try:
            problems += [f"log.jsonl:{i}: {m}" for m in artifacts.errors_for(json.loads(line), "log-event")]
        except json.JSONDecodeError as e:
            problems.append(f"log.jsonl:{i}: not JSON ({e})")
    for path, schema_name in artifact_map(eid):
        problems += [f"{path.relative_to(d)}: {m.split(': ', 1)[-1]}" for m in artifacts.validate_file(path, schema_name)]
    return problems


def artifact_map(eid: str) -> list[tuple[Path, str]]:
    """Every contract artifact currently on disk, paired with its schema."""
    d = edir(eid)
    fixed = {
        "01-intake/signal-voice.md": "signal-voice",
        "01-intake/signal-docs.md": "signal-docs",
        "01-intake/brief.md": "brief",
        "03-framing/problems.md": "problems",
        "04-workshop/design/agenda.md": "agenda",
        "04-workshop/capture/outcomes.md": "outcomes",
        "05-delegation/routing.md": "routing",
    }
    found = [(d / rel, s) for rel, s in fixed.items() if (d / rel).exists()]
    for rd in sorted((d / "02-research").glob("round-*")):
        for name in ("secondary", "questions", "responses"):
            if (rd / f"{name}.md").exists():
                found.append((rd / f"{name}.md", name))
    for t in ("decisions", "processes", "tools"):
        found += [(p, "spec") for p in sorted((d / "05-delegation" / t).glob("x*.md")) if not p.name.endswith(".handoff.md")]
    found += [(p, "tracking") for p in sorted((d / "06-tracking").glob("x*.md"))]
    found += [(p, "handoff") for p in sorted(d.rglob("handoff*.md"))]
    found += [(p, "handoff") for p in sorted(d.rglob("*.handoff.md"))]
    return found
