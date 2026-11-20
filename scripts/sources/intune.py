"""Microsoft Intune: managed devices that are not compliant, through Microsoft Graph (app-only token)."""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from common import ConfigError, Section, clean, request, same_host, secret

LOGIN = "https://login.microsoftonline.com"
GRAPH = "https://graph.microsoft.com"
MAX_PAGES = 20
FIELDS = "deviceName,operatingSystem,complianceState,lastSyncDateTime"


def _token(cfg: dict[str, Any]) -> str:
    tenant = secret(cfg, "tenant_id_env")
    if not re.fullmatch(r"[A-Za-z0-9.-]+", tenant):  # it goes into the token URL
        raise ConfigError("the tenant id must be a GUID or a domain name")
    form = {
        "grant_type": "client_credentials",
        "client_id": secret(cfg, "client_id_env"),
        "client_secret": secret(cfg, "client_secret_env"),
        "scope": f"{GRAPH}/.default",
    }
    token = (request("POST", f"{LOGIN}/{tenant}/oauth2/v2.0/token", data=form) or {}).get("access_token")
    if not token:
        raise RuntimeError("no access token returned; check the app registration and its client secret")
    return str(token)


def collect(cfg: dict[str, Any], since: dt.datetime) -> Section:
    headers = {"Authorization": f"Bearer {_token(cfg)}", "Accept": "application/json"}
    url: str | None = f"{GRAPH}/v1.0/deviceManagement/managedDevices"
    params: dict[str, Any] | None = {"$filter": "complianceState eq 'noncompliant'", "$select": FIELDS}
    devices: list[dict] = []
    for _ in range(MAX_PAGES):
        page = request("GET", url, headers=headers, params=params)
        values = page.get("value") if isinstance(page, dict) else None
        if not isinstance(values, list):
            raise RuntimeError("unexpected response shape: value is missing")  # never report a false 0
        devices += [d for d in values if isinstance(d, dict)]
        url, params = page.get("@odata.nextLink"), None
        if not url:
            break
        if not same_host(str(url), GRAPH):
            raise RuntimeError("the next-page link points to another host; stopping")
    lines = [f"**Devices not compliant** ({len(devices)}, needs DeviceManagementManagedDevices.Read.All)"]
    lines += [
        f"- {clean(d.get('deviceName'), 80)} ({clean(d.get('operatingSystem'), 30)}), "
        f"last sync {clean(d.get('lastSyncDateTime'), 10) or '?'}"
        for d in devices
    ]
    return Section("Microsoft Intune", lines)
