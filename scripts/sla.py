#!/usr/bin/env python3
"""Due date for a finding ticket: it follows the highest severity still open.

    python3 sla.py 2026-01-10 High Medium     # first reported, then every severity still open

Counted from the day the findings were first reported. A ticket with a Critical still open keeps
the Critical date; once the Criticals are fixed the date moves to the next severity's SLA.
A severity with no entry in sla_days is an error unless it is listed in sla_ignore.
"""

from __future__ import annotations

import datetime as dt
import sys
from collections.abc import Iterable, Mapping

from common import ConfigError, load_config


def highest_open(
    open_severities: Iterable[str], sla_days: Mapping[str, int], ignore: Iterable[str] = ()
) -> str | None:
    """The open severity with the shortest SLA (ties: alphabetical). Unknown severities raise ValueError."""
    names = {name.lower(): name for name in sla_days}
    ignored = {name.lower() for name in ignore}
    wanted = {s.strip().lower() for s in open_severities if s.strip()} - ignored
    unknown = sorted(wanted - set(names))
    if unknown:
        raise ValueError(f"no SLA for severity {', '.join(unknown)}: add it to sla_days or to sla_ignore")
    return min((names[s] for s in wanted), key=lambda name: (sla_days[name], name), default=None)


def due_date(
    first_reported: dt.date,
    open_severities: Iterable[str],
    sla_days: Mapping[str, int],
    ignore: Iterable[str] = (),
) -> dt.date | None:
    """First reported date plus the SLA of the highest severity still open; None if every one is ignored."""
    top = highest_open(open_severities, sla_days, ignore)
    return first_reported + dt.timedelta(days=sla_days[top]) if top else None


def main(argv: list[str]) -> None:
    if len(argv) < 2:
        sys.exit(__doc__)
    try:
        cfg = load_config()
        sla_days, ignore = cfg["sla_days"], cfg["sla_ignore"]
        reported = dt.date.fromisoformat(argv[0])
        top = highest_open(argv[1:], sla_days, ignore)
    except (ConfigError, ValueError) as e:
        sys.exit(f"FAILED: {e}")
    if not top:
        sys.exit(f"FAILED: every severity given is in sla_ignore {list(ignore)}, so no SLA date applies")
    print(f"{due_date(reported, argv[1:], sla_days, ignore)} ({top}, {sla_days[top]} days from {reported})")


if __name__ == "__main__":
    main(sys.argv[1:])
