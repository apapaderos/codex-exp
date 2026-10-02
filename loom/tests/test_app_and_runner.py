"""The app drives the same state machine through the job queue, and the SDK runner gives
each agent only its own tools and only the files its task names."""

from __future__ import annotations

import asyncio
import importlib

import pytest

from test_pipeline import FIX, loom  # noqa: F401 - fixture


@pytest.fixture()
def client(loom, monkeypatch):  # noqa: F811
    monkeypatch.setenv("LOOM_UNDO_SECONDS", "0")
    import app.main
    import app.narrate

    importlib.reload(app.narrate)
    importlib.reload(app.main)
    from fastapi.testclient import TestClient

    return TestClient(app.main.app), loom


def drain(loom):  # noqa: F811
    while (got := loom.jobs.claim()):
        path, job = got
        loom.jobs.run_job(job)
        path.unlink()


SYSTEM_WORDS = ("gate", "schema", "agent", "stage ")


def visible(html):
    import re

    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split()).lower()


def test_conversation_walks_the_whole_engagement(client):
    c, loom = client  # noqa: F811
    r = c.post("/new", data={"challenge": "New store staff take six weeks to work alone.", "challenge_type": "Employee experience",
                             "person": ['{"name": "Eleni", "role": "Sponsor"}', '{"name": "Nikos", "role": "Stakeholder"}'],
                             "kind": ["transcripts", "documents"]},
               files=[("files", ("kickoff.vtt", (FIX / "kickoff.vtt").read_bytes())),
                      ("files", ("process.md", (FIX / "onboarding-process.md").read_bytes()))],
               follow_redirects=False)
    assert r.status_code == 303
    eid = r.headers["location"].rsplit("/", 1)[1]
    st = loom.state.load(eid)
    assert st["sponsor"] == "Eleni" and st["challenge_type"] == "Employee experience" and len(st["people"]) == 2
    drain(loom)

    page = c.get(f"/e/{eid}").text
    words = visible(page)
    assert "here is what i understood" in words and "understand" in words
    assert "tell me where research should point" in words and "research this direction" in words
    for w in SYSTEM_WORDS:
        assert w not in words, f"system word on screen: {w!r}"
    assert "Needs you" in page

    # A note while nothing is decided: saved for later steps, and answered.
    c.post(f"/e/{eid}/say", data={"text": "The CFO is also a sponsor."})
    drain(loom)
    assert len(loom.orchestrator.input_files(eid, "notes")) == 1
    assert "noted" in visible(c.get(f"/e/{eid}").text)

    # Research this direction: the note becomes the research direction.
    c.post(f"/e/{eid}/decide", data={"decision": "approve", "note": "How peers onboard in weeks", "note_as": "direction"})
    drain(loom)
    st = loom.state.load(eid)
    assert (st["stage"], st["status"]) == (2, "awaiting_gate")
    brief, _ = loom.artifacts.read(loom.state.edir(eid) / "01-intake/brief.md")
    assert brief["research_direction"] == "How peers onboard in weeks"
    assert any(s["name"] == "Eleni" for s in brief["stakeholders"])

    # Brief is editable when Loom is idle, and validated.
    form = c.get(f"/e/{eid}/brief").text
    assert "Where research should point" in form
    assert c.post(f"/e/{eid}/brief", data={"challenge": "Onboarding in three weeks", "sponsor": "Eleni",
                                           "stakeholders": "Nikos, IT\nMaria", "constraints": "Spring opening",
                                           "research_direction": "Peers"}, follow_redirects=False).status_code == 303
    brief, _ = loom.artifacts.read(loom.state.edir(eid) / "01-intake/brief.md")
    assert brief["stakeholders"][0] == {"name": "Nikos", "role": "IT"} and brief["constraints"] == ["Spring opening"]

    # Send questions, then paste replies from the composer and continue.
    c.post(f"/e/{eid}/decide", data={"decision": "send"})
    drain(loom)
    assert "waiting on replies" in visible(c.get(f"/e/{eid}").text)
    c.post(f"/e/{eid}/say", data={"text": "Laptops late\nNo buddy owner", "as_kind": "responses"})
    c.post(f"/e/{eid}/decide", data={"decision": "continue:responses"})
    drain(loom)
    assert loom.state.load(eid)["research"]["round"] == 2

    c.post(f"/e/{eid}/decide", data={"decision": "approve"})
    drain(loom)
    assert "ready to run it" in visible(c.get(f"/e/{eid}").text)
    c.post(f"/e/{eid}/decide", data={"decision": "approve"})
    drain(loom)
    c.post(f"/e/{eid}/say", files=[("files", ("wall.txt", b"Decision: one template"))])
    c.post(f"/e/{eid}/decide", data={"decision": "continue:workshop"})
    drain(loom)
    page = c.get(f"/e/{eid}").text
    assert "who owns which" in visible(page) and 'name="owner_x1"' in page
    c.post(f"/e/{eid}/decide", data={"decision": "approve", "item": ["x1", "x2", "x3"], "owner_x1": "Eleni",
                                     "meeting_x1": "2026-11-02", "owner_x2": "Ops", "owner_x3": "IT"})
    drain(loom)
    st = loom.state.load(eid)
    assert st["stage"] == 6 and st["assignments"][0] == {"item_id": "x1", "owner": "Eleni", "meeting": "2026-11-02"}
    panel = c.get(f"/e/{eid}").text
    assert "Tracking" in panel and "Spec owners" in panel

    # Preview renders markdown and keeps raw HTML escaped; paths cannot escape the engagement.
    assert c.get(f"/e/{eid}/preview", params={"path": "03-framing/problems.md"}).status_code == 200
    assert c.get(f"/e/{eid}/preview", params={"path": "../../etc/passwd"}).status_code == 404

    c.post(f"/e/{eid}/decide", data={"decision": "close"})
    drain(loom)
    assert loom.state.load(eid)["closed"]
    assert "closed and archived" in visible(c.get(f"/e/{eid}").text)
    assert c.get("/archive", params={"q": "operating"}).status_code == 200
    assert c.get("/settings").status_code == 200


