"""Notifications fire on two events only: a gate opening and a wait ending.
The transport is a generic JSON webhook so it works with Teams, Slack or any relay."""

from __future__ import annotations

import json
import logging
import urllib.request
from typing import Any

from . import config

log = logging.getLogger("loom.notify")


def send(event: str, st: dict[str, Any], text: str) -> None:
    link = f"{config.APP_BASE_URL.rstrip('/')}/e/{st['id']}"
    payload = {"text": f"Loom · {st['title']}: {text}\n{link}", "event": event, "engagement": st["id"], "link": link}
    if not config.NOTIFY_WEBHOOK:
        log.info("notify (no webhook set): %s", payload["text"])
        return
    try:
        req = urllib.request.Request(
            config.NOTIFY_WEBHOOK,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10).read()
    except Exception:  # noqa: BLE001 - a failed notification must never stall an engagement
        log.exception("notification failed")
