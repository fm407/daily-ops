import pytest
from common import ConfigError, load_config, output_dir, redact, secret


def test_defaults_fill_what_the_file_leaves_out(tmp_path):
    path = tmp_path / "daily-ops.yaml"
    path.write_text("sla_days:\n  High: 10\nsources:\n  jira:\n    enabled: true\n")

    cfg = load_config(path)

    assert cfg["sla_days"] == {"Critical": 7, "High": 10, "Medium": 90, "Low": 180}
    assert cfg["sources"]["jira"]["enabled"] is True
    assert cfg["style"]["avoid_em_dashes"] is False
    assert output_dir(cfg) == tmp_path / "daily-ops-out"


def test_example_config_loads_with_every_source_disabled():
    cfg = load_config("config.example.yaml")

    assert cfg["sources"]
    assert not any(source["enabled"] for source in cfg["sources"].values())
    assert not any(cfg["connectors"][name] for name in ("email", "calendar", "chat"))


def test_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(ConfigError, match="config not found"):
        load_config(tmp_path / "nope.yaml")


def test_invalid_sla_days_are_rejected(tmp_path):
    path = tmp_path / "daily-ops.yaml"
    path.write_text("sla_days:\n  High: soon\n")

    with pytest.raises(ConfigError, match="sla_days"):
        load_config(path)


def test_dotenv_next_to_the_config_is_loaded_without_overriding(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_SET", "from-shell")
    monkeypatch.delenv("DEMO_NEW", raising=False)
    monkeypatch.delenv("DEMO_EXPORTED", raising=False)
    (tmp_path / ".env").write_text(
        "# comment\nDEMO_SET=from-file\nDEMO_NEW='from-file'\nexport DEMO_EXPORTED=abc123 # note\n"
    )
    (tmp_path / "daily-ops.yaml").write_text("{}")

    load_config(tmp_path / "daily-ops.yaml")

    assert secret({"a_env": "DEMO_SET"}, "a_env") == "from-shell"
    assert secret({"a_env": "DEMO_NEW"}, "a_env") == "from-file"
    assert secret({"a_env": "DEMO_EXPORTED"}, "a_env") == "abc123"
    monkeypatch.delenv("DEMO_NEW")
    monkeypatch.delenv("DEMO_EXPORTED")


def test_secret_names_the_missing_variable_not_a_value(monkeypatch):
    monkeypatch.delenv("DEMO_TOKEN", raising=False)

    with pytest.raises(ConfigError, match="DEMO_TOKEN"):
        secret({"token_env": "DEMO_TOKEN"}, "token_env")


def test_redact_blanks_configured_secret_values(monkeypatch):
    monkeypatch.setenv("DEMO_TOKEN", "s3cr3t-value")
    cfg = {"sources": {"x": {"token_env": "DEMO_TOKEN"}}}

    assert redact("error with s3cr3t-value inside", cfg) == "error with *** inside"


def test_redact_leaves_the_users_own_email_readable(monkeypatch):
    monkeypatch.setenv("DEMO_EMAIL", "alex@example.com")
    cfg = {"sources": {"jira": {"email_env": "DEMO_EMAIL"}}}

    assert redact("comment by alex@example.com", cfg) == "comment by alex@example.com"


@pytest.mark.parametrize(
    "text, message",
    [
        ("lookback_days: 0\n", "lookback_days"),
        ("sources:\n  jira: yes\n", "sources.jira must be a mapping"),
        ("sources: [jira]\n", "sources must be a mapping"),
        ("sla_ignore: Low\n", "sla_ignore must be a list"),
        ("sla_ignore: [1]\n", "sla_ignore must be a list"),
    ],
)
def test_invalid_values_are_rejected_at_load(tmp_path, text, message):
    path = tmp_path / "daily-ops.yaml"
    path.write_text(text)

    with pytest.raises(ConfigError, match=message):
        load_config(path)


def test_relative_workbook_path_is_taken_from_the_config_directory(tmp_path):
    path = tmp_path / "daily-ops.yaml"
    path.write_text("sources:\n  workbook:\n    path: controls.xlsx\n")

    assert load_config(path)["sources"]["workbook"]["path"] == str(tmp_path.resolve() / "controls.xlsx")
