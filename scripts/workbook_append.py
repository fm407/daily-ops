#!/usr/bin/env python3
"""Append one dated line to a control's notes cell in the compliance workbook (sources.workbook).

    python3 workbook_append.py CONTROL "what changed"

This changes the workbook: run it only for an update the user approved. It only accepts a .xlsx
file, refuses when the file is open in a spreadsheet application (a lock file sits beside it),
takes a timestamped backup copy beside the file first, and only ever adds a line to a plain-text
cell; existing text and formulas are never rewritten.

The file is rewritten by openpyxl, which does not keep everything a spreadsheet application can
store (for example some charts and images). Check the first update against the backup.
"""

from __future__ import annotations

import datetime as dt
import fcntl
import os
import pathlib
import shutil
import sys
import zipfile
from typing import Any

from common import ConfigError, load_config

LOCK_PATTERNS = ("~${name}", ".~lock.{name}#")  # Excel, LibreOffice
MAX_CELL_CHARS = 32767  # the most text one cell can hold


def append_line(cfg: dict[str, Any], control: str, text: str, now: dt.datetime | None = None) -> pathlib.Path:
    """Add "<date>: <text>" to the notes cell of the one row whose control matches. Returns the backup."""
    for key in ("path", "sheet", "control_column", "notes_column"):
        if not cfg.get(key):
            raise ConfigError(f"sources.workbook.{key} is not set")
    path = pathlib.Path(str(cfg["path"])).expanduser()
    if path.suffix.lower() != ".xlsx":
        raise ConfigError(
            f"only .xlsx workbooks can be updated, not {path.suffix or 'a file with no extension'}"
        )
    if not path.is_file():
        raise ConfigError(f"workbook not found: {path}")
    if not text.strip():
        raise ValueError("the update text is empty")
    with open(path.with_name(f".{path.name}.lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # one update at a time from this tool (POSIX advisory lock)
        return _append(cfg, path, control, text, now or dt.datetime.now())


def _append(
    cfg: dict[str, Any], path: pathlib.Path, control: str, text: str, now: dt.datetime
) -> pathlib.Path:
    import openpyxl  # optional dependency, only needed for the workbook
    from openpyxl.utils import column_index_from_string
    from openpyxl.utils.exceptions import IllegalCharacterError, InvalidFileException

    for pattern in LOCK_PATTERNS:
        if (path.parent / pattern.format(name=path.name)).exists():
            raise RuntimeError(f"{path.name} is open in a spreadsheet application; close it and try again")
    try:
        book = openpyxl.load_workbook(path)
    except (InvalidFileException, zipfile.BadZipFile) as e:
        raise RuntimeError(f"{path.name} is not a readable .xlsx workbook: {e}") from e
    if str(cfg["sheet"]) not in book.sheetnames:
        raise ConfigError(f"sheet {cfg['sheet']!r} is not in {path.name}")
    sheet = book[str(cfg["sheet"])]
    control_col = column_index_from_string(str(cfg["control_column"]))
    notes_col = column_index_from_string(str(cfg["notes_column"]))
    first = int(cfg.get("header_rows", 1)) + 1
    rows = [
        r
        for r in range(first, sheet.max_row + 1)
        if str(sheet.cell(r, control_col).value or "").strip() == control.strip()
    ]
    if len(rows) != 1:
        raise ValueError(f"expected one row for control {control!r}, found {len(rows)}")

    cell = sheet.cell(rows[0], notes_col)
    if cell.data_type == "f" or str(cell.value or "").startswith("="):
        raise ValueError(f"the notes cell {cell.coordinate} holds a formula; it is not changed")
    line = f"{now:%Y-%m-%d}: {' '.join(text.split())}"
    value = f"{cell.value}\n{line}" if cell.value else line
    if len(value) > MAX_CELL_CHARS:
        raise ValueError(f"the notes cell {cell.coordinate} would exceed {MAX_CELL_CHARS} characters")
    try:
        cell.value = value
    except IllegalCharacterError as e:
        raise ValueError("the update text has a control character a workbook cannot store") from e

    stamp, n = f"{now:%Y%m%d-%H%M%S}", 1
    backup = path.with_name(f"{path.stem}.backup-{stamp}{path.suffix}")
    while backup.exists():  # two updates in one second must not overwrite the earlier backup
        n += 1
        backup = path.with_name(f"{path.stem}.backup-{stamp}-{n}{path.suffix}")
    shutil.copy2(path, backup)
    tmp = path.with_name(f".{path.name}.tmp")
    book.save(tmp)
    os.replace(tmp, path)
    return backup


def main(argv: list[str]) -> None:
    if len(argv) != 2:
        sys.exit(__doc__)
    try:
        cfg = load_config()
        backup = append_line(cfg["sources"].get("workbook") or {}, argv[0], argv[1])
    except (ConfigError, RuntimeError, ValueError, OSError, KeyError) as e:
        sys.exit(f"FAILED: {e}")
    print(f"appended to {argv[0]}; backup {backup}")


if __name__ == "__main__":
    main(sys.argv[1:])
