"""KnowBe4: training and acknowledgements still outstanding for active users."""

from __future__ import annotations

import datetime as dt
from typing import Any

from common import Section, clean, request, secret

PAGE_SIZE = 500
MAX_PAGES = 50
FINISHED = {"Completed", "Closed"}


def _all(base: str, headers: dict[str, str], path: str, **params: Any) -> list[dict]:
    rows: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        query = {**params, "per_page": PAGE_SIZE, "page": page}
        batch = request("GET", f"{base}/v1/{path}", headers=headers, params=query) or []
        rows += batch
        if len(batch) < PAGE_SIZE:
            break
    return rows


def collect(cfg: dict[str, Any], since: dt.datetime) -> Section:
    base = str(cfg.get("base_url") or "https://us.api.knowbe4.com").rstrip("/")
    headers = {"Authorization": f"Bearer {secret(cfg, 'token_env')}", "Accept": "application/json"}
    finished = {
        c.get("name") for c in _all(base, headers, "training/campaigns") if c.get("status") in FINISHED
    }
    users = _all(base, headers, "users", status="active")
    active = {str(u["email"]).lower() for u in users if u.get("email")}

    # the campaigns endpoint can omit campaigns, so every campaign seen in enrolments counts as live
    # unless it is listed as finished
    pending: dict[tuple[str, str], int] = {}
    for e in _all(base, headers, "training/enrollments"):
        email = str((e.get("user") or {}).get("email") or "").lower()  # no email never matches
        campaign = e.get("campaign_name")
        if campaign not in finished and e.get("status") != "Passed" and email in active:
            key = (clean(campaign, 80), clean(email, 80))
            pending[key] = pending.get(key, 0) + 1
    lines = [f"**Training outstanding** ({len(pending)})"]
    lines += [f"- {email}: {n} item(s) in {campaign}" for (campaign, email), n in sorted(pending.items())]
    return Section("KnowBe4", lines)
