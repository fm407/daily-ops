import datetime as dt
import json
import subprocess

import collect
import requests
import sec_collect
from sources import dnsfilter, gcp_scc, github, gitlab, jira, jumpcloud, knowbe4

SINCE = dt.datetime.now(dt.UTC) - dt.timedelta(days=1)
JIRA = {
    "enabled": True,
    "base_url": "https://example.atlassian.net",
    "email_env": "DEMO_EMAIL",
    "token_env": "DEMO_TOKEN",
    "queries": [{"title": "New tickets", "jql": "project = SECOPS AND created >= -{days}d"}],
    "waiting_jql": "project = SECOPS AND statusCategory != Done",
}


class FakeResponse:
    def __init__(self, payload, status=200):
        self.status_code, self._payload = status, payload
        self.content, self.text = b"x", str(payload)

    def json(self):
        return self._payload


def issue(key, summary, author_id=None, **fields):
    comment = {
        "id": "7",
        "created": "2026-01-04T10:00:00.000+0000",
        "author": {"accountId": author_id, "displayName": "Alex Example"},
        "body": {"type": "doc", "content": [{"text": "Any update?"}]},
    }
    return {
        "key": key,
        "fields": {
            "summary": summary,
            "status": {"name": "In Review"},
            "assignee": None,
            "comment": {"comments": [comment] if author_id else []},
            **fields,
        },
    }


def fake_jira(calls):
    def fake(method, url, **kwargs):
        calls.append({"method": method, "url": url, **kwargs})
        if url.endswith("/myself"):
            return FakeResponse({"accountId": "me"})
        jql = kwargs["params"]["jql"]
        if "created >=" in jql:
            return FakeResponse({"issues": [issue("SECOPS-1", "Rotate the\n## deploy key")]})
        return FakeResponse(
            {
                "issues": [
                    issue(
                        "SECOPS-2",
                        "Patch the proxy",
                        "someone-else",
                        created="2026-01-01T09:00:00.000+0000",
                        updated="2026-01-04T10:00:00.000+0000",
                        priority={"name": "High"},
                    ),
                    issue("SECOPS-3", "Mine", "me"),
                ]
            }
        )

    return fake


def test_jira_collect_lists_queries_and_tickets_waiting_on_you(monkeypatch):
    calls = []
    monkeypatch.setenv("DEMO_EMAIL", "alex@example.com")
    monkeypatch.setenv("DEMO_TOKEN", "s3cr3t-value")
    monkeypatch.setattr(requests, "request", fake_jira(calls))

    section = jira.collect(JIRA, SINCE)
    text = "\n".join(section.lines)

    assert "**New tickets** (1)" in text
    assert (
        "- SECOPS-1 [In Review] Rotate the ## deploy key (unassigned)" in text
    )  # newline cannot forge a heading
    assert "SECOPS-2" in text and 'last comment by Alex Example 2026-01-04: "Any update?"' in text
    assert "SECOPS-3" not in text  # the last comment there is mine
    assert calls[0]["params"]["jql"] == "project = SECOPS AND created >= -1d"
    assert all(c["timeout"] and c["headers"]["Authorization"].startswith("Basic ") for c in calls)
    assert "s3cr3t-value" not in text


def test_failing_source_is_reported_and_the_run_continues(monkeypatch):
    monkeypatch.setenv("DEMO_EMAIL", "alex@example.com")
    monkeypatch.setenv("DEMO_TOKEN", "s3cr3t-value")
    monkeypatch.delenv("DEMO_MISSING", raising=False)
    monkeypatch.setattr(requests, "request", lambda *a, **k: FakeResponse("denied s3cr3t-value", 401))
    cfg = {
        "lookback_days": 1,
        "monday_lookback_days": 3,
        "sources": {
            "jira": JIRA,
            "jumpcloud": {"enabled": True, "api_key_env": "DEMO_MISSING"},
            "github": {"enabled": False},
        },
    }

    digest = collect.build_digest(cfg, dt.datetime.now(dt.UTC))

    assert (
        "## jira\nSOURCE FAILED: RuntimeError: GET example.atlassian.net/rest/api/3/search/jql returned 401"
        in digest
    )
    assert "## jumpcloud\nSOURCE FAILED: ConfigError: environment variable DEMO_MISSING is not set" in digest
    assert "github" not in digest
    assert "s3cr3t-value" not in digest


