from __future__ import annotations

from zoneinfo import ZoneInfo

import pytest

from judging.settings import (
    DATA_DIR,
    TM_AUTH_URL,
    Settings,
    is_valid_admin_pin,
    load_dotenv,
    parse_dotenv,
    resolve_admin_pin,
    resolve_event_tz,
)


def test_event_tz_from_env(monkeypatch):
    monkeypatch.setenv("EVENT_TZ", "America/Denver")
    assert resolve_event_tz() == ZoneInfo("America/Denver")


def test_event_tz_invalid(monkeypatch):
    monkeypatch.setenv("EVENT_TZ", "Mars/Olympus")
    with pytest.raises(ValueError, match="EVENT_TZ"):
        resolve_event_tz()


def test_event_tz_defaults_to_local(monkeypatch):
    monkeypatch.delenv("EVENT_TZ", raising=False)
    assert resolve_event_tz() is not None


def test_settings_paths(tmp_path):
    settings = Settings(data_dir=tmp_path)
    assert settings.state_path == tmp_path / "state.json"
    assert settings.schedules_dir == tmp_path / "schedules"


def test_host_and_port_use_prefixed_env(monkeypatch):
    # Generic HOST/PORT (often exported by shells/containers) are ignored.
    monkeypatch.setenv("HOST", "10.9.9.9")
    monkeypatch.setenv("PORT", "1234")
    monkeypatch.delenv("JUDGING_HOST", raising=False)
    monkeypatch.delenv("JUDGING_PORT", raising=False)
    settings = Settings()
    assert (settings.host, settings.port) == ("0.0.0.0", 8000)

    monkeypatch.setenv("JUDGING_HOST", "127.0.0.1")
    monkeypatch.setenv("JUDGING_PORT", "0")
    settings = Settings()
    assert (settings.host, settings.port) == ("127.0.0.1", 0)


@pytest.mark.parametrize("raw", ["abc", "70000", "-1"])
def test_invalid_port(monkeypatch, raw):
    monkeypatch.setenv("JUDGING_PORT", raw)
    with pytest.raises(ValueError, match="JUDGING_PORT"):
        Settings()


def test_data_dir_override(monkeypatch, tmp_path):
    monkeypatch.delenv("JUDGING_DATA_DIR", raising=False)
    assert Settings().data_dir == DATA_DIR
    monkeypatch.setenv("JUDGING_DATA_DIR", "   ")
    assert Settings().data_dir == DATA_DIR

    monkeypatch.setenv("JUDGING_DATA_DIR", str(tmp_path / "practice"))
    settings = Settings()
    assert settings.data_dir == (tmp_path / "practice").resolve()
    assert settings.state_path == settings.data_dir / "state.json"
    assert settings.draft_path == settings.data_dir / "draft.json"


def test_parse_dotenv():
    text = """
# comment line
EVENT_TZ=America/Chicago
export JUDGING_PORT = 8080
JUDGING_HOST="127.0.0.1"   # trailing comment after quotes
JUDGING_ADMIN_PIN='12 34#5'
UNQUOTED=abc # inline comment
HASH_IN_VALUE=abc#def
EMPTY=
SPACES =   padded value
  INDENTED=yes
not an assignment
1BAD=no
BAD KEY=no
DUP=first
DUP=second
"""
    assert parse_dotenv(text) == {
        "EVENT_TZ": "America/Chicago",
        "JUDGING_PORT": "8080",
        "JUDGING_HOST": "127.0.0.1",
        "JUDGING_ADMIN_PIN": "12 34#5",
        "UNQUOTED": "abc",
        "HASH_IN_VALUE": "abc#def",
        "EMPTY": "",
        "SPACES": "padded value",
        "INDENTED": "yes",
        "DUP": "second",
    }


def test_load_dotenv_never_overrides_real_env(tmp_path):
    path = tmp_path / ".env"
    path.write_text("JUDGING_PORT=9000\nJUDGING_HOST=127.0.0.1\n", encoding="utf-8")
    environ = {"JUDGING_PORT": "8001"}
    assert load_dotenv(path, environ) == {"JUDGING_PORT": "9000", "JUDGING_HOST": "127.0.0.1"}
    assert environ == {"JUDGING_PORT": "8001", "JUDGING_HOST": "127.0.0.1"}


def test_load_dotenv_missing_or_unreadable_file(tmp_path):
    environ: dict[str, str] = {}
    assert load_dotenv(tmp_path / "missing", environ) == {}
    (tmp_path / "binary").write_bytes(b"\xff\xfe\x00")
    assert load_dotenv(tmp_path / "binary", environ) == {}
    assert environ == {}


@pytest.mark.parametrize("raw", ["", "   "])
def test_admin_pin_unset(monkeypatch, raw):
    monkeypatch.setenv("JUDGING_ADMIN_PIN", raw)
    assert resolve_admin_pin() is None


@pytest.mark.parametrize("raw", ["1234", "0042", "9" * 12])
def test_admin_pin_from_env(monkeypatch, raw):
    monkeypatch.setenv("JUDGING_ADMIN_PIN", raw)
    assert Settings().admin_pin == raw


