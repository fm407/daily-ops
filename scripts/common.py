"""Shared helpers: config loading, secrets from the environment, HTTP with timeouts, output cleaning."""

from __future__ import annotations

import datetime as dt
import os
import pathlib
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import requests
import yaml

CONFIG_ENV = "DAILY_OPS_CONFIG"
DEFAULT_CONFIG = "daily-ops.yaml"
TIMEOUT = 30  # seconds, applied to every HTTP request
MIN_SECRET_LENGTH = 6  # shorter values are not redacted, they would mangle ordinary text
MIN_PYTHON = (3, 11)

DEFAULTS: dict[str, Any] = {
    "python": "python3",
    "output_dir": "daily-ops-out",
    "lookback_days": 1,
    "monday_lookback_days": 3,
    "sla_days": {"Critical": 7, "High": 30, "Medium": 90, "Low": 180},
    "sla_ignore": [],
    "style": {"avoid_em_dashes": False, "tone": "", "extra_rules": []},
    "drafts": {"open_command": ""},
    "connectors": {"email": False, "calendar": False, "chat": False, "require": True},
    "security_board": {},
    "sources": {},
}


class ConfigError(Exception):
    """The config file or an environment variable it names is missing or invalid."""


@dataclass
class Section:
    """One block of the digest."""

    title: str
    lines: list[str] = field(default_factory=list)


