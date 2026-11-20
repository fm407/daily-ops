#!/usr/bin/env python3
"""Jira Cloud REST helper, using the sources.jira settings from the config. Never prints a token.

    python3 jira_api.py GET '/rest/api/3/issue/SECOPS-1?fields=summary,status'
    python3 jira_api.py GET /rest/api/3/issue/SECOPS-1/transitions
    python3 jira_api.py POST /rest/api/3/issue/SECOPS-1/transitions '{"transition": {"id": "<id>"}}'
    python3 jira_api.py PUT /rest/api/3/issue/SECOPS-1 '{"fields": {"duedate": "2026-01-31"}}'
    python3 jira_api.py comment SECOPS-1 <output_dir>/<date>/draft.txt

"comment" posts a draft file from the output directory as plain paragraphs (blank line between
paragraphs, single newlines kept as line breaks). A first line starting with "Jira comment on"
is the draft's header and is left out.

GET is read-only. Writes change Jira: run one only for an action the user approved. Writes are
limited on purpose: POST only to an issue's /transitions or /comment, PUT only to an issue's
fields, and only in security_board.project or a project listed in sources.jira.writable_projects.
Every other method or path is refused.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
from typing import Any

from common import ConfigError, load_config, output_dir, redact
from sources.jira import ISSUE_KEY, adf, api

DRAFT_HEADER = "jira comment on"
MAX_OUTPUT = 6000
READ_PREFIX = "/rest/api/3/"
WRITE_PATHS = {
    "POST": rf"/rest/api/3/issue/({ISSUE_KEY})/(?:transitions|comment)",
    "PUT": rf"/rest/api/3/issue/({ISSUE_KEY})",
}


def writable_projects(cfg: dict[str, Any]) -> set[str]:
    jira = cfg["sources"].get("jira") or {}
    listed = [cfg["security_board"].get("project"), *(jira.get("writable_projects") or [])]
    return {str(p) for p in listed if p}


def check_key(cfg: dict[str, Any], key: str) -> None:
    if not re.fullmatch(ISSUE_KEY, key):
        raise ValueError(f"not an issue key: {key[:40]!r}")
    allowed = writable_projects(cfg)
    if key.rsplit("-", 1)[0] not in allowed:
        raise ValueError(
            f"writes to {key} are refused: its project is not security_board.project or in "
            f"sources.jira.writable_projects (allowed: {sorted(allowed) or 'none'})"
        )


def check_call(cfg: dict[str, Any], method: str, path: str) -> None:
    """Refuse anything but a read under /rest/api/3/ or one of the few writes the skills need."""
    if method == "GET":
        resource = path.split("?", 1)[0]  # percent-encoding in the resource could hide a "../"
        if not path.startswith(READ_PREFIX) or ".." in resource or "%" in resource:
            raise ValueError(f"GET is limited to plain paths under {READ_PREFIX}")
        return
    if method not in WRITE_PATHS:
        raise ValueError(f"method {method[:10]!r} is refused: only GET, POST and PUT are allowed")
    match = re.fullmatch(WRITE_PATHS[method], path)
    if not match:
        raise ValueError(f"{method} is limited to {WRITE_PATHS[method]}")
    check_key(cfg, match.group(1))


def comment_body(text: str) -> dict:
    lines = text.strip().splitlines()
    if lines and lines[0].lower().startswith(DRAFT_HEADER):
        lines = lines[1:]
    paragraphs = re.split(r"\n\s*\n", "\n".join(line.strip() for line in lines).strip())
    return {"body": adf(*[p for p in paragraphs if p])}


def draft_file(cfg: dict[str, Any], name: str) -> pathlib.Path:
    """A comment is only ever posted from a draft inside the output directory."""
    path, root = pathlib.Path(name).expanduser().resolve(), output_dir(cfg).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"the comment file must be inside the output directory {root}")
    return path


def run(cfg: dict[str, Any], argv: list[str]) -> Any:
    jira = cfg["sources"].get("jira") or {}
    if argv[0] == "comment":
        if len(argv) != 3:
            raise ValueError("usage: comment KEY FILE")
        check_key(cfg, argv[1])
        body = comment_body(draft_file(cfg, argv[2]).read_text())
        return api(jira, "POST", f"/rest/api/3/issue/{argv[1]}/comment", body)
    method = argv[0].upper()
    check_call(cfg, method, argv[1])
    return api(jira, method, argv[1], json.loads(argv[2]) if len(argv) > 2 else None)


def main(argv: list[str]) -> None:
    if len(argv) < 2:
        sys.exit(__doc__)
    try:
        cfg = load_config()
    except ConfigError as e:
        sys.exit(f"FAILED: {e}")
    try:
        text = json.dumps(run(cfg, argv), indent=1, ensure_ascii=False)
    except (ConfigError, RuntimeError, ValueError, OSError) as e:
        sys.exit(redact(f"FAILED: {e}", cfg))
    if len(text) > MAX_OUTPUT:
        text = text[:MAX_OUTPUT] + f"\n... [truncated, {len(text) - MAX_OUTPUT} more characters]"
    print(redact(text, cfg))


if __name__ == "__main__":
    main(sys.argv[1:])
