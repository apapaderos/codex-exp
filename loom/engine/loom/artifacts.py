"""Markdown-with-frontmatter artifacts and their schema validation.

The frontmatter is the contract a downstream agent relies on; the body is for humans.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from . import config


class ArtifactError(Exception):
    pass


def read(path: Path) -> tuple[dict[str, Any], str]:
    text = Path(path).read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise ArtifactError(f"{path}: no YAML frontmatter (file must start with ---)")
    parts = text.split("\n---", 1)
    if len(parts) != 2:
        raise ArtifactError(f"{path}: frontmatter is not closed with ---")
    head = parts[0][3:]
    body = parts[1].lstrip("-").lstrip("\n")
    try:
        meta = yaml.safe_load(head) or {}
    except yaml.YAMLError as e:
        raise ArtifactError(f"{path}: frontmatter is not valid YAML: {e}") from e
    if not isinstance(meta, dict):
        raise ArtifactError(f"{path}: frontmatter must be a mapping")
    return meta, body


def write(path: Path, meta: dict[str, Any], body: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    head = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, width=100)
    path.write_text(f"---\n{head}---\n\n{body.strip()}\n", encoding="utf-8")


@lru_cache(maxsize=None)
def schema(name: str) -> dict[str, Any]:
    return json.loads((config.SCHEMAS_DIR / f"{name}.json").read_text())


def errors_for(data: Any, schema_name: str) -> list[str]:
    v = Draft202012Validator(schema(schema_name))
    out = []
    for e in sorted(v.iter_errors(_jsonable(data)), key=lambda e: list(e.path)):
        loc = "/".join(str(p) for p in e.path) or "(root)"
        out.append(f"{loc}: {e.message}")
    return out


def validate_file(path: Path, schema_name: str) -> list[str]:
    """Return a list of human-readable problems; empty means valid."""
    path = Path(path)
    if not path.exists():
        return [f"{path.name}: missing"]
    try:
        meta, _ = read(path)
    except ArtifactError as e:
        return [str(e)]
    return [f"{path.name}: {m}" for m in errors_for(meta, schema_name)]


def _jsonable(x: Any) -> Any:
    """YAML turns 2026-10-01 into a date object; schemas expect strings."""
    import datetime as dt

    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_jsonable(v) for v in x]
    if isinstance(x, (dt.date, dt.datetime)):
        return x.isoformat()
    return x
