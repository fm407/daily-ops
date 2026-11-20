"""Optional compliance workbook (xlsx): rows that are still open, with those due soon listed."""

from __future__ import annotations

import datetime as dt
import pathlib
from typing import Any

from common import Section, clean

DUE_SOON_DAYS = 7


def _date(value: Any) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def collect(cfg: dict[str, Any], since: dt.datetime) -> Section:
    import openpyxl  # optional dependency, only needed when this source is enabled
    from openpyxl.utils import column_index_from_string

    def column(key: str) -> int | None:
        return column_index_from_string(str(cfg[key])) - 1 if cfg.get(key) else None

    def cell(row: tuple, index: int | None) -> Any:
        return row[index] if index is not None and index < len(row) else None

    control, status, target = column("control_column"), column("status_column"), column("target_date_column")
    closed = [str(v).lower() for v in cfg.get("closed_values") or ["closed", "done"]]
    book = openpyxl.load_workbook(pathlib.Path(cfg["path"]).expanduser(), read_only=True, data_only=True)
    try:
        rows = list(
            book[cfg["sheet"]].iter_rows(min_row=int(cfg.get("header_rows", 1)) + 1, values_only=True)
        )
    finally:
        book.close()

    open_rows = [
        r
        for r in rows
        if cell(r, control) and not str(cell(r, status) or "").lower().startswith(tuple(closed))
    ]
    limit = dt.date.today() + dt.timedelta(days=DUE_SOON_DAYS)
    soon = [r for r in open_rows if (_date(cell(r, target)) or dt.date.max) <= limit]
    lines = [
        f"{len(open_rows)} open rows; {len(soon)} with a target date within {DUE_SOON_DAYS} days or past."
    ]
    lines += [
        f"- {clean(cell(r, control), 60)}: target {_date(cell(r, target))}, "
        f"status {clean(cell(r, status), 60) or '-'}"
        for r in soon
    ]
    return Section("Compliance workbook", lines)