def load_dotenv(path: pathlib.Path) -> None:
    """Read KEY=VALUE lines into the environment without overriding what is already set.

    Accepts an `export ` prefix, quoted values, and a ` # comment` after an unquoted value.
    """
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        name, sep, value = line.strip().removeprefix("export ").partition("=")
        name, value = name.strip(), value.strip()
        if not sep or not name or name.startswith("#"):
            continue
        if value[:1] in ("'", '"') and value.endswith(value[0]) and len(value) > 1:
            value = value[1:-1]
        else:
            value = value.split(" #")[0].strip()
        os.environ.setdefault(name, value)


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def load_config(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Load the YAML config over the defaults. Path: argument, $DAILY_OPS_CONFIG, or ./daily-ops.yaml."""
    if sys.version_info < MIN_PYTHON:
        found = ".".join(map(str, sys.version_info[:3]))
        raise ConfigError(f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer is needed, this is {found}")
    p = pathlib.Path(path or os.environ.get(CONFIG_ENV) or DEFAULT_CONFIG).expanduser()
    if not p.exists():
        raise ConfigError(f"config not found at {p}: copy config.example.yaml there or set ${CONFIG_ENV}")
    user = yaml.safe_load(p.read_text()) or {}
    if not isinstance(user, dict):
        raise ConfigError(f"{p}: the top level must be a mapping")
    cfg: dict[str, Any] = {}
    for name, default in DEFAULTS.items():
        value = user.get(name, default)
        if isinstance(default, dict):
            if not isinstance(value, dict):
                raise ConfigError(f"{p}: {name} must be a mapping")
            value = {**default, **value}
        cfg[name] = value
    if not all(_positive_int(d) for d in cfg["sla_days"].values()):
        raise ConfigError(f"{p}: sla_days values must be positive whole numbers of days")
    ignore = cfg["sla_ignore"]
    if not isinstance(ignore, list) or not all(isinstance(name, str) for name in ignore):
        raise ConfigError(f"{p}: sla_ignore must be a list of severity names")
    for name in ("lookback_days", "monday_lookback_days"):
        if not _positive_int(cfg[name]):
            raise ConfigError(f"{p}: {name} must be a positive whole number of days")
    for name, source in cfg["sources"].items():
        if not isinstance(source, dict):
            raise ConfigError(f"{p}: sources.{name} must be a mapping")
    config_dir = p.resolve().parent
    workbook = cfg["sources"].get("workbook")
    if workbook and workbook.get("path"):  # a relative workbook path is taken from the config's directory
        workbook["path"] = str(config_dir / pathlib.Path(str(workbook["path"])).expanduser())
    cfg["config_dir"] = str(p.resolve().parent)
    load_dotenv(p.resolve().parent / ".env")
    return cfg


def output_dir(cfg: dict[str, Any]) -> pathlib.Path:
    """The output directory; a relative path is taken from the config file's directory."""
    return pathlib.Path(cfg["config_dir"]) / pathlib.Path(cfg["output_dir"]).expanduser()


def day_dir(cfg: dict[str, Any], day: dt.date | None = None) -> pathlib.Path:
    return output_dir(cfg) / str(day or dt.date.today())


def secret(cfg: dict[str, Any], key: str) -> str:
    """The value of the environment variable that cfg[key] names."""
    name = cfg.get(key)
    if not name:
        raise ConfigError(f"{key} is not set in the config")
    value = os.environ.get(str(name))
    if not value:
        raise ConfigError(f"environment variable {name} is not set")
    return value


def _env_names(node: Any) -> list[str]:
    if isinstance(node, dict):
        # an email address is an identifier, not a secret, and blanking it would hide who wrote what
        own = [str(v) for k, v in node.items() if str(k).endswith("_env") and k != "email_env" and v]
        return own + [n for v in node.values() for n in _env_names(v)]
    return []


def redact(text: str, cfg: dict[str, Any]) -> str:
    """Blank out the value of every secret environment variable the config names."""
    for name in _env_names(cfg):
        value = os.environ.get(name, "")
        if len(value) >= MIN_SECRET_LENGTH:
            text = text.replace(value, "***")
    return text


def clean(value: Any, limit: int = 200) -> str:
    """API text is untrusted: one line, bounded length, so it cannot forge digest structure."""
    return re.sub(r"\s+", " ", str(value if value is not None else "")).strip()[:limit]


def parse_time(value: Any) -> dt.datetime | None:
    """An ISO 8601 timestamp as an aware datetime, or None when absent or malformed."""
    if not isinstance(value, str) or not value:
        return None
    try:
        t = dt.datetime.fromisoformat(re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", value.replace("Z", "+00:00")))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.UTC)


def zulu(t: dt.datetime) -> str:
    return t.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _send(method: str, url: str, **kwargs: Any) -> requests.Response:
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise ConfigError(f"refusing a non-https URL for {parts.netloc or url}")
    r = requests.request(method, url, timeout=TIMEOUT, allow_redirects=False, **kwargs)
    where = f"{method} {parts.netloc}{parts.path}"
    if 300 <= r.status_code < 400:  # never follow: a redirect could carry the credentials to another host
        raise RuntimeError(f"{where} was redirected ({r.status_code}); check the configured base_url")
    if r.status_code >= 400:
        raise RuntimeError(f"{where} returned {r.status_code}: {clean(r.text, 300)}")
    return r


def request(method: str, url: str, **kwargs: Any) -> Any:
    """One HTTPS request with a timeout; returns parsed JSON. Errors never include headers or bodies sent."""
    r = _send(method, url, **kwargs)
    return r.json() if r.content else None


def same_host(url: str, base: str) -> bool:
    """A next-page link is only followed on the host the credentials were meant for."""
    return urlsplit(url).scheme == "https" and urlsplit(url).netloc == urlsplit(base).netloc


def get_list_pages(url: str, headers: dict[str, str], params: dict[str, Any], max_pages: int) -> list[Any]:
    """GET a JSON list, following RFC 8288 `Link: rel="next"` headers on the same host, up to max_pages."""
    items: list[Any] = []
    next_url: str | None = url
    for _ in range(max_pages):
        r = _send("GET", next_url, headers=headers, params=params if next_url == url else None)
        page = r.json() if r.content else None
        if not isinstance(page, list):
            raise RuntimeError(
                f"unexpected response shape from {urlsplit(url).netloc}{urlsplit(url).path}: not a list"
            )
        items += page
        next_url = (getattr(r, "links", None) or {}).get("next", {}).get("url")
        if not next_url:
            return items
        if not same_host(next_url, url):
            raise RuntimeError("the next-page link points to another host; stopping")
    return items


def run_checks(wanted: list[str], available: dict[str, Callable[[], list[str]]]) -> list[str]:
    """Run each wanted check; one that fails becomes a CHECK FAILED line and the others still run.
    A name that is not a check of this source is reported, never silently skipped."""
    lines: list[str] = []
    for name in wanted:
        if name not in available:
            lines.append(f"unknown check: {clean(name, 40)} (available: {', '.join(available)})")
            continue
        try:
            lines += available[name]()
        except Exception as e:  # reported, never hidden; the digest is redacted as a whole
            lines.append(f"CHECK FAILED: {name}: {type(e).__name__}: {clean(e, 300)}")
    return lines


def capped(items: list, page_size: int) -> str:
    """A count that admits when it is only the first page."""
    return f"{len(items)}+" if len(items) >= page_size else str(len(items))
