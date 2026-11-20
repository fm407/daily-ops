"""okta, intune, aws_securityhub, snyk, and the shared check runner: mocked responses only."""

import datetime as dt
import json
import subprocess

import pytest
import requests
from common import ConfigError
from sources import aws_securityhub, github, gitlab, intune, okta, snyk

SINCE = dt.datetime.now(dt.UTC) - dt.timedelta(days=1)
SNYK_ORG = "11111111-2222-3333-4444-555555555555"


class FakeResponse:
    def __init__(self, payload, status=200, links=None):
        self.status_code, self._payload, self.links = status, payload, links or {}
        self.content, self.text = b"x", str(payload)

    def json(self):
        return self._payload


def routed(monkeypatch, routes):
    """Replace requests.request; routes are (url substring, params substring, response), first match wins."""
    calls = []

    def fake(method, url, **kwargs):
        calls.append({"method": method, "url": url, **kwargs})
        for part, param_part, payload in routes:
            if part in url and param_part in str(kwargs.get("params") or ""):
                return payload if isinstance(payload, FakeResponse) else FakeResponse(payload)
        raise AssertionError(f"unexpected call {url} {kwargs.get('params')}")

    monkeypatch.setenv("DEMO_TOKEN", "s3cr3t-value")
    monkeypatch.setattr(requests, "request", fake)
    return calls


# ---------- okta ----------

OKTA = {"base_url": "https://example.okta.com", "token_env": "DEMO_TOKEN"}


def user(uid, login):
    return {
        "id": uid,
        "status": "ACTIVE",
        "statusChanged": "2026-01-03T10:00:00.000Z",
        "profile": {"login": login},
    }


def test_okta_lists_users_without_mfa_and_flagged_statuses_following_link_headers(monkeypatch):
    next_page = "https://example.okta.com/api/v1/users?after=abc"
    calls = routed(
        monkeypatch,
        [
            ("/users/u1/factors", "", [{"factorType": "push", "status": "ACTIVE"}]),
            ("/users/u2/factors", "", [{"factorType": "sms", "status": "PENDING_ACTIVATION"}]),
            ("/users/u3/factors", "", []),
            ("after=abc", "", [user("u3", "robin@example.com")]),
            (
                "/api/v1/users",
                "ACTIVE",
                FakeResponse(
                    [user("u1", "alex@example.com"), user("u2", "jo@example.com")],
                    links={"next": {"url": next_page}},
                ),
            ),
            ("/api/v1/users", "SUSPENDED", [user("u4", "sam@example.com")]),
            ("/api/v1/users", "LOCKED_OUT", []),
        ],
    )

    text = "\n".join(okta.collect(OKTA, SINCE).lines)

    assert (
        "**Active users with no enrolled MFA factor** (2 of 3 checked)\n- jo@example.com\n- robin@example.com"
        in text
    )
    assert "**Users in status SUSPENDED** (1)\n- sam@example.com (since 2026-01-03)" in text
    assert "**Users in status LOCKED_OUT** (0)" in text
    assert all(c["headers"]["Authorization"] == "SSWS s3cr3t-value" for c in calls)
    assert "s3cr3t-value" not in text


def test_okta_bounds_the_mfa_check_and_says_what_was_left_out(monkeypatch):
    routed(
        monkeypatch,
        [
            ("/factors", "", []),
            ("/api/v1/users", "ACTIVE", [user("u1", "a@example.com"), user("u2", "b@example.com")]),
        ],
    )

    lines = okta.collect({**OKTA, "checks": ["mfa"], "max_users": 1}, SINCE).lines

    assert lines[0] == "**Active users with no enrolled MFA factor** (1 of 1 checked)"
    assert lines[-1] == "1 more active user(s) not checked: raise max_users to cover them."


def test_okta_error_shape_and_foreign_next_link_fail_the_check(monkeypatch):
    routed(monkeypatch, [("/api/v1/users", "", {"errorCode": "E0000011", "errorSummary": "Invalid token"})])
    lines = okta.collect({**OKTA, "checks": ["status"]}, SINCE).lines
    assert len(lines) == 1 and lines[0].startswith(
        "CHECK FAILED: status: RuntimeError: unexpected response shape"
    )

    elsewhere = FakeResponse([], links={"next": {"url": "https://evil.example.net/api/v1/users?after=x"}})
    calls = routed(monkeypatch, [("/api/v1/users", "", elsewhere)])
    lines = okta.collect({**OKTA, "checks": ["status"]}, SINCE).lines
    assert "next-page link points to another host" in lines[0]
    assert all("evil" not in c["url"] for c in calls)


