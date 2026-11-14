"""Match-schedule parsing: raw text (JSON or TM log output) -> per-team match lists."""
from __future__ import annotations

import json
import re
from datetime import date, datetime, tzinfo
from typing import Any, Iterator, TypedDict

NOT_FOUND_MESSAGE = "Could not find JSON array in match schedule input."

# A log marker such as ">> Matches: {" or ">> Fetched matches:" (mirrors stripLogPreamble).
_LOG_MARKER = re.compile(r">>[^\n]*(?:matches|Matches)\s*:\s*")
_MAX_ARRAY_PROBES = 200


class MatchEntry(TypedDict):
    time: datetime
    label: str


TeamMatches = dict[str, list[MatchEntry]]


def strip_log_preamble(text: str) -> str:
    """Drop everything before the JSON that follows a `>> ... matches:` log marker."""
    marker = _LOG_MARKER.search(text)
    if marker:
        after = text[marker.end():]
        json_start = re.search(r"[\[{]", after)
        if json_start:
            return after[json_start.start():]
    return text


def _as_match_list(value: Any) -> list[dict[str, Any]] | None:
    if isinstance(value, dict):
        for key in ("Matches", "matches"):
            if isinstance(value.get(key), list):
                value = value[key]
                break
        else:
            return None
    if not isinstance(value, list):
        return None
    matches = [item for item in value if isinstance(item, dict)]
    if value and not matches:
        return None  # e.g. "[1, 2]" or ["INFO"]: not a match list
    return matches


def _json_candidates(text: str) -> Iterator[Any]:
    decoder = json.JSONDecoder()
    try:
        yield json.loads(text)
    except json.JSONDecodeError:
        pass

    stripped = text.lstrip()
    if stripped[:1] in ("[", "{"):
        # JSON followed by trailing log lines.
        try:
            yield decoder.raw_decode(stripped)[0]
        except json.JSONDecodeError:
            pass

    # Same fallback as the frontend: first "[" through the last "]".
    greedy = re.search(r"\[(.*)\]", text, re.DOTALL)
    if greedy:
        try:
            yield json.loads("[" + greedy.group(1) + "]")
        except json.JSONDecodeError:
            pass

    # Last resort: an array embedded anywhere in log text.
    for probes, bracket in enumerate(re.finditer(r"\[", text)):
        if probes >= _MAX_ARRAY_PROBES:
            break
        try:
            yield decoder.raw_decode(text, bracket.start())[0]
        except json.JSONDecodeError:
            continue


def parse_matches(raw_text: str) -> list[dict[str, Any]]:
    """Return the list of TM match objects contained in `raw_text`.

    Accepts a bare JSON array, `{"Matches": [...]}`, TM log output with a
    `>> Matches:` preamble, or an array embedded in other log text.
    Raises ValueError when no match list can be found.
    """
    text = str(raw_text or "")
    if not text.strip():
        raise ValueError(NOT_FOUND_MESSAGE)
    working = strip_log_preamble(text)
    sources = [working] if working is text else [working, text]
    fallback: list[dict[str, Any]] | None = None
    for source in sources:
        for candidate in _json_candidates(source):
            matches = _as_match_list(candidate)
            if matches is None:
                continue
            if any("matchInfo" in item for item in matches):
                return matches
            if fallback is None or (matches and not fallback):
                fallback = matches
    if fallback is not None:
        return fallback
    raise ValueError(NOT_FOUND_MESSAGE)


def match_label(match_info: dict[str, Any]) -> str:
    match_tuple = match_info.get("matchTuple") or {}
    round_name = str(match_tuple.get("round", "")).upper()
    match_num = match_tuple.get("match")
    if round_name and match_num is not None:
        if round_name == "QUAL":
            return f"Q{match_num}"
        return f"{round_name}{match_num}"
    if match_num is not None:
        return f"Match {match_num}"
    return "Match"


def _epoch_seconds(value: Any) -> int | None:
    try:
        seconds = int(float(value))
    except (TypeError, ValueError):
        return None
    # TM reports unscheduled matches with 0; they would create a gap from 1970.
    return seconds if seconds > 0 else None


