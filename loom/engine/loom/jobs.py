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
OPS = ("advance", "decide", "inputs", "ask")


def _dirs() -> dict[str, Path]:
    d = {k: config.JOBS_DIR / k for k in ("pending", "running", "done", "failed")}
    for p in d.values():
        p.mkdir(parents=True, exist_ok=True)
    return d


def enqueue(op: str, eid: str, delay: float = 0, **args: Any) -> str:
    """delay > 0 holds the job back so a decision can be undone until its next step starts."""
    if op not in OPS:
        raise ValueError(op)
    jid = f"{time.time_ns()}-{uuid.uuid4().hex[:8]}"
    tmp = _dirs()["pending"] / f".{jid}.tmp"
    job = {"id": jid, "op": op, "eid": eid, "args": args, "created": time.time(), "not_before": time.time() + delay}
    tmp.write_text(json.dumps(job))
    os.replace(tmp, _dirs()["pending"] / f"{jid}.json")
    return jid


def jobs_for(eid: str) -> list[dict[str, Any]]:
    """Queued and running jobs for one engagement, oldest first, each with its 'where'."""
    out = []
    for sub in ("pending", "running"):
        for p in _dirs()[sub].glob("*.json"):
            try:
                job = json.loads(p.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if job.get("eid") == eid:
                job["where"] = sub
                out.append(job)
    return sorted(out, key=lambda j: j["id"])


def pending_for(eid: str) -> int:
    return len(jobs_for(eid))


def cancel(jid: str) -> bool:
    """Undo: remove a job that has not started. False if it already started."""
    try:
        (_dirs()["pending"] / f"{jid}.json").unlink()
        return True
    except FileNotFoundError:
        return False


def claim() -> tuple[Path, dict[str, Any]] | None:
    d = _dirs()
    now = time.time()
    for p in sorted(d["pending"].glob("*.json")):
        try:
            if json.loads(p.read_text()).get("not_before", 0) > now:
                continue
        except (OSError, json.JSONDecodeError):
            continue
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
    elif op == "ask":
        from . import ask

        ask.answer(eid, a["question"])


def recover() -> None:
    """Jobs left in running/ by a crashed worker go back to pending on start-up."""
    d = _dirs()
    for p in d["running"].glob("*.json"):
        os.replace(p, d["pending"] / p.name)


def work_forever(poll_seconds: float = 2.0) -> None:
    """Runs jobs on a small thread pool: engagements progress side by side and a question
    typed into the conversation is answered while a long step is still running. Jobs for the
    same engagement serialise on the engagement lock."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    recover()
    log.info("worker started, runner=%s, root=%s, threads=%s", config.RUNNER, config.ROOT, config.WORKER_THREADS)
    slots = threading.Semaphore(config.WORKER_THREADS)
    busy: set[str] = set()
    lock = threading.Lock()

    def run(path: Path, job: dict[str, Any], key: str) -> None:
        try:
            log.info("job %s %s %s", job["id"], job["op"], job["eid"])
            run_job(job)
            os.replace(path, _dirs()["done"] / path.name)
        except Exception:  # noqa: BLE001 - keep the worker alive, keep the evidence
            job["error"] = traceback.format_exc()
            path.write_text(json.dumps(job))
            os.replace(path, _dirs()["failed"] / path.name)
            log.exception("job %s failed", job["id"])
        finally:
            with lock:
                busy.discard(key)
            slots.release()

    with ThreadPoolExecutor(max_workers=config.WORKER_THREADS, thread_name_prefix="loom-job") as pool:
        while True:
            slots.acquire()
            got = _claim_free(busy, lock)
            if not got:
                slots.release()
                time.sleep(poll_seconds)
                continue
            path, job, key = got
            pool.submit(run, path, job, key)


def _claim_free(busy: set[str], lock: Any) -> tuple[Path, dict[str, Any], str] | None:
    """Claim the oldest due job whose engagement is not already busy. Questions ('ask') are
    keyed separately so they never wait behind a running step."""
    d = _dirs()
    now = time.time()
    for p in sorted(d["pending"].glob("*.json")):
        try:
            job = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if job.get("not_before", 0) > now:
            continue
        key = f"{job['eid']}:{'ask' if job['op'] == 'ask' else 'flow'}"
        with lock:
            if key in busy:
                continue
            dest = d["running"] / p.name
            try:
                os.replace(p, dest)
            except FileNotFoundError:
                continue
            busy.add(key)
        return dest, job, key
    return None
