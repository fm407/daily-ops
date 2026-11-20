#!/usr/bin/env python3
"""Build the README screenshot from FAKE data: the real collector runs against fixture.json.

    python3 scripts/demo/make_digest.py        # needs Google Chrome or Chromium; pillow trims the image

Writes docs/assets/digest.png. No network call is made: requests.request is replaced by the
fixture. One source (okta) is enabled without its key to show a SOURCE FAILED section.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))

import requests  # noqa: E402
from collect import build_digest  # noqa: E402

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "google-chrome",
    "chromium",
    "chromium-browser",
]
WIDTH, HEIGHT = 1000, 1600
DEMO_NOW = dt.datetime(2026, 1, 5, 9, 2, tzinfo=dt.UTC)  # the date shown in the demo digest
CONFIG = {
    "lookback_days": 1,
    "monday_lookback_days": 3,
    "sources": {
        "jira": {
            "enabled": True,
            "base_url": "https://example.atlassian.net",
            "email_env": "DEMO_JIRA_EMAIL",
            "token_env": "DEMO_JIRA_TOKEN",
            "queries": [
                {"title": "Ready for review", "jql": 'project = SECOPS AND status = "Ready for Review"'},
                {"title": "Overdue or due within 7 days", "jql": "project = SECOPS AND duedate <= 7d"},
                {"title": "New tickets", "jql": "project = SECOPS AND created >= -{days}d"},
            ],
            "waiting_jql": "project = SECOPS AND statusCategory != Done AND updated >= -{days}d",
        },
        "github": {
            "enabled": True,
            "token_env": "DEMO_GITHUB_TOKEN",
            "username": "alex-example",
            "org": "example-org",
        },
        "jumpcloud": {"enabled": True, "api_key_env": "DEMO_JUMPCLOUD_KEY"},
        "okta": {"enabled": True, "base_url": "https://example.okta.com", "token_env": "OKTA_API_TOKEN"},
    },
}
CSS = """
body { font: 16px/1.5 -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; color: #1f2328;
       background: #fff; margin: 0; padding: 32px 48px; width: 904px; box-sizing: border-box; }
h1 { font-size: 1.7em; border-bottom: 1px solid #d1d9e0; padding-bottom: .3em; margin: 0 0 16px; }
h2 { font-size: 1.3em; border-bottom: 1px solid #d1d9e0; padding-bottom: .3em; margin: 24px 0 12px; }
p { margin: 0 0 10px; } ul { margin: 0 0 10px; padding-left: 2em; } li { margin: 3px 0; }
.failed { color: #cf222e; font-family: ui-monospace, Menlo, monospace; font-size: 14px; }
"""


class FakeResponse:
    status_code, content = 200, b"x"

    def __init__(self, payload: object) -> None:
        self._payload = payload
        self.text = ""

    def json(self) -> object:
        return self._payload


def run_collector() -> tuple[str, list[str]]:
    """The digest and the collector's progress lines, from the fixture only."""
    seen_now = dt.datetime.now(dt.UTC).isoformat()  # devices marked __NOW__ are never stale
    routes = json.loads((HERE / "fixture.json").read_text().replace("__NOW__", seen_now))

    def fake_request(method: str, url: str, **kwargs: object) -> FakeResponse:
        params = kwargs.get("params") or {}
        severity = f"severity={params['severity']}" if "severity" in params else ""
        target = " ".join(filter(None, [url, params.get("jql", ""), severity]))
        for route in routes:
            if route["match"] in target:
                return FakeResponse(route["response"])
        raise RuntimeError(f"no fixture for {url}")

    for name in ("DEMO_JIRA_EMAIL", "DEMO_JIRA_TOKEN", "DEMO_GITHUB_TOKEN", "DEMO_JUMPCLOUD_KEY"):
        os.environ[name] = "demo-value-not-a-secret"
    os.environ.pop("OKTA_API_TOKEN", None)  # left unset on purpose
    real, requests.request = requests.request, fake_request
    progress: list[str] = []
    try:
        return build_digest(CONFIG, DEMO_NOW, progress.append), progress
    finally:
        requests.request = real


def to_html(markdown: str) -> str:
    """Just enough markdown for a digest: headings, bullets, bold, paragraphs."""
    out, in_list = [], False
    for line in markdown.splitlines():
        text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html.escape(line))
        if in_list and not line.startswith("- "):
            out.append("</ul>")
            in_list = False
        if line.startswith("## "):
            out.append(f"<h2>{text[3:]}</h2>")
        elif line.startswith("# "):
            out.append(f"<h1>{text[2:]}</h1>")
        elif line.startswith("- "):
            out.append(("" if in_list else "<ul>") + f"<li>{text[2:]}</li>")
            in_list = True
        elif line.startswith("SOURCE FAILED"):
            out.append(f'<p class="failed">{text}</p>')
        elif line:
            out.append(f"<p>{text}</p>")
    return (
        f"<!doctype html><meta charset=utf-8><style>{CSS}</style>"
        + "\n".join(out)
        + ("</ul>" if in_list else "")
    )


def main() -> None:
    digest, _ = run_collector()
    out_dir = HERE / "out"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "digest.md").write_text(digest)
    page = out_dir / "digest.html"
    page.write_text(to_html(digest))
    chrome = next((c for c in CHROME_CANDIDATES if shutil.which(c) or pathlib.Path(c).exists()), None)
    if not chrome:
        sys.exit(f"no Chrome or Chromium found; open {page} and capture it by hand")
    png = ROOT / "docs/assets/digest.png"
    png.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            chrome,
            "--headless",
            "--disable-gpu",
            "--hide-scrollbars",
            f"--window-size={WIDTH},{HEIGHT}",
            f"--screenshot={png}",
            page.as_uri(),
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )
    try:
        from PIL import Image, ImageChops
    except ImportError:
        print(f"{png} (untrimmed: install pillow to crop the empty space)")
        return
    image = Image.open(png).convert("RGB")
    box = ImageChops.difference(image, Image.new("RGB", image.size, "#ffffff")).getbbox()
    image = image.crop((0, 0, image.width, min(image.height, box[3] + 32))) if box else image
    image.quantize(colors=64).save(png, optimize=True)
    print(png)


if __name__ == "__main__":
    main()
