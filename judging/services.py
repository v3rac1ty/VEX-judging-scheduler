"""State transitions behind each endpoint.

Every function here mutates a *working copy* of the state handed to it by
`StateStore.update`; raising `ServiceError` aborts the transaction and leaves
the stored state untouched.
"""
from __future__ import annotations

import copy
import random
from datetime import date, datetime, tzinfo
from typing import Any

from .models import (
    GenerateNoShowRequest,
    GenerateRequest,
    ScheduleDict,
    SlotDict,
    StateDict,
    SuggestionDict,
)
from .parsing import (
    TeamMatches,
    extract_team_matches,
    normalize_match_entries,
    parse_matches,
    parse_time,
    serialize_team_matches,
    team_sort_key,
)
from .scheduling import (
    assign_slots_balanced_random,
    build_no_show_suggestion,
    build_slots,
    not_in_schedule_suggestion,
    place_no_shows,
)

NOSHOW_TYPES = frozenset({"noshow", "printed-noshow"})


class ServiceError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _bad_request(detail: str) -> ServiceError:
    return ServiceError(400, detail)


# ---------------------------------------------------------------- schedule helpers


def active_schedule(state: StateDict) -> ScheduleDict | None:
    active_id = state.get("active_schedule_id")
    if not active_id:
        return None
    return next((s for s in state.get("schedules", []) if s.get("id") == active_id), None)


def active_slots(state: StateDict) -> list[SlotDict]:
    schedule = active_schedule(state)
    return schedule.get("slots", []) if schedule else []


def new_schedule_id(state: StateDict, prefix: str) -> str:
    """`<prefix>-YYYYmmdd-HHMMSS`, suffixed if that id already exists."""
    base = f"{prefix}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    taken = {s.get("id") for s in state.get("schedules", [])}
    candidate, n = base, 2
    while candidate in taken:
        candidate, n = f"{base}-{n}", n + 1
    return candidate


def make_schedule(
    schedule_id: str, label: str, schedule_type: str, slots: list[SlotDict]
) -> ScheduleDict:
    return {
        "id": schedule_id,
        "label": label,
        "type": schedule_type,  # type: ignore[typeddict-item]
        "file": f"{schedule_id}.json",
        "slots": slots,
        "created_at": datetime.now().isoformat(),
    }


def upsert_schedule_by_type(
    state: StateDict, schedule_type: str, prefix: str, label: str, slots: list[SlotDict]
) -> str:
    schedules = state.setdefault("schedules", [])
    existing = next((s for s in schedules if s.get("type") == schedule_type), None)
    if existing:
        schedule_id = existing.get("id") or new_schedule_id(state, prefix)
        existing.update(make_schedule(schedule_id, label, schedule_type, slots))
        return schedule_id
    schedule_id = new_schedule_id(state, prefix)
    schedules.append(make_schedule(schedule_id, label, schedule_type, slots))
    return schedule_id


def require_printed_for_actions(state: StateDict) -> None:
    schedule = active_schedule(state)
    if schedule and schedule.get("type") in NOSHOW_TYPES:
        if not state.get("noshow_locked"):
            raise _bad_request("Print the no-show schedule before checking off teams.")
        return
    if not state.get("locked"):
        raise _bad_request("Print the schedule before checking off teams.")


def update_slot_status(state: StateDict, team: str, status: str) -> int | None:
    """Set `status` on the team's slot in the active schedule; return its judge."""
    for slot in active_slots(state):
        if slot.get("team") == team:
            slot["status"] = status  # type: ignore[typeddict-item]
            return slot.get("judge_id")
    return None


def _drop_team_suggestions(state: StateDict, team: str) -> None:
    state["no_shows"] = [t for t in state.get("no_shows", []) if t != team]
    state["no_show_suggestions"] = [
        s for s in state.get("no_show_suggestions", []) if s.get("team") != team
    ]
    if (state.get("last_suggestions") or {}).get("team") == team:
        state.pop("last_suggestions", None)


# ------------------------------------------------------------------------ actions