def extract_team_matches(matches: list[dict[str, Any]], tz: tzinfo) -> TeamMatches:
    """Map team number -> chronologically sorted [{time, label}]."""
    team_matches: TeamMatches = {}
    for match in matches:
        if not isinstance(match, dict):
            continue
        info = match.get("matchInfo") or {}
        seconds = _epoch_seconds(info.get("timeScheduled"))
        if seconds is None:
            continue
        match_time = datetime.fromtimestamp(seconds, tz=tz)
        label = match_label(info)
        for alliance in info.get("alliances") or []:
            for team in (alliance or {}).get("teams") or []:
                team_num = str((team or {}).get("number", "")).strip()
                if team_num:
                    team_matches.setdefault(team_num, []).append(
                        {"time": match_time, "label": label}
                    )
    for entries in team_matches.values():
        entries.sort(key=lambda m: m["time"])
    return team_matches


def serialize_team_matches(team_matches: TeamMatches) -> dict[str, list[dict[str, str]]]:
    return {
        team: [{"time": m["time"].isoformat(), "label": m["label"]} for m in entries]
        for team, entries in team_matches.items()
    }


def normalize_match_entries(entries: list[Any]) -> list[MatchEntry]:
    """Accept stored ({time: iso}) or parsed ({time: datetime}) entries; sort by time."""
    normalized: list[MatchEntry] = []
    for entry in entries:
        value = entry.get("time")
        match_time = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        normalized.append({"time": match_time, "label": entry.get("label", "Match")})
    normalized.sort(key=lambda m: m["time"])
    return normalized


_TEAM_NUMBER = re.compile(r"^\s*(\d+)\s*(.*?)\s*$")


def team_sort_key(team: str) -> tuple[int, int, str]:
    """Numerical order: 99A < 1234A < 1234B < 5678X; names without a number go last."""
    text = str(team)
    match = _TEAM_NUMBER.match(text)
    if match:
        return (0, int(match.group(1)), match.group(2).upper())
    return (1, 0, text.strip().upper())


_TIME_12H = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*([AaPp])\.?\s*[Mm]\.?$")
_TIME_24H = re.compile(r"^(\d{1,2}):(\d{2})$")


def parse_time(raw_time: str, label: str, tz: tzinfo, today: date | None = None) -> datetime:
    """Parse "9:00 AM" / "9 am" / "13:30" as a time today in `tz`."""
    time_text = str(raw_time or "").strip()
    if not time_text:
        raise ValueError(f"Missing {label}.")
    day = today or datetime.now(tz).date()

    match = _TIME_12H.match(time_text)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        meridiem = match.group(3).lower()
        if hour < 1 or hour > 12 or minute > 59:
            raise ValueError(f"Invalid {label}.")
        if meridiem == "p" and hour != 12:
            hour += 12
        if meridiem == "a" and hour == 12:
            hour = 0
        return datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)

    match = _TIME_24H.match(time_text)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        if hour <= 23 and minute <= 59:
            return datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)
    raise ValueError(f"{label.capitalize()} must be like 9:00 AM.")


class MatchPreview(TypedDict):
    match_count: int
    team_count: int
    first_match: str | None  # ISO 8601 in the event timezone
    # Lunch break detected in the qualification schedule (ISO 8601), else None.
    lunch_start: str | None
    lunch_end: str | None
    # When eliminations start: the first elimination match, or (none scheduled
    # yet) the end of the last qualification match. Interviews must end by then.
    elims_start: str | None
    error: str | None


# Rounds that are not eliminations; any other round name (R16, QF, SF, F, ...) is.
NON_ELIM_ROUNDS = frozenset({"", "QUAL", "PRACTICE"})

# A pause in qualification matches this long (and several match cycles long) is lunch.
LUNCH_MIN_MINUTES = 30
LUNCH_MIN_CYCLES = 4


