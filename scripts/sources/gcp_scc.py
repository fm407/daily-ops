"""Google Cloud Security Command Center findings, read through the gcloud CLI (your gcloud login)."""

from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
from typing import Any

from common import ConfigError, Section, clean, parse_time, zulu

GCLOUD_TIMEOUT = 180  # seconds
ACTIVE_UNMUTED = 'state="ACTIVE" AND NOT mute="MUTED"'


def _findings(cfg: dict[str, Any], flt: str) -> list[dict]:
    org = str(cfg.get("organization_id") or "")
    if not org.isdigit():
        raise ConfigError("gcp_scc.organization_id must be the numeric organization id")
    cmd = ["gcloud", "scc", "findings", "list", f"organizations/{org}", "--location=global", "--quiet"]
    if cfg.get("billing_project"):
        cmd.append(f"--billing-project={cfg['billing_project']}")
    r = subprocess.run(  # argument list, no shell, no prompt can block it
        [*cmd, f"--filter={flt}", "--format=json"],
        capture_output=True,
        text=True,
        timeout=GCLOUD_TIMEOUT,
        stdin=subprocess.DEVNULL,
    )
    if r.returncode:
        if "Reauthentication" in r.stderr or "gcloud auth login" in r.stderr:
            raise RuntimeError("gcloud needs a fresh sign-in: run gcloud auth login")
        raise RuntimeError(clean(r.stderr, 300))
    return [f.get("finding", f) for f in json.loads(r.stdout or "[]") if isinstance(f, dict)]


def collect(cfg: dict[str, Any], since: dt.datetime) -> Section:
    # event_time narrows the query on the server; a finding is NEW only if it was created in the
    # window, because a long-standing finding gets a fresh event time on every scan
    flt = f'{ACTIVE_UNMUTED} AND (severity="HIGH" OR severity="CRITICAL") AND event_time >= "{zulu(since)}"'
    lines = [
        f"- NEW {clean(f.get('severity'), 10)} {clean(f.get('category'), 80)} "
        f"({clean(f.get('resourceName'))[-60:]})"
        for f in _findings(cfg, flt)
        if (parse_time(f.get("createTime")) or since) >= since
    ]
    watch = [str(c) for c in cfg.get("watch_categories") or []]
    if any(not re.fullmatch(r"[A-Z0-9_]+", c) for c in watch):
        raise ConfigError("gcp_scc.watch_categories must be finding category names such as MFA_NOT_ENFORCED")
    if watch:
        categories = " OR ".join(f'category="{c}"' for c in watch)
        lines += [
            f"- STILL ACTIVE {clean(f.get('category'), 80)} (last scan {clean(f.get('eventTime'), 10)})"
            for f in _findings(cfg, f"{ACTIVE_UNMUTED} AND ({categories})")
        ]
    return Section(
        "Google Cloud Security Command Center",
        lines or ["No new high or critical findings; watched categories are clear."],
    )
