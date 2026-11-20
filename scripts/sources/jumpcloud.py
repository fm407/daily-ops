"""JumpCloud: devices not seen recently and devices without active disk encryption."""

from __future__ import annotations

import datetime as dt
from typing import Any

from common import Section, clean, parse_time, request, secret

PAGE_SIZE = 100
MAX_PAGES = 100
DEFAULT_BASE = "https://console.jumpcloud.com"  # EU tenants: https://console.eu.jumpcloud.com
ENCRYPTION_REPORTED = ("darwin", "mac", "windows")  # disk encryption state is only reported for these


def _reports_encryption(system: dict) -> bool:
    family = f"{system.get('osFamily') or ''} {system.get('os') or ''}".lower()
    return any(name in family for name in ENCRYPTION_REPORTED)


def collect(cfg: dict[str, Any], since: dt.datetime) -> Section:
    url = str(cfg.get("base_url") or DEFAULT_BASE).rstrip("/") + "/api/systems"
    headers = {"x-api-key": secret(cfg, "api_key_env"), "Accept": "application/json"}
    stale_days = int(cfg.get("stale_days", 3))
    systems: list[dict] = []
    for page in range(MAX_PAGES):
        params = {"limit": PAGE_SIZE, "skip": page * PAGE_SIZE}
        batch = (request("GET", url, headers=headers, params=params) or {}).get("results") or []
        systems += batch
        if len(batch) < PAGE_SIZE:
            break

    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=stale_days)
    # a device connected right now is never stale, whatever its last recorded contact says
    stale = [
        s for s in systems if not s.get("active") and (parse_time(s.get("lastContact")) or cutoff) < cutoff
    ]
    reporting = [s for s in systems if _reports_encryption(s)]
    unencrypted = [s for s in reporting if not (s.get("fde") or {}).get("active")]
    unknown = len(systems) - len(reporting)

    def name(s: dict) -> str:
        return clean(s.get("displayName") or s.get("hostname"), 80)

    lines = [f"{len(systems)} devices.", f"**Not seen for {stale_days}+ days** ({len(stale)})"]
    lines += [f"- {name(s)}: last contact {clean(s.get('lastContact'), 10)}" for s in stale]
    lines += [
        f"**Disk encryption not active** ({len(unencrypted)} of {len(reporting)} macOS and Windows devices)"
    ]
    lines += [f"- {name(s)} ({clean(s.get('os'), 40)})" for s in unencrypted]
    if unknown:
        lines.append(f"Disk encryption unknown for {unknown} other device(s): not reported for their OS.")
    return Section("JumpCloud devices", lines)