def detect_lunch(times: list[int]) -> tuple[int, int] | None:
    """(start, end) epoch seconds of the longest break between scheduled matches, if any.

    The break starts one typical match cycle after the last match before it (that
    match is still being played) and ends when the next match is scheduled.
    """
    ordered = sorted(set(times))
    if len(ordered) < 3:
        return None
    diffs = [b - a for a, b in zip(ordered, ordered[1:])]
    cycle = sorted(diffs)[len(diffs) // 2]  # median spacing between matches
    longest = max(range(len(diffs)), key=lambda i: diffs[i])
    gap = diffs[longest]
    if gap < max(LUNCH_MIN_MINUTES * 60, LUNCH_MIN_CYCLES * cycle):
        return None
    return ordered[longest] + cycle, ordered[longest + 1]


def _median_cycle(times: list[int]) -> int:
    ordered = sorted(set(times))
    diffs = [b - a for a, b in zip(ordered, ordered[1:])]
    return sorted(diffs)[len(diffs) // 2] if diffs else 0


def elims_start_seconds(qual_times: list[int], elim_times: list[int]) -> int | None:
    """First elimination match; else the end of the last qual match (its start + one cycle)."""
    if elim_times:
        return min(elim_times)
    if qual_times:
        return max(qual_times) + _median_cycle(qual_times)
    return None


def _round_times(matches: list[dict[str, Any]]) -> tuple[list[int], list[int], list[int]]:
    """Scheduled start times (epoch seconds): (qualification, elimination, all)."""
    quals: list[int] = []
    elims: list[int] = []
    every: list[int] = []
    for match in matches:
        info = match.get("matchInfo") if isinstance(match, dict) else None
        if not isinstance(info, dict):
            continue
        seconds = _epoch_seconds(info.get("timeScheduled"))
        if seconds is None:
            continue
        every.append(seconds)
        round_name = str((info.get("matchTuple") or {}).get("round", "")).upper()
        if round_name == "QUAL":
            quals.append(seconds)
        elif round_name not in NON_ELIM_ROUNDS:
            elims.append(seconds)
    return quals, elims, every


def elims_start(matches: list[dict[str, Any]], tz: tzinfo) -> datetime | None:
    """When interviews must be over: see `elims_start_seconds`."""
    quals, elims, _ = _round_times(matches)
    seconds = elims_start_seconds(quals, elims)
    return datetime.fromtimestamp(seconds, tz=tz) if seconds is not None else None


def event_day(matches: list[dict[str, Any]], tz: tzinfo) -> date | None:
    """The event's date: that of the first qualification match (else of any match).

    Judging times typed as "9:00 AM" are on this day, not on today's date, so a match
    file exported days ahead (or an old one used for practice) still lines up.
    """
    quals, _, every = _round_times(matches)
    times = quals or every
    return datetime.fromtimestamp(min(times), tz=tz).date() if times else None


def preview_matches(raw_text: str, tz: tzinfo) -> MatchPreview | None:
    """Summary of an uploaded schedule for the setup form; None when the text is blank.

    `first_match` is the earliest scheduled qualification match, else the
    earliest scheduled match of any round. Unparseable text yields `error`.
    """
    text = str(raw_text or "")
    if not text.strip():
        return None
    try:
        matches = parse_matches(text)
    except ValueError as exc:
        return {
            "match_count": 0,
            "team_count": 0,
            "first_match": None,
            "lunch_start": None,
            "lunch_end": None,
            "elims_start": None,
            "error": str(exc),
        }

    teams: set[str] = set()
    for match in matches:
        info = match.get("matchInfo") or {}
        if not isinstance(info, dict):
            continue
        for alliance in info.get("alliances") or []:
            for team in (alliance or {}).get("teams") or []:
                number = str((team or {}).get("number", "")).strip() if isinstance(team, dict) else ""
                if number:
                    teams.add(number)

    qual_times, elim_times, any_times = _round_times(matches)
    first = min(qual_times) if qual_times else (min(any_times) if any_times else None)
    lunch = detect_lunch(qual_times or any_times)
    elims = elims_start_seconds(qual_times, elim_times)

    def iso(seconds: int | None) -> str | None:
        return datetime.fromtimestamp(seconds, tz=tz).isoformat() if seconds is not None else None

    return {
        "match_count": len(matches),
        "team_count": len(teams),
        "first_match": iso(first),
        "lunch_start": iso(lunch[0]) if lunch else None,
        "lunch_end": iso(lunch[1]) if lunch else None,
        "elims_start": iso(elims),
        "error": None,
    }


def format_clock(moment: datetime) -> str:
    """12-hour wall-clock time without a leading zero: "9:05 AM"."""
    hour = moment.hour % 12 or 12
    return f"{hour}:{moment.minute:02d} {'AM' if moment.hour < 12 else 'PM'}"
