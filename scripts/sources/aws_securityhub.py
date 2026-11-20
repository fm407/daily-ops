"""AWS Security Hub: active critical and high findings created in the window, through the aws CLI."""

from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
from typing import Any

from common import ConfigError, Section, clean, zulu

AWS_TIMEOUT = 180  # seconds
MAX_ITEMS = 100  # first page only; more is shown as "100+"


def collect(cfg: dict[str, Any], since: dt.datetime) -> Section:
    filters = {
        "RecordState": [{"Value": "ACTIVE", "Comparison": "EQUALS"}],
        "WorkflowStatus": [{"Value": v, "Comparison": "EQUALS"} for v in ("NEW", "NOTIFIED")],
        "SeverityLabel": [{"Value": v, "Comparison": "EQUALS"} for v in ("CRITICAL", "HIGH")],
        "CreatedAt": [{"Start": zulu(since), "End": zulu(dt.datetime.now(dt.UTC))}],
    }
    cmd = ["aws", "securityhub", "get-findings", "--filters", json.dumps(filters)]
    cmd += ["--max-items", str(MAX_ITEMS), "--output", "json"]
    for option in ("profile", "region"):
        value = str(cfg.get(option) or "")
        if value and not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
            raise ConfigError(f"aws_securityhub.{option} has characters that are not allowed")
        if value:
            cmd += [f"--{option}", value]
    r = subprocess.run(  # argument list, no shell, no prompt or pager can block it
        cmd, capture_output=True, text=True, timeout=AWS_TIMEOUT, stdin=subprocess.DEVNULL
    )
    if r.returncode:
        raise RuntimeError(clean(r.stderr, 300) or f"aws exited with {r.returncode}")
    result = json.loads(r.stdout or "{}")
    findings = result.get("Findings") if isinstance(result, dict) else None
    if not isinstance(findings, list):
        raise RuntimeError("unexpected response shape: Findings is missing")  # never report a false 0
    more = "+" if result.get("NextToken") else ""
    lines = [f"**New active critical and high findings** ({len(findings)}{more})"]
    for f in findings:
        resources = f.get("Resources") or [{}]
        lines.append(
            f"- {clean((f.get('Severity') or {}).get('Label'), 10)} {clean(f.get('Title'))} "
            f"({clean((resources[0] or {}).get('Id'))[-60:]})"
        )
    return Section("AWS Security Hub", lines)
