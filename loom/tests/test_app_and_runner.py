"""The app drives the same state machine through the job queue, and the SDK runner gives
each agent only its own tools and only the files its task names."""

from __future__ import annotations

import asyncio
import importlib

import pytest

from test_pipeline import FIX, loom  # noqa: F401 - fixture


@pytest.fixture()
def client(loom):  # noqa: F811
    import app.main

    importlib.reload(app.main)
    from fastapi.testclient import TestClient

    return TestClient(app.main.app), loom


def drain(loom):  # noqa: F811
    while (got := loom.jobs.claim()):
        path, job = got
        loom.jobs.run_job(job)
        path.unlink()


def test_app_walks_to_gate_2(client):
    c, loom = client  # noqa: F811
    r = c.post("/new", data={"title": "Web run", "intent": "Fix onboarding", "sponsor": "COO"},
               files=[("transcripts", ("kickoff.vtt", (FIX / "kickoff.vtt").read_bytes())),
                      ("documents", ("process.md", (FIX / "onboarding-process.md").read_bytes()))],
               follow_redirects=False)
    assert r.status_code == 303
    eid = r.headers["location"].rsplit("/", 1)[1]
    drain(loom)
    page = c.get(f"/e/{eid}").text
    assert "Gate 1" in page and "01-intake/brief.md" in page
    assert "Gate 1 open" in c.get("/").text

    # Gate 1 edit must validate before saving.
    bad = c.post(f"/e/{eid}/edit", data={"path": "01-intake/brief.md", "text": "---\nartifact: brief\n---\n"})
    assert bad.status_code == 422
    brief = (loom.state.edir(eid) / "01-intake/brief.md").read_text()
    good = brief.replace('research_direction: ""', 'research_direction: "Peers, and where handovers break"').replace(
        "research_direction: ''", "research_direction: Peers, and where handovers break")
    assert good != brief
    assert c.post(f"/e/{eid}/edit", data={"path": "01-intake/brief.md", "text": good}, follow_redirects=False).status_code == 303
    c.post(f"/e/{eid}/decide", data={"decision": "approve"})
    drain(loom)
    st = loom.state.load(eid)
    assert (st["stage"], st["status"], st["gate"]["gate"]) == (2, "awaiting_gate", 2)
    assert st["decisions"][-1]["by"] == "reviewer"

    c.post(f"/e/{eid}/decide", data={"decision": "send"})
    drain(loom)
    assert "Waiting on the organisation" in c.get(f"/e/{eid}").text
    c.post(f"/e/{eid}/inputs", data={"kind": "responses", "text": "Laptops late\nNo buddy owner", "complete": "1"})
    drain(loom)
    assert loom.state.load(eid)["research"]["round"] == 2
    assert c.get(f"/e/{eid}/edit", params={"path": "03-framing/problems.md"}).status_code == 403
    assert c.get(f"/e/{eid}", params={"view": "../../../etc/passwd"}).status_code == 400


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
