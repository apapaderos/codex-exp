"""The ask helper: answers what a reviewer types into the conversation.

Read-only by construction: it gets Read, Grep and Glob, never Write. The question is also
kept as a note in 00-inputs/notes/ (by the app), so later steps take it into account; this
module only answers. Its reply is appended to log.jsonl as a 'reply' event.
"""

from __future__ import annotations

import asyncio
import logging

from . import agents, config, state
from .state import edir

log = logging.getLogger("loom.ask")


def answer(eid: str, question: str) -> str:
    st = state.load(eid)
    if config.RUNNER == "sdk":
        try:
            text = asyncio.run(_sdk_answer(eid, st, question))
        except Exception as e:  # noqa: BLE001 - a failed answer must not break the engagement
            log.exception("ask failed")
            text = f"I couldn't answer that just now ({type(e).__name__}). Your note is saved and the next step will read it."
    else:
        step = {1: "Understand", 2: "Research", 3: "Frame", 4: "Workshop", 5: "Hand over", 6: "Track"}[st["stage"]]
        text = (f"Noted. I've saved this and the next step will take it into account. We're in {step} right now. "
                "(Answers to questions need the real agents: this demo runs on placeholders.)")
    # log.jsonl is append-only: no engagement lock, so a reply never waits behind a running step.
    state.log(eid, "loom", "reply", st["stage"], text=text, question=question[:200])
    return text


async def _sdk_answer(eid: str, st: dict, question: str) -> str:
    from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

    spec = agents.load("ask")
    recent = state.read_log(eid)[-30:]
    prompt = "\n".join([
        f"Engagement: {eid} ({st['title']}), folder {state.agent_path(edir(eid))}/",
        f"Current step: {st['stage']}, status {st['status']}.",
        "Recent events (newest last):",
        *[f"- {e['ts']} {e['actor']} {e['event']} {e.get('detail', '')}" for e in recent],
        "",
        "The reviewer wrote:",
        question,
    ])
    options = ClaudeAgentOptions(
        system_prompt=spec.prompt, cwd=str(config.ROOT), tools=spec.tools, allowed_tools=spec.tools,
        permission_mode="dontAsk", setting_sources=[], model=spec.model, max_turns=12,
    )
    result = ""
    async for msg in query(prompt=prompt, options=options):
        if isinstance(msg, ResultMessage):
            result = msg.result or ""
    return result.strip() or "I don't have an answer to that from the engagement files."
