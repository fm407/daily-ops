"""The two scripts that can change something: jira_api.py and workbook_append.py."""

import datetime as dt

import jira_api
import openpyxl
import pytest
import requests
import workbook_append
from common import ConfigError


class FakeResponse:
    status_code, content, text = 201, b"x", ""

    def json(self):
        return {"id": "1"}


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_EMAIL", "alex@example.com")
    monkeypatch.setenv("DEMO_TOKEN", "s3cr3t-value")
    return {
        "config_dir": str(tmp_path),
        "output_dir": "out",
        "security_board": {"project": "SECOPS"},
        "sources": {
            "jira": {
                "base_url": "https://example.atlassian.net",
                "email_env": "DEMO_EMAIL",
                "token_env": "DEMO_TOKEN",
                "writable_projects": ["HELP"],
            }
        },
    }


@pytest.fixture
def sent(monkeypatch):
    calls = []

    def fake(method, url, **kwargs):
        calls.append({"method": method, "url": url, **kwargs})
        return FakeResponse()

    monkeypatch.setattr(requests, "request", fake)
    return calls


@pytest.mark.parametrize("method", ["DELETE", "PATCH", "HEAD"])
def test_other_methods_are_refused(cfg, sent, method):
    with pytest.raises(ValueError, match="only GET, POST and PUT"):
        jira_api.run(cfg, [method, "/rest/api/3/issue/SECOPS-1"])
    assert sent == []


@pytest.mark.parametrize(
    "method, path",
    [
        ("POST", "/rest/api/3/issue"),  # creating issues
        ("POST", "/rest/api/3/issue/SECOPS-1"),
        ("POST", "/rest/api/3/issue/SECOPS-1/comment/7"),
        ("POST", "/rest/api/3/issue/SECOPS-1/../../user"),
        ("PUT", "/rest/api/3/issue/SECOPS-1/comment/7"),
        ("PUT", "/rest/api/3/project/SECOPS"),
        ("PUT", "/rest/api/3/issue/SECOPS-1?notifyUsers=false"),
        ("GET", "/rest/api/2/issue/SECOPS-1"),
        ("GET", "/rest/api/3/issue/%2e%2e/%2e%2e/user"),
        ("GET", "/rest/api/3/issue/SECOPS-1/..%2f..%2fuser?fields=summary"),
        ("GET", "@evil.example.com/rest/api/3/issue/SECOPS-1"),
    ],
)
def test_other_paths_are_refused(cfg, sent, method, path):
    with pytest.raises(ValueError, match="limited to"):
        jira_api.run(cfg, [method, path, "{}"])
    assert sent == []


def test_writes_outside_the_configured_projects_are_refused(cfg, sent):
    with pytest.raises(ValueError, match="writes to ENG-4 are refused"):
        jira_api.run(cfg, ["PUT", "/rest/api/3/issue/ENG-4", '{"fields": {}}'])
    assert sent == []


def test_allowed_writes_and_reads_go_through(cfg, sent):
    jira_api.run(cfg, ["POST", "/rest/api/3/issue/SECOPS-1/transitions", '{"transition": {"id": "5"}}'])
    jira_api.run(cfg, ["put", "/rest/api/3/issue/HELP-2", '{"fields": {"duedate": "2026-01-31"}}'])
    jira_api.run(cfg, ["GET", "/rest/api/3/issue/ENG-4?fields=summary"])

    assert [c["method"] for c in sent] == ["POST", "PUT", "GET"]
    assert sent[0]["json"] == {"transition": {"id": "5"}}
    assert all(c["allow_redirects"] is False for c in sent)


def test_comment_file_outside_the_output_directory_is_refused(cfg, sent, tmp_path):
    outside = tmp_path / "notes.txt"
    outside.write_text("hello")

    with pytest.raises(ValueError, match="inside the output directory"):
        jira_api.run(cfg, ["comment", "SECOPS-1", str(outside)])
    with pytest.raises(ValueError, match="inside the output directory"):
        jira_api.run(cfg, ["comment", "SECOPS-1", str(tmp_path / "out" / ".." / "notes.txt")])
    assert sent == []


def test_comment_key_is_validated(cfg, sent, tmp_path):
    with pytest.raises(ValueError, match="not an issue key"):
        jira_api.run(cfg, ["comment", "SECOPS-1/../../user", str(tmp_path / "out" / "x.txt")])
    assert sent == []


def test_comment_posts_the_draft_with_paragraphs_and_line_breaks(cfg, sent, tmp_path):
    draft = tmp_path / "out" / "2026-01-05" / "Jira_SECOPS-1_Comment.txt"
    draft.parent.mkdir(parents=True)
    draft.write_text("Jira comment on SECOPS-1\n\nChecked on main.\nResult: still open.\n\n  \nThanks.\n")

    jira_api.run(cfg, ["comment", "SECOPS-1", str(draft)])

    assert sent[0]["url"] == "https://example.atlassian.net/rest/api/3/issue/SECOPS-1/comment"
    first, second = sent[0]["json"]["body"]["content"]
    assert first["content"] == [
        {"type": "text", "text": "Checked on main."},
        {"type": "hardBreak"},
        {"type": "text", "text": "Result: still open."},
    ]
    assert second["content"] == [{"type": "text", "text": "Thanks."}]


