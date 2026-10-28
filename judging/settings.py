"""Runtime configuration (stdlib only).

Environment variables:
    EVENT_TZ  IANA timezone name for the event (e.g. "America/Chicago").
              Defaults to the server's local timezone.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
STATE_PATH = DATA_DIR / "state.json"
SCHEDULES_DIR = DATA_DIR / "schedules"
STATIC_DIR = BASE_DIR / "static"


def _zone_or_none(name: str | None) -> tzinfo | None:
    if not name:
        return None
    try:
        return ZoneInfo(name.strip())
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _local_zone() -> tzinfo:
    """Best-effort IANA zone for the server, falling back to a fixed offset."""
    zone = _zone_or_none(os.environ.get("TZ", "").lstrip(":"))
    if zone:
        return zone
    try:
        target = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in target:
            zone = _zone_or_none(target.split("zoneinfo/", 1)[1])
            if zone:
                return zone
    except OSError:
        pass
    return datetime.now().astimezone().tzinfo or ZoneInfo("UTC")


def resolve_event_tz() -> tzinfo:
    raw = os.environ.get("EVENT_TZ", "").strip()
    if raw:
        zone = _zone_or_none(raw)
        if zone is None:
            raise ValueError(f"EVENT_TZ={raw!r} is not a valid IANA timezone name.")
        return zone
    return _local_zone()


EVENT_TZ = resolve_event_tz()


@dataclass(frozen=True)
class Settings:
    data_dir: Path = DATA_DIR
    static_dir: Path = STATIC_DIR
    event_tz: tzinfo = field(default_factory=lambda: EVENT_TZ)

    @property
    def state_path(self) -> Path:
        return self.data_dir / "state.json"

    @property
    def schedules_dir(self) -> Path:
        return self.data_dir / "schedules"