@pytest.mark.parametrize(
    "raw", ["123", "1" * 13, " 1234", "1234 ", "1234\n", "12ab34", "12-34", "12 34", "²³⁴⁵", "١٢٣٤"]
)
def test_invalid_admin_pin_from_env_raises(monkeypatch, raw):
    monkeypatch.setenv("JUDGING_ADMIN_PIN", raw)
    with pytest.raises(ValueError, match="JUDGING_ADMIN_PIN") as exc:
        Settings()
    assert raw.strip() not in str(exc.value)  # the secret is never echoed


@pytest.mark.parametrize("pin", ["12", " 1234", "1" * 13, "12ab34"])
def test_invalid_injected_admin_pin_raises(tmp_path, pin):
    with pytest.raises(ValueError, match="admin PIN"):
        Settings(data_dir=tmp_path, admin_pin=pin)


@pytest.mark.parametrize(
    ("pin", "valid"),
    [("123", False), ("1234", True), ("1" * 12, True), ("1" * 13, False), ("", False), ("12a4", False)],
)
def test_is_valid_admin_pin(pin, valid):
    assert is_valid_admin_pin(pin) is valid


def test_admin_pin_not_in_repr(tmp_path):
    settings = Settings(data_dir=tmp_path, admin_pin="987654")
    assert "987654" not in repr(settings)
    assert settings.admin_pin_path == tmp_path / "admin_pin.txt"
    assert settings.session_key_path == tmp_path / "session_key"


# ------------------------------------------------------------- tournament manager

TM_ENV = (
    "JUDGING_TM_URL",
    "JUDGING_TM_CLIENT_ID",
    "JUDGING_TM_CLIENT_SECRET",
    "JUDGING_TM_API_KEY",
    "JUDGING_TM_DIVISION",
    "JUDGING_TM_POLL_SECONDS",
    "JUDGING_TM_AUTH_URL",
)


@pytest.fixture
def tm_env(monkeypatch):
    for name in TM_ENV:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_tm_defaults_when_unset(tm_env, tmp_path):
    settings = Settings(data_dir=tmp_path)
    assert settings.tm_url is None and settings.tm_configured is False
    assert (settings.tm_division, settings.tm_poll_seconds) == (1, 60)
    assert settings.tm_auth_url == TM_AUTH_URL == "https://auth.vextm.dwabtech.com/oauth2/token"
    assert settings.tm_missing == list(TM_ENV[:4])


def test_tm_from_env_and_secrets_hidden_from_repr(tm_env, tmp_path):
    tm_env.setenv("JUDGING_TM_URL", " http://192.168.1.50:8080/ ")
    tm_env.setenv("JUDGING_TM_CLIENT_ID", "id-Aa11")
    tm_env.setenv("JUDGING_TM_CLIENT_SECRET", "secret-Bb22")
    tm_env.setenv("JUDGING_TM_API_KEY", "key-Cc33")
    tm_env.setenv("JUDGING_TM_DIVISION", "2")
    tm_env.setenv("JUDGING_TM_POLL_SECONDS", "90")
    settings = Settings(data_dir=tmp_path)
    assert settings.tm_url == "http://192.168.1.50:8080"
    assert settings.tm_configured and settings.tm_missing == []
    assert (settings.tm_division, settings.tm_poll_seconds) == (2, 90)
    text = repr(settings)
    assert "192.168.1.50" in text
    for secret in ("id-Aa11", "secret-Bb22", "key-Cc33"):
        assert secret not in text


@pytest.mark.parametrize("raw", ["59", "0", "-60", "abc", "60.5"])
def test_tm_poll_seconds_below_60_fails_startup(tm_env, raw):
    tm_env.setenv("JUDGING_TM_POLL_SECONDS", raw)
    with pytest.raises(ValueError, match="JUDGING_TM_POLL_SECONDS.*at least 60"):
        Settings()


@pytest.mark.parametrize("raw", ["0", "-1", "one", "1.5"])
def test_tm_division_must_be_positive(tm_env, raw):
    tm_env.setenv("JUDGING_TM_DIVISION", raw)
    with pytest.raises(ValueError, match="JUDGING_TM_DIVISION"):
        Settings()


@pytest.mark.parametrize(
    "raw",
    ["192.168.1.50:8080", "ftp://tm.local", "http://", "http://admin:hunter2@tm.local",
     "http://tm.local:99999", "http://tm.local/?x=1"],
)
def test_tm_url_is_validated_without_echoing_it(tm_env, raw):
    tm_env.setenv("JUDGING_TM_URL", raw)
    with pytest.raises(ValueError, match="JUDGING_TM_URL") as caught:
        Settings()
    assert "hunter2" not in str(caught.value)


def test_tm_values_are_validated_when_injected(tmp_path):
    with pytest.raises(ValueError, match="at least 60"):
        Settings(data_dir=tmp_path, tm_poll_seconds=30)
    with pytest.raises(ValueError, match="division"):
        Settings(data_dir=tmp_path, tm_division=0)
    with pytest.raises(ValueError, match="TM URL"):
        Settings(data_dir=tmp_path, tm_url="tm.local")