# ---------- intune ----------

INTUNE = {"tenant_id_env": "DEMO_TENANT", "client_id_env": "DEMO_CLIENT", "client_secret_env": "DEMO_TOKEN"}


def test_intune_lists_noncompliant_devices_across_pages(monkeypatch):
    monkeypatch.setenv("DEMO_TENANT", "contoso.example.com")
    monkeypatch.setenv("DEMO_CLIENT", "client-id-value")
    device = {
        "deviceName": "surface-jo",
        "operatingSystem": "Windows",
        "lastSyncDateTime": "2026-01-02T07:00:00Z",
    }
    calls = routed(
        monkeypatch,
        [
            ("/oauth2/v2.0/token", "", {"access_token": "graph-token"}),
            ("skiptoken", "", {"value": [{**device, "deviceName": "ipad-sam", "operatingSystem": "iOS"}]}),
            (
                "/deviceManagement/managedDevices",
                "noncompliant",
                {
                    "value": [device],
                    "@odata.nextLink": "https://graph.microsoft.com/v1.0/deviceManagement/managedDevices?$skiptoken=1",
                },
            ),
        ],
    )

    lines = intune.collect(INTUNE, SINCE).lines

    assert lines[0].startswith("**Devices not compliant** (2")
    assert lines[1:] == [
        "- surface-jo (Windows), last sync 2026-01-02",
        "- ipad-sam (iOS), last sync 2026-01-02",
    ]
    assert calls[0]["url"] == "https://login.microsoftonline.com/contoso.example.com/oauth2/v2.0/token"
    assert calls[0]["data"]["grant_type"] == "client_credentials" and "headers" not in calls[0]
    assert calls[1]["headers"]["Authorization"] == "Bearer graph-token"


@pytest.mark.parametrize(
    "token, devices, message",
    [
        ({"error": "invalid_client"}, {"value": []}, "no access token returned"),
        ({"access_token": "t"}, {"error": {"code": "Forbidden"}}, "unexpected response shape"),
        (
            {"access_token": "t"},
            {"value": [], "@odata.nextLink": "https://evil.example.net/x"},
            "another host",
        ),
    ],
)
def test_intune_error_shapes_raise(monkeypatch, token, devices, message):
    monkeypatch.setenv("DEMO_TENANT", "contoso.example.com")
    monkeypatch.setenv("DEMO_CLIENT", "client-id-value")
    routed(monkeypatch, [("/oauth2/v2.0/token", "", token), ("/managedDevices", "", devices)])

    with pytest.raises(RuntimeError, match=message):
        intune.collect(INTUNE, SINCE)


def test_intune_tenant_id_cannot_change_the_token_url(monkeypatch):
    monkeypatch.setenv("DEMO_TENANT", "contoso/../other")
    monkeypatch.setenv("DEMO_CLIENT", "client-id-value")
    calls = routed(monkeypatch, [])

    with pytest.raises(ConfigError, match="tenant id"):
        intune.collect(INTUNE, SINCE)
    assert calls == []


# ---------- aws security hub ----------


def fake_aws(monkeypatch, stdout="", returncode=0, stderr=""):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"], seen["kwargs"] = cmd, kwargs
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(subprocess, "run", fake_run)
    return seen


def test_securityhub_lists_new_findings_with_a_safe_command_line(monkeypatch):
    finding = {
        "Title": "S3 bucket allows public read",
        "Severity": {"Label": "CRITICAL"},
        "Resources": [{"Id": "arn:aws:s3:::example-bucket"}],
    }
    seen = fake_aws(monkeypatch, json.dumps({"Findings": [finding], "NextToken": "more"}))

    lines = aws_securityhub.collect({"profile": "audit", "region": "eu-west-1"}, SINCE).lines

    assert lines == [
        "**New active critical and high findings** (1+)",
        "- CRITICAL S3 bucket allows public read (arn:aws:s3:::example-bucket)",
    ]
    cmd = seen["cmd"]
    assert cmd[:3] == ["aws", "securityhub", "get-findings"] and "--no-cli-pager" not in cmd
    assert cmd[cmd.index("--profile") + 1] == "audit" and cmd[cmd.index("--region") + 1] == "eu-west-1"
    assert seen["kwargs"]["stdin"] is subprocess.DEVNULL and "shell" not in seen["kwargs"]
    filters = json.loads(cmd[cmd.index("--filters") + 1])
    assert [f["Value"] for f in filters["SeverityLabel"]] == ["CRITICAL", "HIGH"]
    assert (
        filters["RecordState"] == [{"Value": "ACTIVE", "Comparison": "EQUALS"}]
        and "Start" in filters["CreatedAt"][0]
    )