def test_plain_http_is_refused(monkeypatch):
    monkeypatch.setenv("DEMO_EMAIL", "alex@example.com")
    monkeypatch.setenv("DEMO_TOKEN", "s3cr3t-value")
    monkeypatch.setattr(requests, "request", lambda *a, **k: (_ for _ in ()).throw(AssertionError("sent")))

    try:
        jira.api({**JIRA, "base_url": "http://example.atlassian.net"}, "GET", "/rest/api/3/myself")
    except Exception as e:
        assert "non-https" in str(e)
    else:
        raise AssertionError("expected a refusal")


def test_monday_uses_the_weekend_lookback():
    cfg = {"lookback_days": 1, "monday_lookback_days": 3}

    assert collect.lookback_days(cfg, dt.date(2026, 1, 5)) == 3  # a Monday
    assert collect.lookback_days(cfg, dt.date(2026, 1, 6)) == 1


def test_security_board_flags_review_and_gives_the_sla_date(monkeypatch):
    monkeypatch.setenv("DEMO_EMAIL", "alex@example.com")
    monkeypatch.setenv("DEMO_TOKEN", "s3cr3t-value")
    monkeypatch.setattr(requests, "request", fake_jira([]))
    cfg = {"sources": {"jira": JIRA}, "sla_days": {"High": 30}, "security_board": {"project": "SECOPS"}}

    board = sec_collect.build_board(cfg, dt.datetime(2026, 1, 5, 12, tzinfo=dt.UTC))

    assert "### SECOPS-2 [In Review] Patch the proxy" in board
    assert "severity High" in board and "SLA date for that severity 2026-01-31" in board
    assert "- fingerprint: In Review|7|-" in board
    assert (
        "in review: verify; unassigned; last comment from Alex Example (2026-01-04): may need a reply"
        in board
    )
    assert "Done without a verified date" not in board  # no verified_on_field configured


# ---------- review fixes ----------


def routed(routes, calls=None):
    """A fake requests.request answering by URL substring (first match wins)."""

    def fake(method, url, **kwargs):
        if calls is not None:
            calls.append({"method": method, "url": url, **kwargs})
        for part, payload in routes:
            if part in url:
                return payload if isinstance(payload, FakeResponse) else FakeResponse(payload)
        raise AssertionError(f"unexpected call {url}")

    return fake


def test_latest_comment_is_fetched_when_the_search_embedded_only_some(monkeypatch):
    calls = []
    monkeypatch.setenv("DEMO_EMAIL", "alex@example.com")
    monkeypatch.setenv("DEMO_TOKEN", "s3cr3t-value")
    truncated = issue("SECOPS-7", "Busy ticket", "me")  # the embedded page ends with my comment
    truncated["fields"]["comment"]["total"] = 25
    newest = {
        "id": "99",
        "created": "2026-01-05T08:00:00.000+0000",
        "author": {"accountId": "x", "displayName": "Jo Park"},
        "body": {"content": [{"text": "Still broken"}]},
    }
    monkeypatch.setattr(
        requests,
        "request",
        routed(
            [
                ("/myself", {"accountId": "me"}),
                ("/issue/SECOPS-7/comment", {"comments": [newest]}),
                ("/search/jql", {"issues": [truncated]}),
            ],
            calls,
        ),
    )

    section = jira.collect({**JIRA, "queries": []}, SINCE)

    assert (
        "SECOPS-7" in section.lines[1]
        and 'last comment by Jo Park 2026-01-05: "Still broken"' in section.lines[1]
    )
    extra = [c for c in calls if c["url"].endswith("/issue/SECOPS-7/comment")]
    assert extra[0]["params"] == {"orderBy": "-created", "maxResults": 1}


def test_jira_search_stops_at_the_page_bound(monkeypatch):
    monkeypatch.setenv("DEMO_EMAIL", "alex@example.com")
    monkeypatch.setenv("DEMO_TOKEN", "s3cr3t-value")
    monkeypatch.setattr(jira, "MAX_PAGES", 3)
    monkeypatch.setattr(
        requests, "request", routed([("/search/jql", {"issues": [], "nextPageToken": "again"})])
    )

    try:
        jira.search(JIRA, "project = SECOPS")
    except RuntimeError as e:
        assert "narrow the JQL" in str(e)
    else:
        raise AssertionError("expected the bound to stop the loop")


GITLAB = {"token_env": "DEMO_TOKEN", "username": "alex", "group": "example-group"}


