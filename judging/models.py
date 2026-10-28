"""Request bodies (pydantic) and the persisted state shape (TypedDicts).

State stays plain JSON-native dicts so the HTTP response is exactly what the
frontend has always received; the TypedDicts document and type-check it.
"""
from __future__ import annotations

from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, field_validator

# --------------------------------------------------------------------------- state

SlotStatus = Literal["scheduled", "checked", "no-show", "rescheduled"]
ScheduleType = Literal["initial", "printed", "noshow", "printed-noshow"]


class SlotDict(TypedDict, total=False):
    judge_id: int
    start: str  # ISO 8601 with offset
    end: str
    team: str | None
    status: SlotStatus
    between: str  # no-show slots only: "Q3 and Q9"


class ScheduleDict(TypedDict, total=False):
    id: str
    label: str
    type: ScheduleType
    file: str  # basename of the archive under data/schedules/
    slots: list[SlotDict]
    created_at: str


class MatchEntryDict(TypedDict):
    time: str
    label: str


class GapDict(TypedDict):
    start: str
    end: str
    minutes: int
    between: str


class SuggestionDict(TypedDict, total=False):
    team: str
    judge_id: int | None
    gaps: list[GapDict]
    not_in_schedule: bool


class ConfigDict(TypedDict, total=False):
    judge_pairs: int
    slot_minutes: int
    duration_minutes: int
    block_minutes: int
    start_time: str
    end_time: str
    event_stats: dict[str, Any]


class StateDict(TypedDict, total=False):
    """Persisted state. The response additionally carries derived `slots`."""

    revision: int
    config: ConfigDict
    locked: bool
    noshow_locked: bool
    team_count: int
    final_match_teams: list[str]
    not_competing: list[str]
    active_schedule_id: str
    schedules: list[ScheduleDict]
    unassigned: list[str]
    team_matches: dict[str, list[MatchEntryDict]]
    no_shows: list[str]
    no_show_suggestions: list[SuggestionDict]
    last_suggestions: SuggestionDict


# ------------------------------------------------------------------------ requests


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _whole_number(value: Any, label: str, minimum: int) -> int:
    """Accept ints, integral floats and numeric strings (form inputs send strings)."""
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a whole number.")
    number: float
    if isinstance(value, (int, float)):
        number = value
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            raise ValueError(f"{label} must be a whole number.") from None
    else:
        raise ValueError(f"{label} must be a whole number.")
    if number != number or number in (float("inf"), float("-inf")) or number != int(number):
        raise ValueError(f"{label} must be a whole number.")
    result = int(number)
    if result < minimum:
        qualifier = "at least 1" if minimum == 1 else f"at least {minimum}"
        raise ValueError(f"{label} must be {qualifier}.")
    return result


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


class GenerateRequest(BaseModel):
    judge_pairs: int = 4
    slot_minutes: int = 10
    block_minutes: int = 8
    start_time: str = ""
    end_time: str = ""
    match_schedule: str = ""
    event_stats: dict[str, Any] | None = None

    @field_validator("judge_pairs", mode="before")
    @classmethod
    def _judge_pairs(cls, value: Any) -> Any:
        return 4 if value is None else _whole_number(value, "Judge pairs", 1)

    @field_validator("slot_minutes", mode="before")
    @classmethod
    def _slot_minutes(cls, value: Any) -> Any:
        return 10 if value is None else _whole_number(value, "Slot minutes", 1)

    @field_validator("block_minutes", mode="before")
    @classmethod
    def _block_minutes(cls, value: Any) -> Any:
        return 8 if value is None else _whole_number(value, "Block minutes", 0)

    @field_validator("start_time", "end_time", "match_schedule", mode="before")
    @classmethod
    def _strings(cls, value: Any) -> str:
        return _text(value)

    @field_validator("event_stats", mode="before")
    @classmethod
    def _event_stats(cls, value: Any) -> Any:
        if _blank(value):
            return None
        if not isinstance(value, dict):
            raise ValueError("event_stats must be an object.")
        return value


class TeamRequest(BaseModel):
    model_config = ConfigDict(validate_default=True)

    team: str = ""

    @field_validator("team", mode="before")
    @classmethod
    def _team(cls, value: Any) -> str:
        team = _text(value).strip()
        if not team:
            raise ValueError("Missing team.")
        return team


class ActiveScheduleRequest(BaseModel):
    model_config = ConfigDict(validate_default=True)

    schedule_id: str = ""

    @field_validator("schedule_id", mode="before")
    @classmethod
    def _schedule_id(cls, value: Any) -> str:
        schedule_id = _text(value).strip()
        if not schedule_id:
            raise ValueError("Missing schedule id.")
        return schedule_id


class SnapshotRequest(BaseModel):
    label: str = "Printed schedule"

    @field_validator("label", mode="before")
    @classmethod
    def _label(cls, value: Any) -> str:
        return _text(value).strip() or "Printed schedule"


class GenerateNoShowRequest(BaseModel):
    match_schedule: str = ""
    event_stats: dict[str, Any] | None = None

    @field_validator("match_schedule", mode="before")
    @classmethod
    def _match_schedule(cls, value: Any) -> str:
        return _text(value).strip()

    @field_validator("event_stats", mode="before")
    @classmethod
    def _event_stats(cls, value: Any) -> Any:
        if _blank(value):
            return None
        if not isinstance(value, dict):
            raise ValueError("event_stats must be an object.")
        return value
