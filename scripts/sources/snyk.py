"""Snyk: open critical and high issues for an organisation, through the Snyk REST API."""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from common import ConfigError, Section, clean, parse_time, request, secret

API_VERSION = "2024-10-15"
PAGE_SIZE = 100  # first page only; more is shown as "100+"


def collect(cfg: dict[str, Any], since: dt.datetime) -> Section:
    base = str(cfg.get("base_url") or "https://api.snyk.io").rstrip("/")
    org = str(cfg.get("org_id") or "")
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", org):  # it goes into the URL path
        raise ConfigError("snyk.org_id must be the organisation's UUID")
    headers = {"Authorization": f"token {secret(cfg, 'token_env')}", "Accept": "application/vnd.api+json"}
    params = {
        "version": API_VERSION,
        "effective_severity_level": "critical,high",
        "status": "open",
        "limit": PAGE_SIZE,
    }
    page = request("GET", f"{base}/rest/orgs/{org}/issues", headers=headers, params=params)
    issues = page.get("data") if isinstance(page, dict) else None
    if not isinstance(issues, list):
        raise RuntimeError("unexpected response shape: data is missing")  # never report a false 0
    more = "+" if (page.get("links") or {}).get("next") else ""
    # an ignored issue stays "open" with ignored: true; accepted risk is not a finding to chase
    attrs = [i.get("attributes") or {} if isinstance(i, dict) else {} for i in issues]
    attrs = [a for a in attrs if a.get("ignored") is not True]
    lines = [f"**Open critical and high issues, not ignored** ({len(attrs)}{more})"]
    for a in attrs:
        created = parse_time(a.get("created_at"))
        new = " NEW" if created and created >= since else ""
        lines.append(
            f"-{new} {clean(a.get('effective_severity_level'), 10)} {clean(a.get('type'), 30)}: "
            f"{clean(a.get('title'))}"
        )
    return Section("Snyk", lines)
