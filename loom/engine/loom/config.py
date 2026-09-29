"""Paths and settings. Everything is overridable by environment variables so the same
code runs from a laptop checkout and inside the container."""

from __future__ import annotations

import os
from pathlib import Path

# The Loom root holds .claude/, schemas/, archive/ and engagements/.
# Agents run with this as their working directory, so every path they see is relative to it.
ROOT = Path(os.environ.get("LOOM_ROOT", Path(__file__).resolve().parents[2])).resolve()

AGENTS_DIR = ROOT / ".claude" / "agents"
SKILLS_DIR = ROOT / ".claude" / "skills"
SCHEMAS_DIR = ROOT / "schemas"
ARCHIVE_DIR = Path(os.environ.get("LOOM_ARCHIVE_DIR", ROOT / "archive")).resolve()
ENGAGEMENTS_DIR = Path(os.environ.get("LOOM_ENGAGEMENTS_DIR", ROOT / "engagements")).resolve()
JOBS_DIR = Path(os.environ.get("LOOM_JOBS_DIR", ROOT / ".loom-jobs")).resolve()

# Where reused skills (evidence, reframe, solve-for-x, senior-experience-architect) are found
# when they are not copied into .claude/skills. Colon-separated list of directories.
EXTRA_SKILL_DIRS = [Path(p) for p in os.environ.get("LOOM_EXTRA_SKILL_DIRS", "").split(":") if p]

# "sdk" runs real agents through the Claude Agent SDK. "stub" writes schema-valid
# placeholders with no API calls: phase 0 spine testing, CI, and demos.
RUNNER = os.environ.get("LOOM_RUNNER", "stub")

# Per-run safety limits for real agent runs.
MAX_TURNS = int(os.environ.get("LOOM_MAX_TURNS", "60"))
MAX_BUDGET_USD = float(os.environ["LOOM_MAX_BUDGET_USD"]) if os.environ.get("LOOM_MAX_BUDGET_USD") else None

# Notifications: generic JSON webhook (Teams / Slack incoming webhook, or any relay).
NOTIFY_WEBHOOK = os.environ.get("LOOM_NOTIFY_WEBHOOK", "")
APP_BASE_URL = os.environ.get("LOOM_APP_BASE_URL", "http://localhost:8080")


def model_for(agent: str, default: str) -> str:
    """LOOM_MODEL_<AGENT> overrides the model in the agent's frontmatter, e.g.
    LOOM_MODEL_FRAMING=opus. Lets you tune cost/quality without editing agent files."""
    return os.environ.get("LOOM_MODEL_" + agent.upper().replace("-", "_"), default)
