"""Okta: active users with no enrolled MFA factor, and suspended or locked-out users."""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from common import Section, clean, get_list_pages, run_checks, secret

DEFAULT_CHECKS = ["mfa", "status"]
PAGE_SIZE = 200
MAX_PAGES = 50
DEFAULT_MAX_USERS = 200  # the MFA check costs one request per user, so it is bounded
FLAGGED_STATUSES = ("SUSPENDED", "LOCKED_OUT")


def _login(user: dict) -> str:
    return clean((user.get("profile") or {}).get("login") or user.get("id"), 80)


def _users(base: str, headers: dict[str, str], status: str) -> list[dict]:
    params = {"filter": f'status eq "{status}"', "limit": PAGE_SIZE}
    return [
        u for u in get_list_pages(f"{base}/api/v1/users", headers, params, MAX_PAGES) if isinstance(u, dict)
    ]


def _mfa(base: str, headers: dict[str, str], max_users: int) -> list[str]:
    users = _users(base, headers, "ACTIVE")
    checked, without = users[:max_users], []
    for user in checked:
        user_id = str(user.get("id") or "")
        if not re.fullmatch(r"[A-Za-z0-9]+", user_id):  # it goes into a URL path
            raise RuntimeError("unexpected response shape: a user has no usable id")
        factors = get_list_pages(f"{base}/api/v1/users/{user_id}/factors", headers, {}, 1)
        if not any(isinstance(f, dict) and f.get("status") == "ACTIVE" for f in factors):
            without.append(user)
    lines = [f"**Active users with no enrolled MFA factor** ({len(without)} of {len(checked)} checked)"]
    lines += [f"- {_login(u)}" for u in without]
    if len(users) > len(checked):
        lines.append(
            f"{len(users) - len(checked)} more active user(s) not checked: raise max_users to cover them."
        )
    return lines


def _status(base: str, headers: dict[str, str]) -> list[str]:
    lines: list[str] = []
    for status in FLAGGED_STATUSES:
        users = _users(base, headers, status)
        lines.append(f"**Users in status {status}** ({len(users)})")
        lines += [f"- {_login(u)} (since {clean(u.get('statusChanged'), 10) or '?'})" for u in users]
    return lines


def collect(cfg: dict[str, Any], since: dt.datetime) -> Section:
    base = str(cfg.get("base_url") or "").rstrip("/")
    headers = {"Authorization": f"SSWS {secret(cfg, 'token_env')}", "Accept": "application/json"}
    max_users = int(cfg.get("max_users", DEFAULT_MAX_USERS))
    available = {
        "mfa": lambda: _mfa(base, headers, max_users),
        "status": lambda: _status(base, headers),
    }
    return Section("Okta", run_checks(cfg.get("checks") or DEFAULT_CHECKS, available))
