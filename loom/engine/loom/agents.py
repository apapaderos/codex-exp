"""Agent definitions are the .claude/agents/*.md files. The same files serve Claude Code
(interactive, phase 0-5) and the app (Agent SDK, phase 6), so there is one source of truth."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import artifacts, config


@dataclass
class AgentSpec:
    name: str
    description: str
    tools: list[str]
    model: str
    skills: list[str]
    prompt: str
    path: Path


@dataclass
class Task:
    """One dispatch. The orchestrator names every file the agent may read and write, so
    agents never guess paths and never talk to each other directly."""

    agent: str
    eid: str
    stage: int
    instructions: str
    reads: list[str] = field(default_factory=list)
    writes: list[str] = field(default_factory=list)
    write_dirs: list[str] = field(default_factory=list)
    handoff: str = ""
    notes: str = ""
    extra: dict = field(default_factory=dict)

    def prompt(self) -> str:
        lines = [
            f"Loom engagement: {self.eid} (stage {self.stage}).",
            "",
            "Task:",
            self.instructions.strip(),
            "",
            "Read (paths are relative to the Loom root, your working directory):",
            *[f"- {p}" for p in self.reads or ["(nothing beyond your agent instructions)"]],
            "",
            "Write exactly these files, and nothing else:",
            *[f"- {p}" for p in self.writes],
        ]
        if self.write_dirs:
            lines += ["You may also create files inside:", *[f"- {p}/" for p in self.write_dirs]]
        lines += [
            f"- {self.handoff}  (handoff: what you did, what you are unsure of, what the reviewer should check; schema schemas/handoff.json)",
            "",
            "Every artifact is markdown with YAML frontmatter that must validate against its schema in schemas/.",
            "Never touch state.json or log.jsonl. Never advance a stage. Never contact anyone.",
        ]
        if self.notes:
            lines += ["", "Reviewer notes for this run (follow them):", self.notes.strip()]
        return "\n".join(lines)


def load(name: str) -> AgentSpec:
    path = config.AGENTS_DIR / f"{name}.md"
    meta, body = artifacts.read(path)
    tools = [t.strip() for t in str(meta.get("tools", "")).split(",") if t.strip()]
    skills = meta.get("skills") or []
    if isinstance(skills, str):
        skills = [s.strip() for s in skills.split(",") if s.strip()]
    return AgentSpec(
        name=meta["name"],
        description=meta.get("description", ""),
        tools=tools,
        model=config.model_for(name, meta.get("model", "sonnet")),
        skills=list(skills),
        prompt=body,
        path=path,
    )


def all_agents() -> list[AgentSpec]:
    return [load(p.stem) for p in sorted(config.AGENTS_DIR.glob("*.md"))]


def find_skill(name: str) -> Path | None:
    """Repo skills first, then LOOM_EXTRA_SKILL_DIRS (reused personal skills)."""
    for base in [config.SKILLS_DIR, *config.EXTRA_SKILL_DIRS]:
        p = base / name / "SKILL.md"
        if p.exists():
            return p
    return None
