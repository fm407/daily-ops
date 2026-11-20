"""GitHub: pull requests awaiting your review, Dependabot alerts, code scanning alerts."""

from __future__ import annotations

import datetime as dt
from typing import Any
from urllib.parse import quote

from common import Section, capped, clean, request, run_checks, secret

DEFAULT_CHECKS = ["reviews", "dependabot", "code_scanning"]
PAGE_SIZE = 100  # first page only; a full page is shown as "100+"


def _reviews(base: str, headers: dict[str, str], user: str, org: str) -> list[str]:
    q = f"is:pr is:open review-requested:{user}" + (f" org:{org}" if org else "")
    found = request("GET", f"{base}/search/issues", headers=headers, params={"q": q, "per_page": PAGE_SIZE})
    prs = (found or {}).get("items") or []
    return [f"**Pull requests waiting on your review** ({capped(prs, PAGE_SIZE)})"] + [
        f"- {clean(p.get('title'))} ({clean((p.get('user') or {}).get('login'), 40)}) "
        f"{clean(p.get('html_url'))}"
        for p in prs
    ]


def _dependabot(org_url: str, headers: dict[str, str]) -> list[str]:
    params = {"state": "open", "severity": "critical,high", "per_page": PAGE_SIZE}
    alerts = request("GET", f"{org_url}/dependabot/alerts", headers=headers, params=params) or []
    return [f"**Open critical and high Dependabot alerts** ({capped(alerts, PAGE_SIZE)})"] + [
        f"- {clean((a.get('security_advisory') or {}).get('severity'), 10)} "
        f"{clean((a.get('repository') or {}).get('name'), 60)}: "
        f"{clean((a.get('security_advisory') or {}).get('summary'))} {clean(a.get('html_url'))}"
        for a in alerts
    ]


def _code_scanning(org_url: str, headers: dict[str, str]) -> list[str]:
    lines: list[str] = []
    for severity in ("critical", "high"):  # the endpoint takes one severity per call
        params = {"state": "open", "severity": severity, "per_page": PAGE_SIZE}
        alerts = request("GET", f"{org_url}/code-scanning/alerts", headers=headers, params=params) or []
        lines.append(f"**Open {severity} code scanning alerts** ({capped(alerts, PAGE_SIZE)})")
        lines += [
            f"- {clean((a.get('repository') or {}).get('name'), 60)}: "
            f"{clean((a.get('rule') or {}).get('description'))} {clean(a.get('html_url'))}"
            for a in alerts
        ]
    return lines


def collect(cfg: dict[str, Any], since: dt.datetime) -> Section:
    base = str(cfg.get("base_url") or "https://api.github.com").rstrip("/")
    headers = {
        "Authorization": f"Bearer {secret(cfg, 'token_env')}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    wanted = cfg.get("checks") or DEFAULT_CHECKS
    org = str(cfg.get("org") or "")
    org_url = f"{base}/orgs/{quote(org, safe='')}"
    available = {
        "reviews": lambda: _reviews(base, headers, str(cfg["username"]), org),
        "dependabot": lambda: _dependabot(org_url, headers),
        "code_scanning": lambda: _code_scanning(org_url, headers),
    }
    lines: list[str] = []
    if not org and set(wanted) & {"dependabot", "code_scanning"}:
        lines.append("Organisation checks skipped: no org is configured.")
        wanted = [c for c in wanted if c not in {"dependabot", "code_scanning"}]
    lines += run_checks(wanted, available)
    return Section("GitHub", lines)
