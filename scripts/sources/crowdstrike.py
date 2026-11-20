"""CrowdStrike Falcon: alerts created since the lookback, through OAuth2 client credentials."""

from __future__ import annotations

import datetime as dt
from typing import Any

from common import Section, capped, clean, request, secret, zulu

MAX_ALERTS = 100


def collect(cfg: dict[str, Any], since: dt.datetime) -> Section:
    base = str(cfg.get("base_url") or "https://api.crowdstrike.com").rstrip("/")
    creds = {"client_id": secret(cfg, "client_id_env"), "client_secret": secret(cfg, "client_secret_env")}
    token = (request("POST", f"{base}/oauth2/token", data=creds) or {}).get("access_token")
    if not token:
        raise RuntimeError("no access token returned; check the API client and its base_url (cloud region)")
    headers = {"Authorization": f"Bearer {token}"}

    params = {"limit": MAX_ALERTS, "filter": f"created_timestamp:>'{zulu(since)}'"}
    found = request("GET", f"{base}/alerts/queries/alerts/v2", headers=headers, params=params) or {}
    ids = found.get("resources") or []
    if not ids:
        return Section("CrowdStrike Falcon", ["No alerts since the last run window."])
    detail = request(
        "POST", f"{base}/alerts/entities/alerts/v2", headers=headers, json={"composite_ids": ids}
    )
    lines = [
        f"- {clean(a.get('severity_name'), 20)} {clean(a.get('display_name') or a.get('name'))} on "
        f"{clean((a.get('device') or {}).get('hostname') or '?', 80)} ({clean(a.get('status'), 30)})"
        for a in (detail or {}).get("resources") or []
    ]
    count = capped(ids, MAX_ALERTS)  # only the first page of alerts is read
    return Section("CrowdStrike Falcon", [f"**Alerts** ({count}, needs the Alerts: Read scope)", *lines])