def test_gitlab_graphql_error_fails_that_check_and_keeps_the_others(monkeypatch):
    calls = []
    monkeypatch.setenv("DEMO_TOKEN", "s3cr3t-value")
    monkeypatch.setattr(
        requests,
        "request",
        routed(
            [
                (
                    "/api/graphql",
                    {"errors": [{"message": "Field 'vulnerabilities' doesn't exist"}], "data": None},
                ),
                (
                    "/merge_requests",
                    [
                        {
                            "title": "Bump parser",
                            "references": {"full": "example-group/app!3"},
                            "author": {"username": "jo"},
                            "web_url": "https://gitlab.com/example-group/app/-/merge_requests/3",
                        }
                    ],
                ),
                ("/access_tokens", FakeResponse({"message": "403 Forbidden"}, 403)),
            ],
            calls,
        ),
    )

    text = "\n".join(gitlab.collect(GITLAB, SINCE).lines)

    assert "**Merge requests waiting on your review** (1)" in text
    assert "CHECK FAILED: vulnerabilities: RuntimeError: GraphQL error" in text
    assert (
        "CHECK FAILED: tokens: RuntimeError: GET gitlab.com/api/v4/groups/example-group/access_tokens" in text
    )
    assert all(c["headers"]["Authorization"] == "Bearer s3cr3t-value" for c in calls)


def test_gitlab_missing_group_is_an_error_not_zero_vulnerabilities(monkeypatch):
    monkeypatch.setenv("DEMO_TOKEN", "s3cr3t-value")
    monkeypatch.setattr(requests, "request", routed([("/api/graphql", {"data": {"group": None}})]))

    text = "\n".join(gitlab.collect({**GITLAB, "checks": ["vulnerabilities"]}, SINCE).lines)

    assert text == "CHECK FAILED: vulnerabilities: RuntimeError: group not found or not visible to this token"


def test_gitlab_without_a_group_skips_the_group_checks(monkeypatch):
    monkeypatch.setenv("DEMO_TOKEN", "s3cr3t-value")
    monkeypatch.setattr(requests, "request", routed([("/merge_requests", [])]))

    lines = gitlab.collect({**GITLAB, "group": ""}, SINCE).lines

    assert lines == [
        "Group checks skipped: no group is configured.",
        "**Merge requests waiting on your review** (0)",
    ]


def test_github_code_scanning_asks_per_severity_and_survives_a_failed_check(monkeypatch):
    calls = []
    monkeypatch.setenv("DEMO_TOKEN", "s3cr3t-value")
    alert = {
        "rule": {"description": "SQL injection"},
        "repository": {"name": "app"},
        "html_url": "https://github.com/example-org/app/security/code-scanning/1",
    }
    monkeypatch.setattr(
        requests,
        "request",
        routed(
            [
                ("/dependabot/alerts", FakeResponse({"message": "no access"}, 403)),
                ("/code-scanning/alerts", [alert]),
                ("/search/issues", {"items": []}),
            ],
            calls,
        ),
    )

    text = "\n".join(
        github.collect({"token_env": "DEMO_TOKEN", "username": "alex", "org": "example-org"}, SINCE).lines
    )

    assert "CHECK FAILED: dependabot: RuntimeError" in text
    assert (
        "**Open critical code scanning alerts** (1)" in text
        and "**Open high code scanning alerts** (1)" in text
    )
    assert [c["params"]["severity"] for c in calls if "code-scanning" in c["url"]] == ["critical", "high"]


def test_dnsfilter_sums_the_real_response_shape(monkeypatch):
    calls = []
    monkeypatch.setenv("DEMO_TOKEN", "s3cr3t-value")
    bucket = {
        "bucket": "2026-01-04T00:00:00Z",
        "policies": [1, 2],
        "policies_names": ["a", "b"],
        "scheduled_policies": [],
        "scheduled_policies_names": None,
        "total_networks": 1,
        "total_proxies": 0,
        "total_agents": 4,
    }
    payload = {
        "data": {
            "organization_ids": [1],
            "organization_names": ["Example"],
            "network_ids": [],
            "network_names": [],
            "values": [{**bucket, "total": 5}, {**bucket, "total": 7}],
        }
    }
    monkeypatch.setattr(requests, "request", routed([("/traffic_reports/total_threats", payload)], calls))

    section = dnsfilter.collect({"token_env": "DEMO_TOKEN", "organization_id": 1}, SINCE)

    assert section.lines == ["12 threat lookups blocked (detail in the DNSFilter console)."]
    assert calls[0]["headers"] == {"Authorization": "s3cr3t-value"}
    assert set(calls[0]["params"]) == {"organization_id", "from", "to"}


