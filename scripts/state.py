#!/usr/bin/env python3
"""What was already decided, so a rerun asks only about new or changed items.

    python3 state.py show                              # today's decisions plus all drops, as JSON
    python3 state.py record ID OUTCOME FINGERPRINT "TITLE"

ID          stable item id: jira:SECOPS-1, email:<threadId>, chat:<channel>:<ts>,
            workbook:<control>, training:<user>, device:<hostname>, other:<slug>
OUTCOME     approved | changed | skipped | dropped
FINGERPRINT what the item looked like when decided (latest comment id, last message id,
            status...). A different fingerprint on a later run means the item changed.

Decisions live in <output_dir>/<today>/decisions.json. Drops also go to <output_dir>/dropped.json,
so they stay dropped on later days until their fingerprint changes. Writes are atomic and
serialised with a lock file; a file that is not valid JSON is set aside, never overwritten.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import json
import os
import pathlib
import sys
from collections.abc import Iterator
from typing import Any

from common import ConfigError, day_dir, load_config, output_dir

OUTCOMES = {"approved", "changed", "skipped", "dropped"}


@contextlib.contextmanager
def _locked(cfg: dict[str, Any]) -> Iterator[None]:
    """One reader or writer at a time (POSIX advisory lock)."""
    root = output_dir(cfg)
    root.mkdir(parents=True, exist_ok=True)
    with open(root / ".state.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _load(p: pathlib.Path) -> dict[str, Any]:
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
        if isinstance(data, dict):
            return data
    except ValueError:
        pass
    aside = p.with_name(f"{p.name}.corrupt-{dt.datetime.now():%Y%m%d-%H%M%S}")
    p.rename(aside)
    print(f"state: {p.name} was not valid, kept as {aside.name}; starting empty", file=sys.stderr)
    return {}


def _save(p: pathlib.Path, data: dict[str, Any]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    os.replace(tmp, p)


def show(cfg: dict[str, Any], today: dt.date | None = None) -> dict[str, Any]:
    with _locked(cfg):
        return {
            "today": _load(day_dir(cfg, today) / "decisions.json"),
            "dropped": _load(output_dir(cfg) / "dropped.json"),
        }


def record(
    cfg: dict[str, Any], item: str, outcome: str, fingerprint: str, title: str, now: dt.datetime | None = None
) -> dict[str, str]:
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {sorted(OUTCOMES)}")
    now = now or dt.datetime.now()
    entry = {
        "outcome": outcome,
        "fingerprint": fingerprint,
        "title": title,
        "at": now.isoformat(timespec="minutes"),
    }
    files = [day_dir(cfg, now.date()) / "decisions.json"]
    if outcome == "dropped":
        files.append(output_dir(cfg) / "dropped.json")
    with _locked(cfg):
        for p in files:
            _save(p, {**_load(p), item: entry})
    return entry


def main(argv: list[str]) -> None:
    try:
        cfg = load_config()
        if argv == ["show"]:
            print(json.dumps(show(cfg), indent=1, ensure_ascii=False))
            return
        if len(argv) != 5 or argv[0] != "record" or argv[2] not in OUTCOMES:
            sys.exit(__doc__)
        record(cfg, *argv[1:])
    except (ConfigError, OSError, ValueError) as e:
        sys.exit(f"FAILED: {e}")
    print(f"recorded {argv[1]}: {argv[2]}")


if __name__ == "__main__":
    main(sys.argv[1:])
