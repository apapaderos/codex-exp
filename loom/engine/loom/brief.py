"""The brief merge. The orchestrator writes no content, so this is a mechanical merge:
it copies what intake extracted and what the human typed at Start, and keeps any edits a
reviewer already made at gate 1. It never summarises or invents; research_direction is
left for the reviewer to set at gate 1."""

from __future__ import annotations

from typing import Any

from . import artifacts
from .state import edir


def merge(eid: str, st: dict[str, Any]) -> None:
    d = edir(eid)
    path = d / "01-intake/brief.md"
    old: dict[str, Any] = {}
    if path.exists():
        try:
            old, _ = artifacts.read(path)
        except artifacts.ArtifactError:
            old = {}

    voice = _meta(d / "01-intake/signal-voice.md")
    docs = _meta(d / "01-intake/signal-docs.md")

    stakeholders = list(old.get("stakeholders") or [])
    known = {s.get("name") for s in stakeholders}
    for sp in voice.get("speakers") or []:
        if sp.get("label") not in known:
            stakeholders.append({"name": sp["label"], "role": sp.get("role", "")})

    meta = {
        "artifact": "brief",
        "challenge": old.get("challenge") or st["intent"],
        "sponsor": old.get("sponsor") or st.get("sponsor", ""),
        "stakeholders": stakeholders,
        "constraints": old.get("constraints") or [],
        "research_direction": old.get("research_direction") or "",
        "signal_refs": [f for f in ("01-intake/signal-voice.md", "01-intake/signal-docs.md") if (d / f).exists()],
    }

    lines = [f"# Brief: {st['title']}", "", "## Intent (from Start)", "", st["intent"], ""]
    lines += ["## Claims from intake", "", "Low-confidence claims (below 0.5) are marked ⚠ and weigh less downstream.", ""]
    for c in voice.get("claims") or []:
        flag = " ⚠" if c.get("confidence", 1) < 0.5 else ""
        lines.append(f"- `signal-voice#{c['id']}`{flag} {c['text']} ({c.get('speaker') or 'unknown speaker'}, "
                     f"{c.get('timestamp') or 'no time'}, confidence {c.get('confidence')})")
    for c in docs.get("claims") or []:
        lines.append(f"- `signal-docs#{c['id']}` {c['text']} ({c['source_ref']})")
    if voice.get("uncertain"):
        lines += ["", "## Passages intake could not hear clearly", ""]
        lines += [f"- {u.get('timestamp', '')} \"{u['heard']}\": {u['why']}" for u in voice["uncertain"]]
    if docs.get("diagrams"):
        lines += ["", "## Diagrams", ""]
        lines += [f"- {g['source_ref']}: {g['description']}" for g in docs["diagrams"]]
    lines += [
        "",
        "## For the reviewer at gate 1",
        "",
        "Edit the frontmatter above: confirm the challenge and sponsor, add stakeholders and constraints, "
        "and set `research_direction` (where primary and secondary research should point). "
        "Gate 1 cannot be approved while research_direction is empty.",
    ]
    artifacts.write(path, meta, "\n".join(lines))


def _meta(path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return artifacts.read(path)[0]
    except artifacts.ArtifactError:
        return {}