def test_redirect_is_an_error_not_followed(cfg, monkeypatch):
    class Redirect(FakeResponse):
        status_code = 302

    monkeypatch.setattr(requests, "request", lambda *a, **k: Redirect())

    with pytest.raises(RuntimeError, match="was redirected"):
        jira_api.run(cfg, ["GET", "/rest/api/3/myself"])


# ---------- workbook ----------


@pytest.fixture
def workbook(tmp_path):
    path = tmp_path / "controls.xlsx"
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Plan"
    sheet.append(["Control", "Status", "Notes"])
    sheet.append(["AC-1", "Open", "2026-01-02: first note"])
    sheet.append(["AC-2", "Open", None])
    book.save(path)
    return {"path": str(path), "sheet": "Plan", "control_column": "A", "notes_column": "C"}


def notes(cfg, row):
    return openpyxl.load_workbook(cfg["path"])["Plan"].cell(row, 3).value


def test_append_adds_a_dated_line_and_keeps_history_and_a_backup(workbook):
    now = dt.datetime(2026, 1, 5, 9, 30)

    backup = workbook_append.append_line(workbook, "AC-1", "Fix deployed,\n verified", now)
    second = workbook_append.append_line(workbook, "AC-2", "Owner confirmed", now)

    assert notes(workbook, 2) == "2026-01-02: first note\n2026-01-05: Fix deployed, verified"
    assert notes(workbook, 3) == "2026-01-05: Owner confirmed"
    assert backup.name == "controls.backup-20260105-093000.xlsx"
    assert second.name == "controls.backup-20260105-093000-2.xlsx"  # same second, earlier backup kept
    assert openpyxl.load_workbook(backup)["Plan"].cell(2, 3).value == "2026-01-02: first note"


def test_append_refuses_while_the_workbook_is_open(workbook, tmp_path):
    (tmp_path / "~$controls.xlsx").write_text("")

    with pytest.raises(RuntimeError, match="is open"):
        workbook_append.append_line(workbook, "AC-1", "text")

    assert notes(workbook, 2) == "2026-01-02: first note"
    assert not list(tmp_path.glob("*.backup-*"))


def test_append_needs_exactly_one_matching_row_and_a_notes_column(workbook, tmp_path):
    with pytest.raises(ValueError, match="found 0"):
        workbook_append.append_line(workbook, "AC-9", "text")
    with pytest.raises(ConfigError, match="notes_column"):
        workbook_append.append_line({**workbook, "notes_column": ""}, "AC-1", "text")
    assert not list(tmp_path.glob("*.backup-*"))


def test_append_refuses_a_formula_cell_and_an_oversized_cell(workbook, tmp_path):
    book = openpyxl.load_workbook(workbook["path"])
    book["Plan"].cell(2, 3).value = "=A2"
    book["Plan"].cell(3, 3).value = "x" * 32760
    book.save(workbook["path"])

    with pytest.raises(ValueError, match="holds a formula"):
        workbook_append.append_line(workbook, "AC-1", "text")
    with pytest.raises(ValueError, match="would exceed 32767"):
        workbook_append.append_line(workbook, "AC-2", "text")
    with pytest.raises(ValueError, match="control character"):
        workbook_append.append_line({**workbook, "notes_column": "D"}, "AC-1", "bad \x07 char")
    assert not list(tmp_path.glob("*.backup-*"))


def test_append_refuses_files_that_are_not_xlsx_workbooks(workbook, tmp_path):
    macro = tmp_path / "controls.xlsm"
    macro.write_bytes(b"x")
    broken = tmp_path / "broken.xlsx"
    broken.write_text("not a zip")

    with pytest.raises(ConfigError, match="only .xlsx"):
        workbook_append.append_line({**workbook, "path": str(macro)}, "AC-1", "text")
    with pytest.raises(RuntimeError, match="not a readable .xlsx"):
        workbook_append.append_line({**workbook, "path": str(broken)}, "AC-1", "text")
    with pytest.raises(ConfigError, match="sheet 'Nope'"):
        workbook_append.append_line({**workbook, "sheet": "Nope"}, "AC-1", "text")


def test_workbook_source_lists_open_rows_due_soon(tmp_path):
    from sources import workbook as source

    path = tmp_path / "plan.xlsx"
    book = openpyxl.Workbook()
    book.active.title = "Plan"
    for values in (
        ["Control", "Status", "Target"],
        ["AC-1", "Open", dt.date(2026, 1, 2)],
        ["AC-2", "Closed 2026-01-01", dt.date(2026, 1, 2)],
        ["AC-3", "In progress", dt.date(2099, 1, 1)],
    ):
        book.active.append(values)
    book.save(path)
    cfg = {
        "path": str(path),
        "sheet": "Plan",
        "control_column": "A",
        "status_column": "B",
        "target_date_column": "C",
    }

    lines = source.collect(cfg, dt.datetime.now(dt.UTC)).lines

    assert lines == [
        "2 open rows; 1 with a target date within 7 days or past.",
        "- AC-1: target 2026-01-02, status Open",
    ]
