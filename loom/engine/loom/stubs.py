"""StubRunner: writes schema-valid placeholder artifacts with no API calls.

This is the phase 0 spine: a dummy engagement moves through all six stages, pausing at
every gate, so the orchestration can be tested before any real agent exists. Stubs derive
their content mechanically from the inputs (first lines, ids) and say so in their handoff.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Any, Callable

from . import artifacts, config
from .agents import Task
from .runners import RunResult, _abs
from .state import edir, round_dir

TODAY = dt.date.today()


def _in(days: int) -> str:
    return (TODAY + dt.timedelta(days=days)).isoformat()


class StubRunner:
    def run(self, task: Task, on_event: Callable[[str], None] | None = None) -> RunResult:
        fn = getattr(self, "_" + task.agent.replace("-", "_"))
        if on_event:
            on_event(f"[stub] {task.agent} {task.extra or ''}")
        produced = fn(task) or task.writes
        artifacts.write(Path(_abs(task.handoff)), {
            "artifact": "handoff", "agent": task.agent, "stage": task.stage,
            "produced": produced, "unsure": ["This is demo output: placeholder content, not real analysis."],
            "check": ["Switch Loom from demo mode to real mode (LOOM_RUNNER=sdk) to get real work."],
            "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        }, f"# Handoff from {task.agent} (stub)\n\nWrote {', '.join(produced)}.")
        return RunResult(ok=True, summary="stub", cost_usd=0.0)

    # ---- stage 1
    def _intake_voice(self, t: Task) -> None:
        claims, speakers, uncertain = [], {}, []
        for f in t.reads:
            for i, line in enumerate(_lines(f)[:8]):
                m = re.match(r"^\[?([\d:.]+)?\]?\s*([A-Z][\w .'-]{0,30}):\s*(.+)$", line)
                ts, spk, text = (m.group(1) or "", m.group(2), m.group(3)) if m else ("", "", line)
                if spk:
                    speakers[spk] = {"label": spk, "role": ""}
                conf = 0.4 if "inaudible" in text.lower() or "?" in text else 0.8
                claims.append({"id": f"c{len(claims) + 1}", "text": text[:300], "speaker": spk, "confidence": conf,
                               "timestamp": ts or f"line {i + 1}", "source": f})
                if conf < 0.5:
                    uncertain.append({"timestamp": ts or f"line {i + 1}", "heard": text[:120], "why": "marked inaudible or unclear"})
        artifacts.write(Path(_abs(t.writes[0])), {"artifact": "signal-voice", "sources": t.reads,
                        "speakers": list(speakers.values()), "claims": claims or _one_claim(t), "uncertain": uncertain},
                        "# Voice signal (stub)\n\nOne claim per transcript line, first lines only.")

    def _intake_docs(self, t: Task) -> None:
        sources, claims, diagrams = [], [], []
        for i, f in enumerate(t.reads, 1):
            kind = "diagram" if Path(f).suffix.lower() in (".png", ".jpg", ".jpeg", ".svg") else "document"
            sources.append({"ref": f"s{i}", "file": f, "kind": kind})
            if kind == "diagram":
                diagrams.append({"source_ref": f"s{i}", "description": "Image not read by the stub runner."})
                continue
            for line in _lines(f)[:4]:
                claims.append({"id": f"c{len(claims) + 1}", "text": line[:300], "source_ref": f"s{i} p1", "confidence": 0.9})
        artifacts.write(Path(_abs(t.writes[0])), {"artifact": "signal-docs", "sources": sources,
                        "claims": claims or [{"id": "c1", "text": "No text extracted.", "source_ref": "s1", "confidence": 0.1}],
                        "diagrams": diagrams}, "# Document signal (stub)")

    # ---- stage 2
    def _research_secondary(self, t: Task) -> None:
        n = t.extra["round"]
        findings = []
        for p in sorted(config.ARCHIVE_DIR.glob("*.md")):
            findings.append({"id": f"f{len(findings) + 1}", "text": f"Prior engagement on file: {p.stem}",
                             "source": f"archive/{p.name}", "from_archive": True, "confidence": 0.7})
        findings.append({"id": f"f{len(findings) + 1}", "text": "Placeholder web finding.", "source": "https://example.org",
                         "from_archive": False, "confidence": 0.3})
        gaps = [{"id": "g1", "text": f"Round {n}: how do people experience the problem today?", "why_it_matters": "stub"},
                {"id": "g2", "text": f"Round {n}: who owns the decision?", "why_it_matters": "stub"}]
        artifacts.write(Path(_abs(t.writes[0])), {"artifact": "secondary", "round": n, "findings": findings, "gaps": gaps},
                        "# Secondary research (stub)")

    def _research_primary(self, t: Task) -> None:
        n = t.extra["round"]
        if t.extra.get("mode") == "normalise":
            q = _meta(round_dir(t.eid, n) / "questions.md")
            raw = [line for f in t.reads[1:] for line in _lines(f)]
            answers = [{"id": f"a{i + 1}", "question_id": q["questions"][i % len(q["questions"])]["id"],
                        "respondent": f"r{i + 1}", "text": line[:500]} for i, line in enumerate(raw)]
            artifacts.write(Path(_abs(t.writes[0])), {"artifact": "responses", "round": n, "respondents": len(raw),
                            "answers": answers, "raw_files": t.reads[1:]}, "# Responses (stub normalise)")
            return
        sec = _meta(round_dir(t.eid, n) / "secondary.md")
        qs = [{"id": f"q{i + 1}", "text": f"Tell us about: {g['text']}", "gap_ref": g["id"], "format": "open"}
              for i, g in enumerate(sec["gaps"])]
        artifacts.write(Path(_abs(t.writes[0])), {"artifact": "questions", "round": n, "audience": "Team leads in scope",
                        "audience_size": 12, "channel": "survey", "questions": qs}, "# Questions (stub)")

    # ---- stage 3
    def _framing(self, t: Task) -> None:
        ref = "01-intake/signal-voice.md#c1" if (edir(t.eid) / "01-intake/signal-voice.md").exists() else "01-intake/signal-docs.md#c1"
        meta = {"artifact": "problems", "reframed_challenge": "Stub reframe.",
                "offline": [{"id": "p1", "problem": "Confirm the reporting format", "owner": "Sponsor", "evidence_refs": [ref],
                             "why": "One owner can decide; others only need informing."}],
                "cocreate": [{"id": "p2", "problem": "Agree how teams hand work over", "owner": "Sponsor + team leads",
                              "evidence_refs": [ref], "why": "Several parties hold part of the answer."}]}
        artifacts.write(Path(_abs(t.writes[0])), meta, "# Problems (stub)")
        Path(_abs(t.writes[1])).write_text("# Offline drafts (stub)\n\n## p1\n\nDraft message to the Sponsor.\n")

    # ---- stage 4
    def _workshop_designer(self, t: Task) -> None:
        p = _meta(edir(t.eid) / "03-framing/problems.md")
        meta = {"artifact": "agenda", "problems_in_scope": [x["id"] for x in p["cocreate"]], "duration": "3h30",
                "participants": [{"name": "Team leads", "role": "co-creators"}],
                "acts": [{"act": "Reframe", "minutes": 45, "purpose": "Agree the question", "activities": ["How might we"]},
                         {"act": "Explore", "minutes": 90, "purpose": "Generate options", "activities": ["Crazy 8s"]},
                         {"act": "Shape", "minutes": 60, "purpose": "Commit to actions", "activities": ["Action canvas"]}]}
        artifacts.write(Path(_abs(t.writes[0])), meta, "# Agenda (stub)")

    def _workshop_capture(self, t: Task) -> None:
        meta = {"artifact": "outcomes", "sources": t.reads[:1] or ["none"],
                "decisions": [{"id": "d1", "text": "Adopt a single handover template", "problem_ref": "p2"}],
                "actions": [{"id": "x1", "text": "Decide the template owner", "type": "decision", "owner": "Sponsor", "due": _in(14)},
                            {"id": "x2", "text": "Define the handover process", "type": "process", "owner": "Ops lead", "due": _in(30)},
                            {"id": "x3", "text": "Configure the tracker board", "type": "tool", "owner": "IT lead", "due": _in(45)}]}
        artifacts.write(Path(_abs(t.writes[0])), meta, "# Outcomes (stub)")

    # ---- stage 5
    def _delegation(self, t: Task) -> None:
        o = _meta(edir(t.eid) / "04-workshop/capture/outcomes.md")
        items = [{"item_id": a["id"], "from_action": a["id"], "type": a["type"], "agent": f"spec-{a['type']}",
                  "summary": a["text"], "owner": a["owner"], "due": str(a["due"])} for a in o["actions"]]
        artifacts.write(Path(_abs(t.writes[0])), {"artifact": "routing", "items": items}, "# Routing (stub)")

    def _spec(self, t: Task) -> None:
        item = t.extra["item"]
        artifacts.write(Path(_abs(t.writes[0])), {"artifact": "spec", "type": item["type"], "item_id": item["item_id"],
                        "owner": item["owner"], "definition_of_done": f"{item['summary']}: done and communicated.",
                        "dependencies": [], "evidence_refs": ["04-workshop/capture/outcomes.md#" + item["from_action"]],
                        "due": item["due"]}, f"# Spec {item['item_id']} (stub)")

    _spec_decision = _spec_process = _spec_tool = _spec

    # ---- stage 6
    def _tracking(self, t: Task) -> None:
        met = any("met" in " ".join(_lines(f)).lower() for f in t.reads if "00-inputs/tracking" in f)
        for w in t.writes:
            item = Path(w).stem
            artifacts.write(Path(_abs(w)), {"artifact": "tracking", "item_id": item,
                            "kpis": [{"metric": "Teams using the new handover", "baseline": 0, "target": 5, "by": _in(90),
                                      "current": 5 if met else 0}],
                            "okr": {"objective": "Handover works first time", "key_results": ["5 teams on the template"]},
                            "status": "met" if met else "in_progress", "updated": TODAY.isoformat()}, f"# Tracking {item} (stub)")

    def _archivist(self, t: Task) -> None:
        st = _json(edir(t.eid) / "state.json")
        artifacts.write(Path(_abs(t.writes[0])), {"artifact": "archive", "engagement": t.eid,
                        "client": st.get("client") or "Unknown", "sector": st.get("sector") or "Unknown",
                        "challenge_type": "operating model", "closed": TODAY.isoformat(), "tags": ["stub"],
                        "decisions": ["Adopt a single handover template"], "landed": ["x1", "x2", "x3"],
                        "did_not_land": [], "lessons": ["Stub lesson."], "follow_up": None},
                        f"# {st['title']}\n\nStub archive entry.")


def _lines(rel: str) -> list[str]:
    p = Path(_abs(rel))
    if not p.is_file():
        return []
    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith(("WEBVTT", "---", "#")) and "-->" not in line and not line.isdigit():
            out.append(line)
    return out


def _meta(path: Path) -> dict[str, Any]:
    return artifacts.read(path)[0]


def _json(path: Path) -> dict[str, Any]:
    import json

    return json.loads(path.read_text())


def _one_claim(t: Task) -> list[dict[str, Any]]:
    return [{"id": "c1", "text": "Transcript empty.", "confidence": 0.1, "timestamp": "", "source": t.reads[0] if t.reads else "none"}]