def test_decision_can_be_undone_until_the_next_step_starts(client, monkeypatch):
    c, loom = client  # noqa: F811
    import app.main

    monkeypatch.setattr(app.main, "UNDO_SECONDS", 60)
    eid = loom.orchestrator.create("Undo", "Test undo")
    loom.orchestrator.add_input(eid, "documents", "a.md", b"line\n")
    loom.orchestrator.inputs_arrived(eid, "documents")
    c.post(f"/e/{eid}/decide", data={"decision": "revise", "note": "again"})
    assert loom.jobs.claim() is None, "held back during the undo window"
    page = c.get(f"/e/{eid}").text
    assert "Undo" in page and "Starting in" in page
    job = loom.jobs.jobs_for(eid)[0]["id"]
    c.post(f"/e/{eid}/undo", data={"job": job})
    assert loom.jobs.jobs_for(eid) == []
    assert loom.state.load(eid)["gate"]["gate"] == 1


def test_divider_marks_what_happened_since_a_real_absence(client):
    c, loom = client  # noqa: F811
    import json

    eid = loom.orchestrator.create("Away", "Test divider")
    assert "since you were last here" not in visible(c.get(f"/e/{eid}").text)
    seen = loom.state.edir(eid) / ".seen.json"
    seen.write_text(json.dumps({"reviewer": "2026-01-01T00:00:00+00:00"}))
    loom.orchestrator.add_input(eid, "documents", "a.md", b"line\n")
    loom.orchestrator.inputs_arrived(eid, "documents")
    assert "since you were last here" in visible(c.get(f"/e/{eid}").text)


