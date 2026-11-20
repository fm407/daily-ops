"""Jira Cloud REST v3: configured JQL queries, plus tickets whose last comment is someone else's."""

from __future__ import annotations

import base64
import datetime as dt
import re
from typing import Any

from common import Section, clean, request, secret

PAGE_SIZE = 100
MAX_PAGES = 50  # 5000 issues; a query that returns more needs narrowing, not more paging
DEFAULT_FIELDS = "summary,status,assignee,duedate"
ISSUE_KEY = r"[A-Z][A-Z0-9_]*-\d+"


def _headers(cfg: dict[str, Any]) -> dict[str, str]:
    token = secret(cfg, "token_env")
    if cfg.get("auth", "basic") == "bearer":
        return {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    raw = f"{secret(cfg, 'email_env')}:{token}".encode()
    return {"Authorization": "Basic " + base64.b64encode(raw).decode(), "Accept": "application/json"}


def api(cfg: dict[str, Any], method: str, path: str, body: Any = None, params: dict | None = None) -> Any:
    """Call the configured Jira site. The path must be absolute so it cannot redirect the host."""
    if not path.startswith("/"):
        raise ValueError("Jira path must start with /")
    url = str(cfg.get("base_url", "")).rstrip("/") + path
    return request(method, url, headers=_headers(cfg), json=body, params=params)


def search(cfg: dict[str, Any], jql: str, fields: str = DEFAULT_FIELDS) -> list[dict]:
    issues: list[dict] = []
    token = None
    for _ in range(MAX_PAGES):
        params = {"jql": jql, "fields": fields, "maxResults": PAGE_SIZE}
        if token:
            params["nextPageToken"] = token
        data = api(cfg, "GET", "/rest/api/3/search/jql", params=params) or {}
        issues += data.get("issues") or []
        token = data.get("nextPageToken")
        if not token:
            return issues
    raise RuntimeError(f"more than {MAX_PAGES * PAGE_SIZE} issues match; narrow the JQL")


def _inline(text: str) -> list[dict]:
    """Text nodes with a hardBreak for every single newline."""
    nodes: list[dict] = []
    for i, line in enumerate(text.split("\n")):
        if i:
            nodes.append({"type": "hardBreak"})
        if line:
            nodes.append({"type": "text", "text": line})
    return nodes


def adf(*paragraphs: str) -> dict:
    """Plain paragraphs as an Atlassian Document, the format comments and descriptions take."""
    return {
        "type": "doc",
        "version": 1,
        "content": [{"type": "paragraph", "content": _inline(p)} for p in paragraphs],
    }


def adf_text(node: Any) -> str:
    if not isinstance(node, dict):
        return ""
    return str(node.get("text", "")) + "".join(adf_text(c) for c in node.get("content") or [])


def last_comment(cfg: dict[str, Any], issue: dict) -> dict | None:
    """The newest comment. A search embeds only the first page of comments, so when the issue has
    more than came back the true latest is fetched on its own."""
    block = (issue.get("fields") or {}).get("comment") or {}
    comments = block.get("comments") or []
    key = str(issue.get("key") or "")
    total = block.get("total")
    if isinstance(total, int) and total > len(comments) and re.fullmatch(ISSUE_KEY, key):
        params = {"orderBy": "-created", "maxResults": 1}
        newest = (api(cfg, "GET", f"/rest/api/3/issue/{key}/comment", params=params) or {}).get("comments")
        return newest[0] if newest else None
    return comments[-1] if comments else None


def issue_line(issue: dict, due_field: str = "duedate") -> str:
    f = issue.get("fields") or {}
    who = clean((f.get("assignee") or {}).get("displayName") or "unassigned", 60)
    due = clean(f.get(due_field) or f.get("duedate") or "", 10)
    status = clean((f.get("status") or {}).get("name"), 40)
    return (
        f"- {clean(issue.get('key'), 30)} [{status}] {clean(f.get('summary'))} "
        f"({who}{', due ' + due if due else ''})"
    )


def collect(cfg: dict[str, Any], since: dt.datetime) -> Section:
    days = max(1, round((dt.datetime.now(dt.UTC) - since).total_seconds() / 86400))
    due_field = cfg.get("due_date_field") or "duedate"
    lines: list[str] = []
    for q in cfg.get("queries") or []:
        issues = search(cfg, str(q["jql"]).replace("{days}", str(days)), f"{DEFAULT_FIELDS},{due_field}")
        lines += [f"**{q['title']}** ({len(issues)})", *(issue_line(i, due_field) for i in issues), ""]

    waiting_jql = cfg.get("waiting_jql")
    if waiting_jql:
        me = (api(cfg, "GET", "/rest/api/3/myself") or {}).get("accountId")
        waiting = []
        for i in search(
            cfg, str(waiting_jql).replace("{days}", str(days)), "summary,status,assignee,comment"
        ):
            last = last_comment(cfg, i)
            if last and (last.get("author") or {}).get("accountId") != me:
                author = clean((last.get("author") or {}).get("displayName"), 60)
                waiting.append(
                    f"{issue_line(i)}: last comment by {author} {clean(last.get('created'), 10)}: "
                    f'"{clean(adf_text(last.get("body")), 300)}"'
                )
        lines += [f"**Tickets where the last comment is someone else's** ({len(waiting)})", *waiting]
    return Section("Jira", lines)
