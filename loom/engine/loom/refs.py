"""Traceability checks: an evidence_ref or gap_ref must point at a real id in a real file,
so any claim in a spec can be walked back to the transcript line or survey answer."""

from __future__ import annotations

from typing import Any

from . import artifacts
from .state import edir


def _ids(node: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        if isinstance(node.get("id"), str):
            found.add(node["id"])
        for v in node.values():
            found |= _ids(v)
    elif isinstance(node, list):
        for v in node:
            found |= _ids(v)
    return found


def resolves(eid: str, ref: str) -> bool:
    if "#" not in ref:
        return False
    rel, anchor = ref.split("#", 1)
    path = edir(eid) / rel
    if not path.exists():
        return False
    try:
        meta, _ = artifacts.read(path)
    except artifacts.ArtifactError:
        return False
    return anchor in _ids(meta)


def broken_refs(eid: str, refs: list[str]) -> list[str]:
    return [r for r in refs if not resolves(eid, r)]


def ids_in(eid: str, rel: str) -> set[str]:
    path = edir(eid) / rel
    if not path.exists():
        return set()
    meta, _ = artifacts.read(path)
    return _ids(meta)
