from __future__ import annotations

from zoneinfo import ZoneInfo

import pytest

from judging.settings import Settings, resolve_event_tz


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
