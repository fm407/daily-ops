import datetime as dt

import state


def cfg(tmp_path):
    return {"config_dir": str(tmp_path), "output_dir": "out"}


def test_record_then_show_returns_todays_decision(tmp_path):
    now = dt.datetime(2026, 1, 5, 9, 30)

    state.record(cfg(tmp_path), "jira:SECOPS-1", "approved", "Done|10|2026-01-31", "Verify SECOPS-1", now)

    shown = state.show(cfg(tmp_path), now.date())
    assert shown["today"]["jira:SECOPS-1"]["outcome"] == "approved"
    assert shown["today"]["jira:SECOPS-1"]["fingerprint"] == "Done|10|2026-01-31"
    assert shown["dropped"] == {}


def test_dropped_item_stays_dropped_on_later_days(tmp_path):
    state.record(
        cfg(tmp_path), "device:mbp-alex", "dropped", "2026-01-01", "Stale laptop", dt.datetime(2026, 1, 5)
    )

    later = state.show(cfg(tmp_path), dt.date(2026, 1, 9))

    assert later["today"] == {}
    assert later["dropped"]["device:mbp-alex"]["fingerprint"] == "2026-01-01"


def test_second_decision_keeps_the_first(tmp_path):
    now = dt.datetime(2026, 1, 5, 9, 30)
    state.record(cfg(tmp_path), "jira:SECOPS-1", "skipped", "a", "one", now)

    state.record(cfg(tmp_path), "jira:SECOPS-2", "approved", "b", "two", now)

    assert set(state.show(cfg(tmp_path), now.date())["today"]) == {"jira:SECOPS-1", "jira:SECOPS-2"}


def test_unknown_outcome_is_rejected(tmp_path):
    try:
        state.record(cfg(tmp_path), "jira:SECOPS-1", "maybe", "a", "one")
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_corrupt_file_is_set_aside_and_the_run_continues(tmp_path, capsys):
    now = dt.datetime(2026, 1, 5, 9, 30)
    dropped = tmp_path / "out" / "dropped.json"
    dropped.parent.mkdir(parents=True)
    dropped.write_text('{"jira:SECOPS-9": {"outcome": "dro')

    state.record(cfg(tmp_path), "jira:SECOPS-1", "dropped", "a", "one", now)

    assert set(state.show(cfg(tmp_path), now.date())["dropped"]) == {"jira:SECOPS-1"}
    kept = list(dropped.parent.glob("dropped.json.corrupt-*"))
    assert len(kept) == 1 and kept[0].read_text().startswith('{"jira:SECOPS-9"')
    assert "kept as dropped.json.corrupt-" in capsys.readouterr().err
    assert not list(dropped.parent.rglob("*.tmp"))
