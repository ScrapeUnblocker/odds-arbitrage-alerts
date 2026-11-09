"""What has been seen and delivered, kept in one JSON file.

``prices`` holds, per selection, the best price it is measured against (the price when
first seen, or when last alerted) and the day it was last seen. ``arbs`` holds the open
arbitrages already delivered. Events that drop off the pages are forgotten after a few
days. In GitHub Actions the file is carried from run to run in the Actions cache.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

STATE_VERSION = 1
SECTIONS = ("prices", "arbs")


class StateError(ValueError):
    """The state file exists but cannot be read."""


def empty_state() -> dict[str, dict[str, Any]]:
    return {name: {} for name in SECTIONS}


def load_state(path: str | Path) -> dict[str, dict[str, Any]]:
    """Return ``{"prices": {...}, "arbs": {...}}``; empty sections when there is no file."""
    path = Path(path)
    if not path.exists():
        return empty_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StateError(f"cannot read state file {path}: {exc}") from None
    if not isinstance(data, dict):
        raise StateError(f"state file {path} is not a JSON object")
    state = empty_state()
    for name in SECTIONS:
        section = data.get(name, {})
        if not isinstance(section, dict):
            raise StateError(f"state file {path}: '{name}' must be a mapping")
        state[name] = section
    return state


def dump_state(state: dict[str, dict[str, Any]]) -> str:
    payload: dict[str, Any] = {"version": STATE_VERSION}
    for name in SECTIONS:
        payload[name] = dict(sorted((state.get(name) or {}).items()))
    return json.dumps(payload, indent=1, ensure_ascii=False) + "\n"


def save_state(path: str | Path, state: dict[str, dict[str, Any]]) -> bool:
    """Write the state atomically. Returns False when the file already had this content."""
    path = Path(path)
    text = dump_state(state)
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)
    return True