def test_drafts_and_inputs_are_editable_on_your_turn_only(client):
    c, loom = client  # noqa: F811
    import app.main

    r = c.post("/new/sample", follow_redirects=False)
    eid = r.headers["location"].rsplit("/", 1)[1]
    assert len(loom.orchestrator.input_files(eid, "transcripts")) == 1
    assert c.get(f"/e/{eid}/edit", params={"path": "00-inputs/transcripts/01-kickoff-call.vtt"}).status_code == 409, "busy"
    drain(loom)
    # Your files: a transcript you gave Loom can be corrected.
    assert c.get(f"/e/{eid}/edit", params={"path": "00-inputs/transcripts/01-kickoff-call.vtt"}).status_code == 200
    c.post(f"/e/{eid}/decide", data={"decision": "approve", "note": "Peers", "note_as": "direction"})
    drain(loom)
    q = "02-research/round-01/questions.md"
    page = c.get(f"/e/{eid}/preview", params={"path": q}).text
    assert "data-edit" in page
    original = (loom.state.edir(eid) / q).read_text()
    bad = c.post(f"/e/{eid}/edit", data={"path": q, "text": "no header any more"})
    assert bad.status_code == 422 and (loom.state.edir(eid) / q).read_text() == original
    good = c.post(f"/e/{eid}/edit", data={"path": q, "text": original + "\nReply by Friday.\n"}, follow_redirects=False)
    assert good.status_code == 303 and "Reply by Friday" in (loom.state.edir(eid) / q).read_text()
    assert c.get(f"/e/{eid}/edit", params={"path": "01-intake/signal-voice.md"}).status_code == 409, "past step"
    # The sample button drops in the replies while waiting.
    c.post(f"/e/{eid}/decide", data={"decision": "send"})
    drain(loom)
    assert "Add the sample replies" in c.get(f"/e/{eid}").text
    c.post(f"/e/{eid}/sample", data={"kind": "responses"})
    assert loom.orchestrator.input_files(eid, "responses")


def test_doctor_in_demo_mode(loom, capsys):  # noqa: F811
    assert loom.cli.doctor() == 0
    assert "Demo mode" in capsys.readouterr().out


def test_sdk_runner_restricts_tools_and_writes(loom, monkeypatch):  # noqa: F811
    import claude_agent_sdk
    from claude_agent_sdk import ResultMessage

    seen = {}

    async def fake_query(prompt, options):
        seen["options"], seen["prompt"] = options, prompt
        guard = options.hooks["PreToolUse"][0].hooks[0]
        ok = await guard({"tool_input": {"file_path": "engagements/e1/02-research/round-01/secondary.md"}}, "t1", None)
        state_write = await guard({"tool_input": {"file_path": "engagements/e1/state.json"}}, "t2", None)
        other = await guard({"tool_input": {"file_path": "engagements/e1/03-framing/problems.md"}}, "t3", None)
        seen["decisions"] = (ok, state_write, other)
        yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1,
                            session_id="s", total_cost_usd=0.01, result="done")

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
    from loom.agents import Task

    task = Task(agent="research-secondary", eid="e1", stage=2, instructions="x",
                writes=["engagements/e1/02-research/round-01/secondary.md"],
                handoff="engagements/e1/02-research/round-01/handoff-research-secondary.md")
    res = loom.runners.SdkRunner().run(task)
    assert res.ok and res.cost_usd == 0.01
    o = seen["options"]
    assert set(o.tools) == {"Read", "Write", "Edit", "Grep", "Glob", "WebSearch", "WebFetch"}
    assert o.model == "sonnet" and o.permission_mode == "dontAsk" and o.setting_sources == []
    assert "Skill `loom-secondary-research`" in o.system_prompt and "Project rules (CLAUDE.md)" in o.system_prompt
    ok, state_write, other = seen["decisions"]
    assert ok == {}
    assert state_write["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert other["hookSpecificOutput"]["permissionDecision"] == "deny"

    # Delegation's Task tool is engine-only inside the app.
    seen.clear()
    loom.runners.SdkRunner().run(Task(agent="delegation", eid="e1", stage=5, instructions="x",
                                      writes=["engagements/e1/05-delegation/routing.md"], handoff="engagements/e1/05-delegation/h.md"))
    assert "Task" not in seen["options"].tools


def test_model_override(loom, monkeypatch):  # noqa: F811
    monkeypatch.setenv("LOOM_MODEL_FRAMING", "claude-opus-5-5")
    assert loom.agents.load("framing").model == "claude-opus-5-5"
