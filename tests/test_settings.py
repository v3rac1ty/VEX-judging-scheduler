from __future__ import annotations

from zoneinfo import ZoneInfo

import pytest

from judging.settings import DATA_DIR, Settings, resolve_event_tz


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
