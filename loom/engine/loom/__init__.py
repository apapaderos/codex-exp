"""Loom engine: the deterministic orchestrator, validators and agent runners.

The orchestrator is a small state machine (see loom-orchestration skill). It reads
state.json, dispatches the next agent, validates what the agent wrote, and moves the
stage forward. It never writes content, and it never passes a gate on its own.
"""

__version__ = "0.1.0"
