"""GitLab: merge requests awaiting your review, the group vulnerability report, expiring group tokens."""

from __future__ import annotations

import datetime as dt
from typing import Any
from urllib.parse import quote

from common import Section, capped, clean, parse_time, request, run_checks, secret

DEFAULT_CHECKS = ["reviews", "vulnerabilities", "tokens"]
PAGE_SIZE = 100  # first page only; a full page is shown as "100+"
VULN_QUERY = """query($group: ID!) { group(fullPath: $group) {
  vulnerabilities(severity: [CRITICAL, HIGH], state: [DETECTED], sort: detected_desc, first: 100) {
    nodes { title severity detectedAt webUrl project { name } } } } }"""


def _reviews(base: str, headers: dict[str, str], cfg: dict[str, Any]) -> list[str]:
    params = {"scope": "all", "state": "opened", "reviewer_username": cfg["username"], "per_page": PAGE_SIZE}
    mrs = request("GET", f"{base}/api/v4/merge_requests", headers=headers, params=params) or []
    return [f"**Merge requests waiting on your review** ({capped(mrs, PAGE_SIZE)})"] + [
        f"- {clean((m.get('references') or {}).get('full'), 80)} {clean(m.get('title'))} "
        f"({clean((m.get('author') or {}).get('username'), 40)}) {clean(m.get('web_url'))}"
        for m in mrs
    ]


def _vulnerabilities(base: str, headers: dict[str, str], group: str, since: dt.datetime) -> list[str]:
    """Needs the group vulnerability report (GitLab Ultimate). Newest first, so "new" is reliable."""
    body = {"query": VULN_QUERY, "variables": {"group": group}}
    payload = request("POST", f"{base}/api/graphql", headers=headers, json=body) or {}
    if payload.get("errors"):
        raise RuntimeError(f"GraphQL error: {clean(payload['errors'], 200)}")
    found = (payload.get("data") or {}).get("group")
    if not isinstance(found, dict):
        raise RuntimeError("group not found or not visible to this token")
    vulns = (found.get("vulnerabilities") or {}).get("nodes") or []
    new = [v for v in vulns if (parse_time(v.get("detectedAt")) or since) > since]
    return [
        f"**Critical and high vulnerabilities**: {capped(vulns, PAGE_SIZE)} open, {len(new)} new among them"
    ] + [
        f"- {clean(v.get('severity'), 10)} {clean((v.get('project') or {}).get('name'), 60)}: "
        f"{clean(v.get('title'))} {clean(v.get('webUrl'))}"
        for v in new
    ]


def _tokens(base: str, headers: dict[str, str], group: str, within: int) -> list[str]:
    """Group access tokens are only listed for a token whose user is an Owner of the group."""
    limit = (dt.date.today() + dt.timedelta(days=within)).isoformat()
    url = f"{base}/api/v4/groups/{quote(group, safe='')}/access_tokens"
    tokens = request("GET", url, headers=headers, params={"per_page": PAGE_SIZE}) or []
    soon = [
        t
        for t in tokens
        if t.get("active") and not t.get("revoked") and str(t.get("expires_at") or "9999") <= limit
    ]
    return [f"**Group tokens expiring within {within} days** ({len(soon)})"] + [
        f"- {clean(t.get('name'), 80)} expires {clean(t.get('expires_at'), 10)}" for t in soon
    ]


def collect(cfg: dict[str, Any], since: dt.datetime) -> Section:
    base = str(cfg.get("base_url") or "https://gitlab.com").rstrip("/")
    headers = {"Authorization": f"Bearer {secret(cfg, 'token_env')}"}
    wanted = cfg.get("checks") or DEFAULT_CHECKS
    group = str(cfg.get("group") or "")
    within = int(cfg.get("token_expiry_days", 14))
    available = {
        "reviews": lambda: _reviews(base, headers, cfg),
        "vulnerabilities": lambda: _vulnerabilities(base, headers, group, since),
        "tokens": lambda: _tokens(base, headers, group, within),
    }
    lines: list[str] = []
    if not group and set(wanted) & {"vulnerabilities", "tokens"}:
        lines.append("Group checks skipped: no group is configured.")
        wanted = [c for c in wanted if c not in {"vulnerabilities", "tokens"}]
    lines += run_checks(wanted, available)
    return Section("GitLab", lines)
