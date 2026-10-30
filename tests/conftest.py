from __future__ import annotations

import json
import random
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from judging.main import create_app
from judging.settings import Settings
from tm_fixture import make_matches

TZ = ZoneInfo("America/Chicago")
ADMIN_PIN = "246813"
NO_TM = {"tm_url": None, "tm_client_id": None, "tm_client_secret": None, "tm_api_key": None}

MakeClient = Callable[..., TestClient]


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
    # An explicit PIN and no TM keep tests independent of JUDGING_* / .env on the dev machine.
    return Settings(data_dir=data_dir, event_tz=tz, admin_pin=ADMIN_PIN, **NO_TM)


def sign_in(client: TestClient, pin: str = ADMIN_PIN) -> TestClient:
    response = client.post("/api/admin/login", json={"pin": pin})
    assert response.status_code == 200, response.text
    return client


@pytest.fixture
def make_client(settings: Settings) -> MakeClient:
    """`make_client(settings=..., rng=..., admin=True)`: a client on a fresh app."""

    def make(
        app_settings: Settings | None = None, *, rng: random.Random | None = None, admin: bool = True
    ) -> TestClient:
        client = TestClient(create_app(app_settings or settings, rng=rng))
        return sign_in(client, (app_settings or settings).admin_pin or ADMIN_PIN) if admin else client

    return make


@pytest.fixture
def client(make_client: MakeClient) -> TestClient:
    """Signed in as admin, so tests of admin-only routes work unchanged."""
    return make_client(rng=random.Random(1234))


@pytest.fixture
def anon_client(client: TestClient) -> TestClient:
    """Another device on the same app as `client`, not signed in."""
    return TestClient(client.app)


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