def generate(
    state: StateDict,
    req: GenerateRequest,
    tz: tzinfo,
    rng: random.Random | None = None,
    today: date | None = None,
) -> None:
    if state.get("locked"):
        raise _bad_request("Schedule is locked after printing.")
    try:
        start_time = parse_time(req.start_time, "judging start time", tz, today)
        end_time = parse_time(req.end_time, "judging end time", tz, today)
    except ValueError as exc:
        raise _bad_request(str(exc)) from exc

    duration_minutes = int((end_time - start_time).total_seconds() / 60)
    if duration_minutes <= 0:
        raise _bad_request("Judging end time must be after the start time.")

    team_matches: TeamMatches
    if req.match_schedule.strip():
        try:
            matches = parse_matches(req.match_schedule)
        except ValueError as exc:
            raise _bad_request(str(exc)) from exc
        team_matches = extract_team_matches(matches, tz)
    else:
        existing = state.get("team_matches") or {}
        if not existing:
            raise _bad_request("Upload match schedule before generating.")
        team_matches = {team: normalize_match_entries(v) for team, v in existing.items()}

    if not team_matches:
        raise _bad_request("No teams found in match schedule.")

    teams = sorted(team_matches, key=team_sort_key)
    slots = build_slots(req.judge_pairs, start_time, end_time, req.slot_minutes)
    slots, unassigned = assign_slots_balanced_random(slots, teams, req.judge_pairs, rng)
    slot_payload = [s.to_dict() for s in slots]

    schedule_id = new_schedule_id({}, "schedule")
    new_state: StateDict = {
        "config": {
            "judge_pairs": req.judge_pairs,
            "slot_minutes": req.slot_minutes,
            "duration_minutes": duration_minutes,
            "block_minutes": req.block_minutes,
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "event_stats": req.event_stats or {},
        },
        "locked": False,
        "noshow_locked": False,
        "team_count": len(teams),
        "final_match_teams": [],
        "not_competing": [],
        "active_schedule_id": schedule_id,
        "schedules": [make_schedule(schedule_id, "Initial schedule", "initial", slot_payload)],
        "unassigned": unassigned,
        "team_matches": serialize_team_matches(team_matches),  # type: ignore[typeddict-item]
        "no_shows": [],
        "no_show_suggestions": [],
    }
    if unassigned:
        suggestions = [
            build_no_show_suggestion(team, normalize_match_entries(team_matches.get(team, [])))
            for team in unassigned
        ]
        new_state["no_show_suggestions"] = suggestions
        new_state["last_suggestions"] = suggestions[-1]

    revision = state.get("revision", 0)
    state.clear()
    state.update(new_state)
    state["revision"] = revision


def checkoff(state: StateDict, team: str) -> None:
    require_printed_for_actions(state)
    if update_slot_status(state, team, "checked") is None:
        raise ServiceError(404, "Team not found.")
    _drop_team_suggestions(state, team)


def noshow(state: StateDict, team: str) -> None:
    require_printed_for_actions(state)
    if update_slot_status(state, team, "no-show") is None:
        raise ServiceError(404, "Team not found.")

    no_shows = state.setdefault("no_shows", [])
    if team not in no_shows:
        no_shows.append(team)
    state["not_competing"] = [t for t in state.get("not_competing", []) if t != team]

    final_match_teams = set(state.get("final_match_teams", []))
    not_in_schedule = bool(final_match_teams and team not in final_match_teams)
    suggestion: SuggestionDict
    if not_in_schedule:
        suggestion = not_in_schedule_suggestion(team)
    else:
        entries = (state.get("team_matches") or {}).get(team, [])
        suggestion = build_no_show_suggestion(team, normalize_match_entries(entries))

    state["no_show_suggestions"] = [
        s for s in state.get("no_show_suggestions", []) if s.get("team") != team
    ] + [suggestion]
    if not not_in_schedule:
        state["last_suggestions"] = suggestion


def mark_not_competing(state: StateDict, team: str) -> None:
    require_printed_for_actions(state)
    _drop_team_suggestions(state, team)
    not_competing = state.setdefault("not_competing", [])
    if team not in not_competing:
        not_competing.append(team)


def set_active_schedule(state: StateDict, schedule_id: str) -> None:
    if not any(s.get("id") == schedule_id for s in state.get("schedules", [])):
        raise ServiceError(404, "Schedule not found.")
    state["active_schedule_id"] = schedule_id


def snapshot_print(state: StateDict, label: str) -> None:
    schedule = active_schedule(state)
    slots = active_slots(state)
    if not slots:
        raise _bad_request("No schedule to snapshot.")

    if schedule and schedule.get("type") == "noshow":
        if state.get("noshow_locked"):
            return
        label = "Printed no-show recovery"
        prefix = schedule_type = "printed-noshow"
        state["noshow_locked"] = True
    else:
        if state.get("locked"):
            return
        prefix = schedule_type = "printed"
        state["locked"] = True

    schedule_id = new_schedule_id(state, prefix)
    state.setdefault("schedules", []).append(
        make_schedule(schedule_id, label, schedule_type, copy.deepcopy(slots))
    )
    state["active_schedule_id"] = schedule_id


