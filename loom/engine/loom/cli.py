"""`loom` command line: the gate-by-hand interface for phases 0 to 5.

  loom new "Acme onboarding" --intent "..." [--sponsor ...] [--client ...] [--sector ...]
  loom add <id> transcripts|documents|responses|workshop|tracking FILE...   (stores and triggers)
  loom run <id>                      advance until the next gate or wait
  loom status [<id>]                 where every engagement is
  loom decide <id> approve|revise|reject|another_round|send|close|restart [--notes ..] [--target N]
  loom validate [<id>]               state.json, log.jsonl and every artifact against schemas/
  loom inbox                         open gates and waits, oldest first
  loom worker                        run the job queue (the app's background runner)

Editing pending_decision in state.json by hand and then `loom run <id>` is equivalent to
`loom decide`.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import config, orchestrator, state


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(prog="loom", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runner", choices=["stub", "sdk"], help="override LOOM_RUNNER")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("new")
    p.add_argument("title")
    p.add_argument("--intent", required=True)
    p.add_argument("--sponsor", default="")
    p.add_argument("--client", default="")
    p.add_argument("--sector", default="")
    p.add_argument("--id")

    p = sub.add_parser("add")
    p.add_argument("id")
    p.add_argument("kind", choices=state.INPUT_KINDS)
    p.add_argument("files", nargs="+")
    p.add_argument("--no-trigger", action="store_true", help="store only; more files are coming")

    sub.add_parser("run").add_argument("id")
    sub.add_parser("status").add_argument("id", nargs="?")
    sub.add_parser("validate").add_argument("id", nargs="?")
    sub.add_parser("inbox")
    sub.add_parser("worker")
    sub.add_parser("doctor", help="check this machine can run Loom with real Claude agents")
    sub.add_parser("sample", help="create an engagement from the files in samples/")
    sub.add_parser("login", help="sign in to Claude (opens your browser); stored for Loom's agents")

    p = sub.add_parser("decide")
    p.add_argument("id")
    p.add_argument("decision", choices=["approve", "revise", "reject", "another_round", "send", "close", "restart"])
    p.add_argument("--notes", default="")
    p.add_argument("--target", type=int)
    p.add_argument("--by", default="reviewer")
    p.add_argument("--assign", action="append", default=[], help="item_id=owner[@meeting]")

    a = ap.parse_args(argv)
    if a.runner:
        config.RUNNER = a.runner

    if a.cmd == "new":
        eid = orchestrator.create(a.title, a.intent, a.sponsor, a.client, a.sector, eid=a.id)
        print(eid)
    elif a.cmd == "add":
        for f in a.files:
            orchestrator.add_input(a.id, a.kind, Path(f).name, Path(f).read_bytes())
        if not a.no_trigger:
            _show(orchestrator.inputs_arrived(a.id, a.kind))
    elif a.cmd == "run":
        _show(orchestrator.advance(a.id))
    elif a.cmd == "decide":
        assignments = []
        for s in a.assign:
            item, rest = s.split("=", 1)
            owner, _, meeting = rest.partition("@")
            assignments.append({"item_id": item, "owner": owner, "meeting": meeting})
        _show(orchestrator.decide(a.id, a.decision, a.by, a.notes, a.target, assignments or None))
    elif a.cmd == "status":
        for eid in [a.id] if a.id else state.list_ids():
            _show(state.load(eid))
    elif a.cmd == "validate":
        bad = 0
        for eid in [a.id] if a.id else state.list_ids():
            problems = state.validate_engagement(eid)
            print(f"{eid}: {'ok' if not problems else f'{len(problems)} problem(s)'}")
            for m in problems:
                print("  -", m)
            bad += bool(problems)
        return 1 if bad else 0
    elif a.cmd == "inbox":
        for row in inbox():
            print(f"{row['since']}  {row['id']:<40} {row['what']}")
    elif a.cmd == "doctor":
        return doctor()
    elif a.cmd == "login":
        return login()
    elif a.cmd == "sample":
        from . import sample

        print(sample.create(start=False))
    elif a.cmd == "worker":
        from . import jobs

        jobs.work_forever()
    return 0


def doctor() -> int:
    """Checks, in plain words, what a live run needs. Exit code 0 means ready."""
    import asyncio
    import shutil
    import sys as _sys

    from . import agents

    ok = True

    def line(good: bool, text: str, fix: str = "") -> None:
        nonlocal ok
        ok = ok and good
        print(("  ok   " if good else "  FIX  ") + text + (f"\n         -> {fix}" if fix and not good else ""))

    logging.getLogger("claude_agent_sdk").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.CRITICAL)
    print("Loom doctor")
    line(_sys.version_info >= (3, 11), f"Python {_sys.version.split()[0]}", "Install Python 3.11 or newer.")
    for d in (config.ENGAGEMENTS_DIR, config.ARCHIVE_DIR, config.JOBS_DIR):
        try:
            d.mkdir(parents=True, exist_ok=True)
            (d / ".write-test").write_text("x")
            (d / ".write-test").unlink()
            line(True, f"Can write {d}")
        except OSError as e:
            line(False, f"Can't write {d}: {e}", "Check folder permissions or set LOOM_ENGAGEMENTS_DIR.")
    missing = sorted({s for a in agents.all_agents() for s in a.skills if not agents.find_skill(s)})
    print(("  ok   " if not missing else "  note ") + ("All skills found" if not missing else
          f"Shared skills not found: {', '.join(missing)}. Agents still run and say so in their notes. "
          f"Looked in .claude/skills and {', '.join(str(p) for p in config.EXTRA_SKILL_DIRS) or '(nothing)'}; "
          "set LOOM_EXTRA_SKILL_DIRS to the folder that holds them."))
    if config.RUNNER != "sdk":
        print("  note Demo mode (LOOM_RUNNER=stub): no Claude calls. Set LOOM_RUNNER=sdk for live work.")
        return 0 if ok else 1
    import os as _os

    how = "ANTHROPIC_API_KEY" if _os.environ.get("ANTHROPIC_API_KEY") else "your Claude Code login"
    try:
        from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

        async def ping() -> str:
            # Read the stream to the end before deciding: raising mid-stream leaves the SDK's
            # generator open and prints a confusing traceback.
            result, error = "", None
            async for m in query(prompt="Reply with exactly: ready",
                                 options=ClaudeAgentOptions(model="haiku", max_turns=1, tools=[], setting_sources=[])):
                if isinstance(m, ResultMessage):
                    result, error = (m.result or ""), ((m.result or m.subtype) if m.is_error else None)
            if error:
                raise RuntimeError(error)
            return result

        reply = asyncio.run(asyncio.wait_for(ping(), timeout=90))
        line("ready" in reply.lower(), f"Claude answered using {how}", "Unexpected reply: " + reply[:80])
    except Exception as e:  # noqa: BLE001 - report any auth or network problem plainly
        msg = str(e)[:200]
        fix = ("Run:  .venv/bin/loom login   (opens your browser to sign in to Claude), or export ANTHROPIC_API_KEY=..."
               if "log" in msg.lower() else "Check your internet connection, or export ANTHROPIC_API_KEY=... and try again.")
        line(False, f"Claude could not be reached using {how}: {msg}", fix)
    print("Ready for live work." if ok else "Fix the items above, or start in demo mode: LOOM_RUNNER=stub ./start.sh")
    return 0 if ok else 1


def login() -> int:
    """Sign in with the Claude Code tool that ships inside the Agent SDK, so the agents Loom
    runs use the same login. Your Claude subscription or Console account both work."""
    import shutil
    import subprocess
    from pathlib import Path

    import claude_agent_sdk

    bundled = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
    exe = str(bundled) if bundled.exists() else shutil.which("claude")
    if not exe:
        print("Couldn't find the Claude sign-in tool. Install Claude Code, or export ANTHROPIC_API_KEY=...")
        return 1
    print("Opening your browser to sign in to Claude. Come back here when it says you're signed in.")
    return subprocess.call([exe, "auth", "login"])


def inbox() -> list[dict]:
    rows = []
    for eid in state.list_ids():
        st = state.load(eid)
        if st["closed"]:
            continue
        if st["status"] == "awaiting_gate" and st.get("gate"):
            g = st["gate"]
            rows.append({"id": eid, "title": st["title"], "since": g["opened_at"], "kind": "gate", "gate": g["gate"],
                         "what": f"Gate {g['gate']} open" + (" (BLOCKED)" if g.get("error") else "")})
        elif st["status"] == "waiting_org" and st.get("waiting"):
            w = st["waiting"]
            rows.append({"id": eid, "title": st["title"], "since": w["since"], "kind": "wait", "gate": None,
                         "what": f"Waiting on {w['kind']}"})
    return sorted(rows, key=lambda r: r["since"])


def _show(st: dict) -> None:
    g = st.get("gate") or {}
    w = st.get("waiting") or {}
    where = f"stage {st['stage']} ({state.STAGES[st['stage']][0]}) {st['status']}"
    if st["closed"]:
        where = "closed"
    elif g:
        where += f" · gate {g['gate']}: {g['question']}" + (f"\n    BLOCKED: {g['error']}" if g.get("error") else "")
    elif w:
        where += f" · waiting on {w['kind']} since {w['since']} {json.dumps(w.get('detail', {}))}"
    print(f"{st['id']}: {where}")


if __name__ == "__main__":
    sys.exit(main())
