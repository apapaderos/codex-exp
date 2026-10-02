"""Phase 0 done-when: a dummy engagement moves through all six stages with stub agents,
pausing at each gate, and every artifact validates against its schema."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest


@pytest.fixture()
def loom(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOM_ENGAGEMENTS_DIR", str(tmp_path / "engagements"))
    monkeypatch.setenv("LOOM_ARCHIVE_DIR", str(tmp_path / "archive"))
    monkeypatch.setenv("LOOM_JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("LOOM_RUNNER", "stub")
    (tmp_path / "archive").mkdir()
    (tmp_path / "archive" / "2025-03-old-engagement.md").write_text("---\nartifact: archive\n---\n")
    import loom.config

    importlib.reload(loom.config)
    import loom.artifacts, loom.state, loom.brief, loom.refs, loom.runners, loom.stubs, loom.orchestrator, loom.jobs, loom.cli  # noqa: E401

    for m in (loom.state, loom.brief, loom.refs, loom.runners, loom.stubs, loom.orchestrator, loom.jobs, loom.cli):
        importlib.reload(m)
    return loom


FIX = Path(__file__).parent / "fixtures"


def _where(st):
    return st["stage"], st["status"], (st.get("gate") or {}).get("gate"), (st.get("waiting") or {}).get("kind")


def test_full_pipeline(loom):
    o, state = loom.orchestrator, loom.state
    eid = o.create("Acme onboarding", "New joiners take 6 weeks to be productive", sponsor="COO", client="Acme", sector="Retail")
    st = o.advance(eid)
    assert _where(st) == (1, "queued", None, None), "no inputs yet: sits queued"

    o.add_input(eid, "transcripts", "kickoff.vtt", (FIX / "kickoff.vtt").read_bytes())
    o.add_input(eid, "documents", "onboarding-process.md", (FIX / "onboarding-process.md").read_bytes())
    st = o.inputs_arrived(eid, "documents")
    assert _where(st) == (1, "awaiting_gate", 1, None)

    # Gate 1 refuses approval until research_direction is set.
    st = o.decide(eid, "approve", "andreas")
    assert "research_direction" in st["gate"]["refused"] and not st["gate"]["error"]
    path = state.edir(eid) / "01-intake/brief.md"
    meta, body = loom.artifacts.read(path)
    meta["research_direction"] = "How peers onboard in weeks, and where our handovers break."
    loom.artifacts.write(path, meta, body)
    st = o.decide(eid, "approve", "andreas")
    assert _where(st) == (2, "awaiting_gate", 2, None)
    assert (state.round_dir(eid, 1) / "questions.md").exists()

    # Gate 2: send questions out, wait on the org, responses start round 2.
    st = o.decide(eid, "send", "andreas")
    assert _where(st) == (2, "waiting_org", None, "responses")
    o.add_input(eid, "responses", "survey.csv", b"We lose a week waiting for laptops\nNobody owns the buddy system\n")
    st = o.inputs_arrived(eid, "responses")
    assert _where(st) == (2, "awaiting_gate", 2, None)
    assert st["research"]["round"] == 2
    assert (state.round_dir(eid, 1) / "responses.md").exists()

    # Another round with a prompt, then done.
    st = o.decide(eid, "another_round", "andreas", notes="Look for Greek retail peers")
    assert st["research"]["round"] == 3 and _where(st) == (2, "awaiting_gate", 2, None)
    st = o.decide(eid, "approve", "andreas")
    # Framing runs without a gate, workshop design stops at gate 3.
    assert _where(st) == (4, "awaiting_gate", 3, None)
    assert st["stages"]["3"]["status"] == "done"

    st = o.decide(eid, "revise", "andreas", notes="Shorter Explore act")
    assert _where(st) == (4, "awaiting_gate", 3, None)
    st = o.decide(eid, "approve", "andreas")
    assert _where(st) == (4, "waiting_org", None, "workshop")

    o.add_input(eid, "workshop", "wall.txt", b"Decision: single template\n")
    st = o.inputs_arrived(eid, "workshop")
    assert _where(st) == (5, "awaiting_gate", 4, None)
    assert len(list((state.edir(eid) / "05-delegation").rglob("x*.md"))) >= 3

    st = o.decide(eid, "approve", "andreas", assignments=[{"item_id": "x1", "owner": "COO", "meeting": "2026-11-02"}])
    assert _where(st) == (6, "waiting_org", None, "adoption")

    o.add_input(eid, "tracking", "update.txt", b"All KPIs met\n")
    st = o.inputs_arrived(eid, "tracking")
    assert _where(st) == (6, "awaiting_gate", 5, None)

    st = o.decide(eid, "restart", "andreas", notes="Scale onboarding to 3 new stores")
    assert st["closed"] and st["status"] == "done"
    assert (loom.config.ARCHIVE_DIR / f"{eid}.md").exists()
    children = [i for i in state.list_ids() if state.load(i).get("parent") == eid]
    assert len(children) == 1

    assert state.validate_engagement(eid) == []
    events = [e["event"] for e in state.read_log(eid)]
    assert events.count("gate_opened") >= 7 and "closed" in events


def test_reject_goes_back_and_keeps_research_history(loom):
    o, state = loom.orchestrator, loom.state
    eid = o.create("Beta", "Test reject")
    o.add_input(eid, "documents", "a.md", b"Some doc line\n")
    o.inputs_arrived(eid, "documents")
    p = state.edir(eid) / "01-intake/brief.md"
    meta, body = loom.artifacts.read(p)
    meta["research_direction"] = "x"
    loom.artifacts.write(p, meta, body)
    o.decide(eid, "approve", "r")
    st = o.decide(eid, "reject", "r", notes="wrong people", target_stage=1)
    assert _where(st) == (1, "awaiting_gate", 1, None)
    st = o.decide(eid, "approve", "r")
    assert st["research"]["round"] == 2, "round 1 is kept, a fresh round opens"
    st = o.decide(eid, "close", "r")
    assert st["gate"]["refused"], "close is not valid at gate 2"


def test_invalid_agent_output_blocks_the_gate(loom, monkeypatch):
    o = loom.orchestrator
    eid = o.create("Gamma", "Test block")
    o.add_input(eid, "documents", "a.md", b"line\n")

    orig = loom.stubs.StubRunner._intake_docs

    def broken(self, t):
        orig(self, t)
        Path(loom.runners._abs(t.writes[0])).write_text("no frontmatter")

    monkeypatch.setattr(loom.stubs.StubRunner, "_intake_docs", broken)
    st = o.inputs_arrived(eid, "documents")
    assert st["gate"]["error"] and "intake-docs" in st["gate"]["error"]
    st = o.decide(eid, "approve", "r")
    assert "not allowed" in st["gate"]["refused"]
    monkeypatch.setattr(loom.stubs.StubRunner, "_intake_docs", orig)
    st = o.decide(eid, "revise", "r")
    assert _where(st) == (1, "awaiting_gate", 1, None) and not st["gate"]["error"]


def test_job_queue_round_trip(loom):
    o, jobs = loom.orchestrator, loom.jobs
    eid = o.create("Delta", "Queue")
    o.add_input(eid, "documents", "a.md", b"line\n")
    jobs.enqueue("inputs", eid, kind="documents")
    path, job = jobs.claim()
    jobs.run_job(job)
    assert loom.state.load(eid)["status"] == "awaiting_gate"


def test_schemas_and_agents_are_consistent(loom):
    import json

    from loom import agents

    for p in loom.config.SCHEMAS_DIR.glob("*.json"):
        json.loads(p.read_text())
    specs = {a.name: a for a in agents.all_agents()}
    assert len(specs) == 15  # 14 from the spec + the read-only ask helper
    only_web = {n for n, a in specs.items() if {"WebSearch", "WebFetch"} & set(a.tools)}
    assert only_web == {"research-secondary"}
    for a in specs.values():
        for s in a.skills:
            if s.startswith("loom-"):
                assert agents.find_skill(s), f"{a.name} needs missing skill {s}"