def test_dnsfilter_unexpected_shape_is_an_error_not_zero(monkeypatch):
    monkeypatch.setenv("DEMO_TOKEN", "s3cr3t-value")
    for payload in (
        {"error": "invalid organization"},
        {"data": {}},
        {"data": {"values": [{"bucket": "x"}]}},
        None,
    ):
        monkeypatch.setattr(requests, "request", routed([("/total_threats", payload)]))
        try:
            dnsfilter.collect({"token_env": "DEMO_TOKEN", "organization_id": 1}, SINCE)
        except RuntimeError as e:
            assert "unexpected response shape" in str(e)
        else:
            raise AssertionError(f"expected an error for {payload}")


def test_jumpcloud_active_devices_are_not_stale_and_linux_encryption_is_unknown(monkeypatch):
    calls = []
    monkeypatch.setenv("DEMO_TOKEN", "s3cr3t-value")
    old = "2026-01-01T08:00:00Z"
    systems = [
        {
            "hostname": "mbp-alex",
            "osFamily": "darwin",
            "os": "Mac OS X",
            "active": False,
            "lastContact": old,
            "fde": {"active": True},
        },
        {
            "hostname": "mbp-sam",
            "osFamily": "darwin",
            "os": "Mac OS X",
            "active": True,
            "lastContact": old,
            "fde": {"active": True},
        },
        {
            "hostname": "thinkpad-jo",
            "osFamily": "windows",
            "os": "Windows",
            "active": True,
            "lastContact": old,
            "fde": {"active": False},
        },
        {"hostname": "build-01", "osFamily": "linux", "os": "Ubuntu", "active": True, "lastContact": old},
    ]
    monkeypatch.setattr(requests, "request", routed([("/api/systems", {"results": systems})], calls))

    text = "\n".join(
        jumpcloud.collect(
            {"api_key_env": "DEMO_TOKEN", "base_url": "https://console.eu.jumpcloud.com"}, SINCE
        ).lines
    )

    assert "**Not seen for 3+ days** (1)\n- mbp-alex: last contact 2026-01-01" in text
    assert (
        "**Disk encryption not active** (1 of 3 macOS and Windows devices)\n- thinkpad-jo (Windows)" in text
    )
    assert "build-01" not in text and "Disk encryption unknown for 1 other device(s)" in text
    assert calls[0]["url"] == "https://console.eu.jumpcloud.com/api/systems"


def test_scc_new_means_created_in_the_window_and_muted_findings_are_excluded(monkeypatch):
    seen = {}
    recent = (SINCE + dt.timedelta(hours=1)).isoformat()
    findings = [
        {
            "finding": {
                "severity": "HIGH",
                "category": "OPEN_FIREWALL",
                "resourceName": "//example/fw-1",
                "createTime": recent,
            }
        },
        {
            "finding": {
                "severity": "HIGH",
                "category": "OLD_FINDING",
                "resourceName": "//example/x",
                "createTime": "2025-06-01T00:00:00Z",
            }
        },
    ]

    def fake_run(cmd, **kwargs):
        seen["cmd"], seen["kwargs"] = cmd, kwargs
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(findings), stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    lines = gcp_scc.collect({"organization_id": "123456789012"}, SINCE).lines

    assert lines == ["- NEW HIGH OPEN_FIREWALL (//example/fw-1)"]
    assert "--quiet" in seen["cmd"] and seen["kwargs"]["stdin"] is subprocess.DEVNULL
    assert any('NOT mute="MUTED"' in part for part in seen["cmd"])


def test_knowbe4_enrolment_without_an_email_never_matches(monkeypatch):
    monkeypatch.setenv("DEMO_TOKEN", "s3cr3t-value")
    monkeypatch.setattr(
        requests,
        "request",
        routed(
            [
                ("/training/campaigns", []),
                ("/users", [{"email": None}, {"email": "jo@example.com"}]),
                (
                    "/training/enrollments",
                    [
                        {"campaign_name": "Annual", "status": "In Progress", "user": {}},
                        {
                            "campaign_name": "Annual",
                            "status": "In Progress",
                            "user": {"email": "Jo@example.com"},
                        },
                    ],
                ),
            ]
        ),
    )

    assert knowbe4.collect({"token_env": "DEMO_TOKEN"}, SINCE).lines == [
        "**Training outstanding** (1)",
        "- jo@example.com: 1 item(s) in Annual",
    ]