def _no_show_base_slots(state: StateDict) -> list[SlotDict]:
    """Judge time already committed, which no-show slots must avoid.

    Normally the active (printed) schedule. When the active schedule is the
    unprinted no-show draft being regenerated, use the latest main schedule
    instead -- otherwise the draft's own slots would block its replacement.
    """
    schedule = active_schedule(state)
    if schedule and schedule.get("type") == "noshow":
        for candidate_type in ("printed", "initial"):
            for candidate in reversed(state.get("schedules", [])):
                if candidate.get("type") == candidate_type:
                    return candidate.get("slots", [])
        return []
    return active_slots(state)


def refresh_no_show_gaps_from_schedule(state: StateDict, raw_schedule: str, tz: tzinfo) -> None:
    """Re-derive gaps from the final match schedule; cross off teams no longer in it."""
    team_matches = extract_team_matches(parse_matches(raw_schedule), tz)
    final_teams = sorted(team_matches, key=team_sort_key)
    final_team_set = set(final_teams)

    existing_no_show_teams = {t for t in state.get("no_shows", []) if isinstance(t, str)}
    existing_suggestion_teams = {
        s.get("team") for s in state.get("no_show_suggestions", []) if isinstance(s.get("team"), str)
    }
    teams_to_cross_off = sorted(
        (existing_no_show_teams | existing_suggestion_teams) - final_team_set,  # type: ignore[operator]
        key=team_sort_key,
    )

    state["final_match_teams"] = final_teams
    state["team_matches"] = serialize_team_matches(team_matches)  # type: ignore[typeddict-item]
    state["no_shows"] = [t for t in state.get("no_shows", []) if t in final_team_set]

    not_competing = {
        t for t in state.get("not_competing", []) if isinstance(t, str) and t in final_team_set
    }
    not_competing.update(teams_to_cross_off)
    state["not_competing"] = sorted(not_competing, key=team_sort_key)

    for team in teams_to_cross_off:
        update_slot_status(state, team, "checked")

    ordered_teams: list[str] = []
    seen: set[str] = set()
    candidates = [
        s.get("team") for s in state.get("no_show_suggestions", []) if s.get("team") in final_team_set
    ] + state["no_shows"]
    for team in candidates:
        if team and team not in seen:
            seen.add(team)
            ordered_teams.append(team)

    refreshed: list[SuggestionDict] = [
        build_no_show_suggestion(team, normalize_match_entries(team_matches.get(team, [])))
        for team in ordered_teams
    ]
    # Crossed-off teams stay visible in the column but are skipped when scheduling.
    refreshed.extend(not_in_schedule_suggestion(team) for team in teams_to_cross_off)

    state["no_show_suggestions"] = refreshed
    in_schedule = [s for s in refreshed if not s.get("not_in_schedule")]
    if in_schedule:
        state["last_suggestions"] = in_schedule[-1]
    else:
        state.pop("last_suggestions", None)


def generate_no_show(
    state: StateDict,
    req: GenerateNoShowRequest,
    tz: tzinfo,
    rng: random.Random | None = None,
) -> None:
    if state.get("noshow_locked"):
        raise _bad_request("No-show schedule is locked after printing.")

    if req.event_stats:
        state.setdefault("config", {})["event_stats"] = req.event_stats
    if req.match_schedule:
        try:
            refresh_no_show_gaps_from_schedule(state, req.match_schedule, tz)
        except ValueError as exc:
            raise _bad_request(str(exc)) from exc

    config: dict[str, Any] = dict(state.get("config") or {})
    not_competing = set(state.get("not_competing", []))
    final_match_teams = set(state.get("final_match_teams", []))
    suggestions = [
        s
        for s in state.get("no_show_suggestions", [])
        if s.get("team")
        and not s.get("not_in_schedule")
        and s.get("team") not in not_competing
        and (not final_match_teams or s.get("team") in final_match_teams)
    ]
    slots, assigned = place_no_shows(
        suggestions,
        _no_show_base_slots(state),
        judge_pairs=int(config.get("judge_pairs", 1)),
        slot_minutes=int(config.get("slot_minutes", 10)),
        block_minutes=int(config.get("block_minutes", 0)),
        rng=rng,
    )
    if not slots:
        raise _bad_request("No gaps available for no-show teams.")

    for suggestion in state.get("no_show_suggestions", []):
        team = suggestion.get("team")
        if team in assigned:
            suggestion["judge_id"] = assigned[team]

    state["active_schedule_id"] = upsert_schedule_by_type(
        state, "noshow", "noshow_schedule", "No-show recovery", slots
    )
