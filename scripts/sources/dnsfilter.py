"""DNSFilter: number of threat lookups blocked since the lookback."""

from __future__ import annotations

import datetime as dt
from typing import Any

from common import Section, request, secret, zulu

API = "https://api.dnsfilter.com/v1/traffic_reports/total_threats"


def collect(cfg: dict[str, Any], since: dt.datetime) -> Section:
    params = {
        "organization_id": cfg["organization_id"],
        "from": zulu(since),
        "to": zulu(dt.datetime.now(dt.UTC)),
    }
    # the API key goes in the Authorization header as it is, with no "Bearer" prefix
    report = request("GET", API, headers={"Authorization": secret(cfg, "token_env")}, params=params)
    values = (report.get("data") or {}).get("values") if isinstance(report, dict) else None
    if not isinstance(values, list) or not all(
        isinstance(v, dict) and isinstance(v.get("total"), int) for v in values
    ):
        raise RuntimeError(
            "unexpected response shape: data.values[].total is missing"
        )  # never report a false 0
    total = sum(v["total"] for v in values)
    return Section("DNSFilter", [f"{total} threat lookups blocked (detail in the DNSFilter console)."])
