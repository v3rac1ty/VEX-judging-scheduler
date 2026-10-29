"""Runtime configuration (stdlib only).

Environment variables (prefixed so a generic HOST/PORT exported by a shell or
container can't silently change where the server listens):
    EVENT_TZ          IANA timezone name for the event (e.g. "America/Chicago").
                      Defaults to the server's local timezone.
    JUDGING_HOST      Interface `python -m judging` binds to (default 0.0.0.0: every LAN interface).
    JUDGING_PORT      TCP port for `python -m judging` (default 8000).
    JUDGING_DATA_DIR  Directory for state.json, draft.json and schedules/ (default: <repo>/data).
                      Point it elsewhere for manual testing or a practice copy. Use a
                      dedicated directory: reset deletes stray *.json files in it.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
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


def resolve_host() -> str:
    return os.environ.get("JUDGING_HOST", "").strip() or "0.0.0.0"


def resolve_port() -> int:
    raw = os.environ.get("JUDGING_PORT", "").strip()
    if not raw:
        return 8000
    try:
        port = int(raw)
    except ValueError:
        port = -1
    if not 0 <= port <= 65535:
        raise ValueError(f"JUDGING_PORT={raw!r} is not a valid TCP port.")
    return port


def resolve_data_dir() -> Path:
    raw = os.environ.get("JUDGING_DATA_DIR", "").strip()
    return Path(raw).expanduser().resolve() if raw else DATA_DIR


EVENT_TZ = resolve_event_tz()


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=resolve_data_dir)
    static_dir: Path = STATIC_DIR
    event_tz: tzinfo = field(default_factory=lambda: EVENT_TZ)
    host: str = field(default_factory=resolve_host)
    port: int = field(default_factory=resolve_port)

    @property
    def state_path(self) -> Path:
        return self.data_dir / "state.json"

    @property
    def schedules_dir(self) -> Path:
        return self.data_dir / "schedules"

    @property
    def draft_path(self) -> Path:
        return self.data_dir / "draft.json"
