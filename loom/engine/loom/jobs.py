"""A durable job queue on the shared volume, so agent runs outlive a browser tab.

Deliberately file-based: no extra infrastructure, works on any cloud with a shared disk.
Claiming a job is an atomic rename. Swap for a managed queue (Service Bus, SQS, Cloud
Tasks) by reimplementing enqueue() and claim() only.
"""

from __future__ import annotations

import json
import logging
import os
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

from . import config, orchestrator

log = logging.getLogger("loom.jobs")
OPS = ("advance", "decide", "inputs")


def _dirs() -> dict[str, Path]:
    d = {k: config.JOBS_DIR / k for k in ("pending", "running", "done", "failed")}
    for p in d.values():
        p.mkdir(parents=True, exist_ok=True)
    return d


def enqueue(op: str, eid: str, **args: Any) -> str:
    if op not in OPS:
        raise ValueError(op)
    jid = f"{time.time_ns()}-{uuid.uuid4().hex[:8]}"
    tmp = _dirs()["pending"] / f".{jid}.tmp"
    tmp.write_text(json.dumps({"id": jid, "op": op, "eid": eid, "args": args}))
    os.replace(tmp, _dirs()["pending"] / f"{jid}.json")
    return jid


def pending_for(eid: str) -> int:
    n = 0
    for sub in ("pending", "running"):
        for p in _dirs()[sub].glob("*.json"):
            try:
                n += json.loads(p.read_text()).get("eid") == eid
            except (OSError, json.JSONDecodeError):
                pass
    return n


def claim() -> tuple[Path, dict[str, Any]] | None:
    d = _dirs()
    for p in sorted(d["pending"].glob("*.json")):
        dest = d["running"] / p.name
        try:
            os.replace(p, dest)
        except FileNotFoundError:
            continue  # another worker took it
        return dest, json.loads(dest.read_text())
    return None


def run_job(job: dict[str, Any]) -> None:
    op, eid, a = job["op"], job["eid"], job.get("args", {})
    if op == "advance":
        orchestrator.advance(eid)
    elif op == "decide":
        orchestrator.decide(eid, a["decision"], a.get("by", "human"), a.get("notes", ""),
                            a.get("target_stage"), a.get("assignments"))
    elif op == "inputs":
        orchestrator.inputs_arrived(eid, a["kind"])


def recover() -> None:
    """Jobs left in running/ by a crashed worker go back to pending on start-up."""
    d = _dirs()
    for p in d["running"].glob("*.json"):
        os.replace(p, d["pending"] / p.name)


def work_forever(poll_seconds: float = 2.0) -> None:
    recover()
    log.info("worker started, runner=%s, root=%s", config.RUNNER, config.ROOT)
    while True:
        got = claim()
        if not got:
            time.sleep(poll_seconds)
            continue
        path, job = got
        log.info("job %s %s %s", job["id"], job["op"], job["eid"])
        try:
            run_job(job)
            os.replace(path, _dirs()["done"] / path.name)
        except Exception:  # noqa: BLE001 - keep the worker alive, keep the evidence
            job["error"] = traceback.format_exc()
            path.write_text(json.dumps(job))
            os.replace(path, _dirs()["failed"] / path.name)
            log.exception("job %s failed", job["id"])
