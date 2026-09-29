"""Agent runners. The orchestrator hands a Task to a runner; the runner makes the agent
write its files. Two implementations share one interface:

- SdkRunner: runs the agent through the Claude Agent SDK with the agent's own tool list,
  model and skills, and a hook that blocks writes outside the files the task names.
- StubRunner: writes schema-valid placeholder artifacts, no API calls (phase 0 spine).
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from . import agents, config
from .agents import Task

log = logging.getLogger("loom.runner")


@dataclass
class RunResult:
    ok: bool
    summary: str = ""
    cost_usd: float | None = None
    error: str | None = None


class Runner(Protocol):
    def run(self, task: Task, on_event: Callable[[str], None] | None = None) -> RunResult: ...


def get_runner(kind: str | None = None) -> Runner:
    kind = kind or config.RUNNER
    if kind == "sdk":
        return SdkRunner()
    if kind == "stub":
        from .stubs import StubRunner

        return StubRunner()
    raise ValueError(f"unknown LOOM_RUNNER {kind!r} (use 'sdk' or 'stub')")


# Tools the orchestrator itself handles: agents never get them inside the app.
# (In interactive Claude Code, the orchestrator and delegation agents do use Task.)
ENGINE_ONLY_TOOLS = {"Task", "Agent"}


class SdkRunner:
    def run(self, task: Task, on_event: Callable[[str], None] | None = None) -> RunResult:
        return asyncio.run(self._run(task, on_event))

    async def _run(self, task: Task, on_event: Callable[[str], None] | None) -> RunResult:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            HookMatcher,
            ResultMessage,
            TextBlock,
            ToolUseBlock,
            query,
        )

        spec = agents.load(task.agent)
        tools = [t for t in spec.tools if t not in ENGINE_ONLY_TOOLS]
        # Writes go through Write or Edit; give Edit wherever Write is allowed so agents
        # can revise their own draft.
        if "Write" in tools and "Edit" not in tools:
            tools.append("Edit")

        allowed_files = {_abs(p) for p in [*task.writes, task.handoff] if p}
        allowed_dirs = [_abs(d) for d in task.write_dirs]

        async def guard_writes(input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict[str, Any]:
            path = (input_data.get("tool_input") or {}).get("file_path", "")
            target = _abs(path) if path else ""
            ok = target in allowed_files or any(target.startswith(d + os.sep) for d in allowed_dirs)
            if ok and os.path.basename(target) not in ("state.json", "log.jsonl"):
                return {}
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": f"Loom: {task.agent} may not write {path}. Allowed: "
                    + ", ".join(sorted(allowed_files | {d + '/' for d in allowed_dirs})),
                }
            }

        skill_text, skill_dirs = _skills_block(spec)
        system = "\n\n".join(
            [
                spec.prompt,
                _read_claude_md(),
                skill_text,
            ]
        )
        options = ClaudeAgentOptions(
            system_prompt=system,
            cwd=str(config.ROOT),
            add_dirs=[str(d) for d in skill_dirs + _data_dirs_outside_root()],
            tools=tools,
            allowed_tools=tools,
            permission_mode="dontAsk",
            setting_sources=[],  # isolation: CLAUDE.md and skills are injected above
            model=spec.model,
            max_turns=config.MAX_TURNS,
            max_budget_usd=config.MAX_BUDGET_USD,
            hooks={"PreToolUse": [HookMatcher(matcher="Write|Edit|MultiEdit|NotebookEdit", hooks=[guard_writes])]},
        )

        summary, cost, error = "", None, None
        try:
            async for msg in query(prompt=task.prompt(), options=options):
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock) and on_event:
                            on_event(block.text[:500])
                        elif isinstance(block, ToolUseBlock) and on_event:
                            target = block.input.get("file_path") or block.input.get("query") or block.input.get("url") or ""
                            on_event(f"{block.name} {target}".strip())
                elif isinstance(msg, ResultMessage):
                    cost = msg.total_cost_usd
                    summary = msg.result or ""
                    if msg.is_error:
                        error = f"{msg.subtype}: {msg.errors or msg.result}"
        except Exception as e:  # noqa: BLE001 - surface any SDK/CLI failure to the gate
            log.exception("agent %s failed", task.agent)
            return RunResult(ok=False, error=f"{type(e).__name__}: {e}")
        return RunResult(ok=error is None, summary=summary, cost_usd=cost, error=error)


def _abs(p: str) -> str:
    path = Path(p)
    if not path.is_absolute():
        path = config.ROOT / path
    return os.path.normpath(str(path))


def _data_dirs_outside_root() -> list[Path]:
    """In the container the data volume may live outside LOOM_ROOT; agents need access."""
    out = []
    for d in (config.ENGAGEMENTS_DIR, config.ARCHIVE_DIR):
        if not str(d).startswith(str(config.ROOT) + os.sep):
            out.append(d)
    return out


def _read_claude_md() -> str:
    p = config.ROOT / "CLAUDE.md"
    return "# Project rules (CLAUDE.md)\n\n" + p.read_text() if p.exists() else ""


def _skills_block(spec: agents.AgentSpec) -> tuple[str, list[Path]]:
    """Inline each skill's SKILL.md so the method is always in context, and expose skill
    folders outside the root (reused personal skills) for reading their references."""
    parts, dirs = [], []
    for name in spec.skills:
        path = agents.find_skill(name)
        if path is None:
            parts.append(
                f"# Skill `{name}` (NOT AVAILABLE)\n\nThis reused skill is not installed in this "
                "environment. Proceed with your own best method and say so under `unsure` in your handoff."
            )
            log.warning("skill %s not found for %s", name, spec.name)
            continue
        if not str(path.resolve()).startswith(str(config.ROOT)):
            dirs.append(path.parent)
        parts.append(f"# Skill `{name}` (folder: {path.parent})\n\n{path.read_text()}")
    return "\n\n".join(parts), dirs
