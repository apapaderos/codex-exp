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
    elif a.cmd == "worker":
        from . import jobs

        jobs.work_forever()
    return 0


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
