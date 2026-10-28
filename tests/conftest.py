from __future__ import annotations

import json
import random
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from judging.main import create_app
from judging.settings import Settings
from tm_fixture import make_matches

TZ = ZoneInfo("America/Chicago")


@pytest.fixture
def tz() -> ZoneInfo:
    return TZ


@pytest.fixture
def first_match(tz: ZoneInfo) -> datetime:
    today = datetime.now(tz).date()
    return datetime(today.year, today.month, today.day, 11, 0, tzinfo=tz)


@pytest.fixture
def matches(first_match: datetime) -> list[dict]:
    return make_matches(first_match)


@pytest.fixture
def match_text(matches: list[dict]) -> str:
    return json.dumps(matches)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    path = tmp_path / "data"
    path.mkdir()
    (path / ".gitkeep").touch()
    return path


@pytest.fixture
def settings(data_dir: Path, tz: ZoneInfo) -> Settings:
    return Settings(data_dir=data_dir, event_tz=tz)


@pytest.fixture
def client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings, rng=random.Random(1234)))


@pytest.fixture
def generate_payload(match_text: str) -> dict:
    # Strings, like the frontend's input .value
    return {
        "judge_pairs": "4",
        "slot_minutes": "10",
        "block_minutes": "8",
        "start_time": "9:00 AM",
        "end_time": "11:00 AM",
        "match_schedule": match_text,
        "event_stats": {"rounds_min": 6, "rounds_max": 6},
    }
