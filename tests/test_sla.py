import datetime as dt

import pytest
import sla
from sla import due_date, highest_open

SLA = {"Critical": 7, "High": 30, "Medium": 90, "Low": 180}
REPORTED = dt.date(2026, 1, 1)


def test_due_date_follows_critical_while_one_is_open():
    assert due_date(REPORTED, ["Medium", "Critical", "High"], SLA) == dt.date(2026, 1, 8)


def test_due_date_moves_to_high_once_criticals_are_fixed():
    assert due_date(REPORTED, ["High", "Medium"], SLA) == dt.date(2026, 1, 31)


def test_due_date_moves_to_medium_once_highs_are_fixed():
    assert due_date(REPORTED, ["Medium"], SLA) == dt.date(2026, 4, 1)


def test_severity_names_are_case_insensitive():
    assert highest_open(["high", " MEDIUM "], SLA) == "High"


def test_unknown_severity_is_an_error_not_a_silent_skip():
    with pytest.raises(ValueError, match="no SLA for severity info"):
        due_date(REPORTED, ["High", "Info"], SLA)


def test_ignored_severity_gives_no_date_and_does_not_shorten_others():
    assert due_date(REPORTED, ["Info", ""], SLA, ignore=["info"]) is None
    assert due_date(REPORTED, ["Info", "Medium"], SLA, ignore=["Info"]) == dt.date(2026, 4, 1)


def test_low_has_its_own_sla_by_default():
    assert due_date(REPORTED, ["Low"], SLA) == dt.date(2026, 6, 30)


def test_tie_between_severities_has_a_deterministic_label():
    tied = {"Urgent": 7, "Critical": 7, "High": 30}

    assert highest_open(["urgent", "critical"], tied) == "Critical"
    assert highest_open(["critical", "urgent"], tied) == "Critical"


def test_cli_exits_non_zero_on_unknown_severity(tmp_path, monkeypatch, capsys):
    (tmp_path / "daily-ops.yaml").write_text("{}")
    monkeypatch.setenv("DAILY_OPS_CONFIG", str(tmp_path / "daily-ops.yaml"))

    with pytest.raises(SystemExit) as stop:
        sla.main(["2026-01-01", "Sev9"])

    assert "no SLA for severity sev9" in str(stop.value.code)


def test_custom_sla_days_are_used():
    assert due_date(REPORTED, ["High"], {"High": 7}) == dt.date(2026, 1, 8)
