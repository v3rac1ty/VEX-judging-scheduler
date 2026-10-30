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
    JUDGING_ADMIN_PIN PIN that unlocks the admin controls (4-12 digits, so a phone's numeric
                      keypad can type it). Unset: a random 6-digit PIN is generated once and
                      kept in <data dir>/admin_pin.txt (see auth.py).

Values may also come from `<repo>/.env` (KEY=VALUE lines, see .env.example); a variable that
is already set in the real environment wins over the file.
"""
from __future__ import annotations

import os
import re
from collections.abc import MutableMapping
from dataclasses import dataclass, field
from datetime import datetime, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
STATIC_DIR = BASE_DIR / "static"
DOTENV_PATH = BASE_DIR / ".env"

ADMIN_PIN_MIN_LENGTH = 4
ADMIN_PIN_MAX_LENGTH = 12

_DOTENV_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_INLINE_COMMENT = re.compile(r"\s+#")


def _dotenv_value(raw: str) -> str:
    """Unquote a quoted value (text after the closing quote is ignored), else drop a ` # comment`."""
    if raw[:1] in ("'", '"'):
        end = raw.find(raw[0], 1)
        if end > 0:
            return raw[1:end]
    return _INLINE_COMMENT.split(raw, maxsplit=1)[0].rstrip()


def parse_dotenv(text: str) -> dict[str, str]:
    """`KEY=VALUE` lines; `#` comment lines, an `export ` prefix and quotes are tolerated.

    Lines that don't look like an assignment are skipped rather than failing startup.
    """
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if sep and _DOTENV_KEY.fullmatch(key):
            values[key] = _dotenv_value(value.strip())
    return values


def load_dotenv(
    path: Path = DOTENV_PATH, environ: MutableMapping[str, str] | None = None
) -> dict[str, str]:
    """Copy `path`'s variables into `environ` (default os.environ) without overriding any.

    Returns what the file defined; a missing or unreadable file defines nothing.
    """
    target = os.environ if environ is None else environ
    try:
        values = parse_dotenv(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        return {}
    for key, value in values.items():
        target.setdefault(key, value)
    return values


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


def is_valid_admin_pin(pin: str) -> bool:
    # ASCII digits only: str.isdigit() would also accept characters like "²" and "٣".
    return (
        ADMIN_PIN_MIN_LENGTH <= len(pin) <= ADMIN_PIN_MAX_LENGTH
        and pin.isascii()
        and pin.isdigit()
    )


def resolve_admin_pin() -> str | None:
    """JUDGING_ADMIN_PIN, or None when unset/blank (auth.py then uses the data-dir PIN file)."""
    raw = os.environ.get("JUDGING_ADMIN_PIN", "")
    if not raw.strip():
        return None
    if not is_valid_admin_pin(raw):  # never echo the value: it is a secret
        raise ValueError(
            f"JUDGING_ADMIN_PIN must be {ADMIN_PIN_MIN_LENGTH}-{ADMIN_PIN_MAX_LENGTH} digits "
            "(numbers only, no spaces)."
        )
    return raw


load_dotenv()  # before anything below reads the environment
EVENT_TZ = resolve_event_tz()


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=resolve_data_dir)
    static_dir: Path = STATIC_DIR
    event_tz: tzinfo = field(default_factory=lambda: EVENT_TZ)
    host: str = field(default_factory=resolve_host)
    port: int = field(default_factory=resolve_port)
    # None: auth.py reads or creates admin_pin.txt. Tests inject a PIN here.
    admin_pin: str | None = field(default_factory=resolve_admin_pin, repr=False)

    def __post_init__(self) -> None:
        if self.admin_pin is not None and not is_valid_admin_pin(self.admin_pin):
            raise ValueError(
                f"The admin PIN must be {ADMIN_PIN_MIN_LENGTH}-{ADMIN_PIN_MAX_LENGTH} digits "
                "(numbers only, no spaces)."
            )

    @property
    def state_path(self) -> Path:
        return self.data_dir / "state.json"

    @property
    def schedules_dir(self) -> Path:
        return self.data_dir / "schedules"

    @property
    def draft_path(self) -> Path:
        return self.data_dir / "draft.json"

    @property
    def admin_pin_path(self) -> Path:
        return self.data_dir / "admin_pin.txt"

    @property
    def session_key_path(self) -> Path:
        return self.data_dir / "session_key"