def test_securityhub_error_shapes_raise(monkeypatch):
    fake_aws(monkeypatch, returncode=254, stderr="An error occurred (AccessDeniedException)")
    with pytest.raises(RuntimeError, match="AccessDeniedException"):
        aws_securityhub.collect({}, SINCE)

    fake_aws(monkeypatch, json.dumps({"Message": "not enabled"}))
    with pytest.raises(RuntimeError, match="Findings is missing"):
        aws_securityhub.collect({}, SINCE)

    seen = fake_aws(monkeypatch, "{}")
    with pytest.raises(ConfigError, match="profile"):
        aws_securityhub.collect({"profile": "audit --endpoint-url x"}, SINCE)
    assert seen == {}


# ---------- snyk ----------


def test_snyk_lists_open_issues_and_marks_new_ones(monkeypatch):
    def item(title, level, created):
        return {
            "attributes": {
                "title": title,
                "effective_severity_level": level,
                "type": "package_vulnerability",
                "created_at": created,
            }
        }

    recent = (SINCE + dt.timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    calls = routed(
        monkeypatch,
        [
            (
                "/rest/orgs/",
                "",
                {
                    "data": [
                        item("Prototype pollution", "critical", recent),
                        item("Path traversal", "high", "2025-06-01T00:00:00Z"),
                        {
                            "attributes": {
                                **item("Accepted risk", "high", recent)["attributes"],
                                "ignored": True,
                            }
                        },
                    ],
                    "links": {"next": "/rest/orgs/x/issues?starting_after=1"},
                },
            )
        ],
    )

    lines = snyk.collect({"token_env": "DEMO_TOKEN", "org_id": SNYK_ORG}, SINCE).lines

    assert lines == [
        "**Open critical and high issues, not ignored** (2+)",
        "- NEW critical package_vulnerability: Prototype pollution",
        "- high package_vulnerability: Path traversal",
    ]
    assert calls[0]["url"] == f"https://api.snyk.io/rest/orgs/{SNYK_ORG}/issues"
    assert calls[0]["headers"]["Authorization"] == "token s3cr3t-value"
    assert calls[0]["params"]["effective_severity_level"] == "critical,high" and calls[0]["params"]["version"]


def test_snyk_error_shape_and_bad_org_raise(monkeypatch):
    calls = routed(
        monkeypatch, [("/rest/orgs/", "", {"errors": [{"status": "404", "detail": "Org not found"}]})]
    )

    with pytest.raises(RuntimeError, match="data is missing"):
        snyk.collect({"token_env": "DEMO_TOKEN", "org_id": SNYK_ORG}, SINCE)
    with pytest.raises(ConfigError, match="org_id"):
        snyk.collect({"token_env": "DEMO_TOKEN", "org_id": "../other"}, SINCE)
    assert len(calls) == 1


# ---------- unknown check names ----------


@pytest.mark.parametrize(
    "module, cfg, available",
    [
        (
            gitlab,
            {"token_env": "DEMO_TOKEN", "username": "alex", "group": "", "checks": ["reviews", "tokns"]},
            "reviews",
        ),
        (
            github,
            {"token_env": "DEMO_TOKEN", "username": "alex", "org": "", "checks": ["reviews", "tokns"]},
            "reviews",
        ),
        (okta, {**OKTA, "checks": ["status", "tokns"]}, "mfa, status"),
    ],
)
def test_unknown_check_name_is_reported_not_skipped(monkeypatch, module, cfg, available):
    routed(
        monkeypatch,
        [("/merge_requests", "", []), ("/search/issues", "", {"items": []}), ("/api/v1/users", "", [])],
    )

    lines = module.collect(cfg, SINCE).lines

    assert any(line.startswith("unknown check: tokns (available: ") and available in line for line in lines)
    assert len(lines) > 1  # the known check still ran
