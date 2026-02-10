from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
STATE_PATH = DATA_DIR / "state.json"
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="VEX Judging Scheduler")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@dataclass
class Slot:
    judge_id: int
    start: datetime
    end: datetime
    team: str | None = None
    status: str = "scheduled"


def _load_state() -> Dict[str, Any]:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {}


def _save_state(state: Dict[str, Any]) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2))


def _schedule_id(prefix: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{stamp}"


def _save_schedule_file(schedule_id: str, slots: List[Dict[str, Any]]) -> str:
    filename = f"{schedule_id}.json"
    path = DATA_DIR / filename
    path.write_text(json.dumps({"id": schedule_id, "slots": slots}, indent=2))
    return filename


def _parse_matches(raw_text: str) -> List[Dict[str, Any]]:
    try:
        parsed = json.loads(raw_text)
        if isinstance(parsed, dict) and "Matches" in parsed:
            return parsed["Matches"]
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        pass

    # Extract the first JSON array if the text includes extra log lines.
    match = re.search(r"\[(.*)\]", raw_text, re.DOTALL)
    if not match:
        raise ValueError("Could not find JSON array in match schedule input.")
    array_text = "[" + match.group(1) + "]"
    return json.loads(array_text)


def _match_label(match_info: Dict[str, Any]) -> str:
    match_tuple = match_info.get("matchTuple", {})
    round_name = str(match_tuple.get("round", "")).upper()
    match_num = match_tuple.get("match")
    if round_name and match_num is not None:
        if round_name == "QUAL":
            return f"Q{match_num}"
        return f"{round_name}{match_num}"
    if match_num is not None:
        return f"Match {match_num}"
    return "Match"


def _extract_team_matches(matches: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    team_matches: Dict[str, List[Dict[str, Any]]] = {}
    for match in matches:
        info = match.get("matchInfo", {})
        time_scheduled = info.get("timeScheduled")
        if time_scheduled is None:
            continue
        match_time = datetime.fromtimestamp(int(time_scheduled), tz=timezone.utc)
        label = _match_label(info)
        alliances = info.get("alliances", [])
        for alliance in alliances:
            for team in alliance.get("teams", []):
                team_num = str(team.get("number", "")).strip()
                if not team_num:
                    continue
                team_matches.setdefault(team_num, []).append(
                    {"time": match_time, "label": label}
                )
    for matches_list in team_matches.values():
        matches_list.sort(key=lambda m: m["time"])
    return team_matches


def _build_slots(
    judge_pairs: int,
    start_time: datetime,
    duration_minutes: int,
    slot_minutes: int,
) -> List[Slot]:
    slots: List[Slot] = []
    total_slots_per_judge = duration_minutes // slot_minutes
    for judge_id in range(1, judge_pairs + 1):
        for i in range(total_slots_per_judge):
            slot_start = start_time + timedelta(minutes=i * slot_minutes)
            slot_end = slot_start + timedelta(minutes=slot_minutes)
            slots.append(Slot(judge_id=judge_id, start=slot_start, end=slot_end))
    return slots


def _parse_time(raw_time: str, label: str) -> datetime:
    time_text = raw_time.strip()
    if not time_text:
        raise ValueError(f"Missing {label}.")

    today = datetime.now().astimezone()
    tzinfo = today.tzinfo

    match = re.match(r"^(\d{1,2}):(\d{2})\s*([AaPp][Mm])$", time_text)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))
        meridiem = match.group(3).lower()
        if hour < 1 or hour > 12 or minute > 59:
            raise ValueError(f"Invalid {label}.")
        if meridiem == "pm" and hour != 12:
            hour += 12
        if meridiem == "am" and hour == 12:
            hour = 0
        return datetime(
            year=today.year,
            month=today.month,
            day=today.day,
            hour=hour,
            minute=minute,
            tzinfo=tzinfo,
        )

    try:
        parsed = datetime.strptime(time_text, "%H:%M")
        return datetime(
            year=today.year,
            month=today.month,
            day=today.day,
            hour=parsed.hour,
            minute=parsed.minute,
            tzinfo=tzinfo,
        )
    except ValueError as exc:
        raise ValueError(f"{label.capitalize()} must be like 9:00 AM.") from exc


def _team_sort_key(team: str) -> Tuple[int, str]:
    try:
        return (0, f"{int(team):06d}")
    except ValueError:
        return (1, team)


def _assign_slots_balanced_random(
    slots: List[Slot],
    teams: List[str],
    judge_pairs: int,
) -> Tuple[List[Slot], List[str]]:
    unassigned: List[str] = []
    if not teams:
        return slots, unassigned

    judge_ids = list(range(1, judge_pairs + 1))
    base = len(teams) // judge_pairs
    remainder = len(teams) % judge_pairs
    extra_judges = set(random.sample(judge_ids, remainder)) if remainder else set()

    assignments: List[int] = []
    for judge_id in judge_ids:
        target = base + (1 if judge_id in extra_judges else 0)
        assignments.extend([judge_id] * target)

    random.shuffle(assignments)
    random.shuffle(teams)

    judge_slots: Dict[int, List[Slot]] = {jid: [] for jid in judge_ids}
    for slot in sorted(slots, key=lambda s: (s.judge_id, s.start)):
        judge_slots[slot.judge_id].append(slot)

    judge_indices = {jid: 0 for jid in judge_ids}
    for team, judge_id in zip(teams, assignments):
        slot_list = judge_slots.get(judge_id, [])
        index = judge_indices[judge_id]
        if index >= len(slot_list):
            unassigned.append(team)
            continue
        slot_list[index].team = team
        judge_indices[judge_id] += 1

    if len(teams) > len(assignments):
        unassigned.extend(teams[len(assignments):])

    return slots, unassigned


def _build_schedule_version(
    schedule_id: str,
    label: str,
    schedule_type: str,
    slots: List[Dict[str, Any]],
) -> Dict[str, Any]:
    schedule_file = _save_schedule_file(schedule_id, slots)
    return {
        "id": schedule_id,
        "label": label,
        "type": schedule_type,
        "file": schedule_file,
        "slots": slots,
        "created_at": datetime.now().isoformat(),
    }


def _get_active_schedule(state: Dict[str, Any]) -> Dict[str, Any] | None:
    active_id = state.get("active_schedule_id")
    if not active_id:
        return None
    return next(
        (schedule for schedule in state.get("schedules", []) if schedule.get("id") == active_id),
        None,
    )


def _set_active_schedule(state: Dict[str, Any], schedule_id: str, slots: List[Dict[str, Any]]) -> None:
    state["active_schedule_id"] = schedule_id
    state["slots"] = slots


def _upsert_schedule_by_type(
    state: Dict[str, Any],
    schedule_type: str,
    schedule_id: str,
    label: str,
    slots: List[Dict[str, Any]],
) -> str:
    schedules = state.setdefault("schedules", [])
    existing = next((s for s in schedules if s.get("type") == schedule_type), None)
    if existing:
        existing_id = existing.get("id") or schedule_id
        existing["id"] = existing_id
        existing["label"] = label
        existing["type"] = schedule_type
        existing["slots"] = slots
        existing["file"] = _save_schedule_file(existing_id, slots)
        existing["created_at"] = datetime.now().isoformat()
        return existing_id

    schedule_version = _build_schedule_version(
        schedule_id,
        label,
        schedule_type,
        slots,
    )
    schedules.append(schedule_version)
    return schedule_id


def _update_slot_status(state: Dict[str, Any], team: str, status: str) -> int | None:
    judge_id = None
    for slot in state.get("slots", []):
        if slot.get("team") == team:
            slot["status"] = status
            judge_id = slot.get("judge_id")
            break

    active_id = state.get("active_schedule_id")
    if active_id:
        for schedule in state.get("schedules", []):
            if schedule.get("id") == active_id:
                for version_slot in schedule.get("slots", []):
                    if version_slot.get("team") == team:
                        version_slot["status"] = status
                        break
    return judge_id


def _compute_gaps_sorted(
    match_entries: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    gaps: List[Dict[str, Any]] = []
    for idx in range(len(match_entries) - 1):
        current = match_entries[idx]
        next_entry = match_entries[idx + 1]
        start = current["time"]
        end = next_entry["time"]
        gap_minutes = int((end - start).total_seconds() / 60)
        if gap_minutes <= 0:
            continue
        gaps.append(
            {
                "start": start,
                "end": end,
                "minutes": gap_minutes,
                "between": f"{current['label']} and {next_entry['label']}",
            }
        )
    gaps.sort(key=lambda g: g["minutes"], reverse=True)
    return gaps


def _build_no_show_suggestion(
    team: str,
    team_matches: List[Dict[str, Any]],
) -> Dict[str, Any]:
    gaps = _compute_gaps_sorted(team_matches)
    return {
        "team": team,
        "judge_id": None,
        "gaps": [
            {
                "start": g["start"].isoformat(),
                "end": g["end"].isoformat(),
                "minutes": g["minutes"],
                "between": g["between"],
            }
            for g in gaps
        ],
    }


def _normalize_match_entries(match_entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for entry in match_entries:
        time_value = entry.get("time")
        if isinstance(time_value, datetime):
            match_time = time_value
        else:
            match_time = datetime.fromisoformat(str(time_value))
        normalized.append({"time": match_time, "label": entry.get("label", "Match")})
    normalized.sort(key=lambda m: m["time"])
    return normalized


def _collect_judge_intervals(slots: List[Dict[str, Any]]) -> Dict[int, List[Tuple[datetime, datetime]]]:
    by_judge: Dict[int, List[Tuple[datetime, datetime]]] = {}
    for slot in slots:
        judge_id = slot.get("judge_id")
        if not judge_id:
            continue
        start = datetime.fromisoformat(slot["start"])
        end = datetime.fromisoformat(slot["end"])
        by_judge.setdefault(int(judge_id), []).append((start, end))
    for intervals in by_judge.values():
        intervals.sort()
    return by_judge


def _find_slot_in_gap(
    gap_start: datetime,
    gap_end: datetime,
    slot_minutes: int,
    intervals: List[Tuple[datetime, datetime]],
) -> Tuple[datetime, datetime] | None:
    start = gap_start
    slot_delta = timedelta(minutes=slot_minutes)
    while start + slot_delta <= gap_end:
        end = start + slot_delta
        conflict = next(
            (interval for interval in intervals if start < interval[1] and end > interval[0]),
            None,
        )
        if not conflict:
            return start, end
        start = conflict[1]
    return None


def _find_best_slot_for_judge(
    gaps: List[Dict[str, Any]],
    slot_minutes: int,
    intervals: List[Tuple[datetime, datetime]],
    damp_delta: timedelta,
) -> Tuple[datetime, datetime, Dict[str, Any]] | None:
    best: Tuple[datetime, datetime, Dict[str, Any]] | None = None
    for gap in gaps:
        gap_start = datetime.fromisoformat(gap["start"]) + damp_delta
        gap_end = datetime.fromisoformat(gap["end"]) - damp_delta
        if gap_end <= gap_start:
            continue
        candidate = _find_slot_in_gap(gap_start, gap_end, slot_minutes, intervals)
        if not candidate:
            continue
        if best is None or candidate[0] < best[0]:
            best = (candidate[0], candidate[1], gap)
    return best


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/state")
def get_state() -> Dict[str, Any]:
    return _load_state()


@app.post("/api/generate")
def generate(payload: Dict[str, Any]) -> Dict[str, Any]:
    existing_state = _load_state()
    if existing_state.get("locked"):
        raise HTTPException(status_code=400, detail="Schedule is locked after printing.")
    try:
        judge_pairs = int(payload.get("judge_pairs", 4))
        slot_minutes = int(payload.get("slot_minutes", 10))
        block_minutes = int(payload.get("block_minutes", 8))
        start_time = _parse_time(payload.get("start_time", ""), "judging start time")
        end_time = _parse_time(payload.get("end_time", ""), "judging end time")
        raw_schedule = payload.get("match_schedule", "")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    duration_minutes = int((end_time - start_time).total_seconds() / 60)
    if duration_minutes <= 0:
        raise HTTPException(
            status_code=400,
            detail="Judging end time must be after the start time.",
        )

    try:
        matches = _parse_matches(raw_schedule)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    team_matches = _extract_team_matches(matches)
    teams = sorted(team_matches.keys(), key=_team_sort_key)
    slots = _build_slots(judge_pairs, start_time, duration_minutes, slot_minutes)
    slots, unassigned = _assign_slots_balanced_random(slots, teams, judge_pairs)
    slot_payload = [
        {
            "judge_id": s.judge_id,
            "start": s.start.isoformat(),
            "end": s.end.isoformat(),
            "team": s.team,
            "status": s.status,
        }
        for s in slots
    ]

    schedule_id = _schedule_id("schedule")
    schedule_version = _build_schedule_version(
        schedule_id,
        "Initial schedule",
        "initial",
        slot_payload,
    )

    state = {
        "config": {
            "judge_pairs": judge_pairs,
            "slot_minutes": slot_minutes,
            "duration_minutes": duration_minutes,
            "block_minutes": block_minutes,
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
        },
        "locked": False,
        "noshow_locked": False,
        "team_count": len(teams),
        "slots": slot_payload,
        "active_schedule_id": schedule_id,
        "schedules": [schedule_version],
        "unassigned": unassigned,
        "team_matches": {
            k: [{"time": m["time"].isoformat(), "label": m["label"]} for m in v]
            for k, v in team_matches.items()
        },
        "no_shows": [],
        "no_show_suggestions": [],
    }

    if unassigned:
        no_show_suggestions: List[Dict[str, Any]] = []
        for team in unassigned:
            matches = _normalize_match_entries(team_matches.get(team, []))
            if not matches:
                continue
            no_show_suggestions.append(_build_no_show_suggestion(team, matches))
        state["no_shows"] = unassigned
        state["no_show_suggestions"] = no_show_suggestions

    _save_state(state)
    return state


@app.post("/api/checkoff")
def checkoff(payload: Dict[str, Any]) -> Dict[str, Any]:
    team = str(payload.get("team", "")).strip()
    if not team:
        raise HTTPException(status_code=400, detail="Missing team.")
    state = _load_state()
    judge_id = _update_slot_status(state, team, "checked")
    if judge_id is not None:
        state["no_shows"] = [t for t in state.get("no_shows", []) if t != team]
        state["no_show_suggestions"] = [
            s for s in state.get("no_show_suggestions", []) if s.get("team") != team
        ]
        if state.get("last_suggestions", {}).get("team") == team:
            state.pop("last_suggestions", None)
        _save_state(state)
        return state
    raise HTTPException(status_code=404, detail="Team not found in slots.")


@app.post("/api/noshow")
def no_show(payload: Dict[str, Any]) -> Dict[str, Any]:
    team = str(payload.get("team", "")).strip()
    if not team:
        raise HTTPException(status_code=400, detail="Missing team.")

    state = _load_state()
    _update_slot_status(state, team, "no-show")
    state.setdefault("no_shows", []).append(team)

    team_matches = state.get("team_matches", {}).get(team, [])
    if not team_matches:
        team_times = state.get("team_times", {}).get(team, [])
        team_matches = [{"time": datetime.fromisoformat(t), "label": "Match"} for t in team_times]
    else:
        team_matches = _normalize_match_entries(team_matches)

    suggestion = _build_no_show_suggestion(team, team_matches)

    suggestions = state.setdefault("no_show_suggestions", [])
    state["no_show_suggestions"] = [s for s in suggestions if s.get("team") != team]
    state["no_show_suggestions"].append(suggestion)
    state["last_suggestions"] = suggestion

    _save_state(state)
    return state


@app.post("/api/active-schedule")
def set_active_schedule(payload: Dict[str, Any]) -> Dict[str, Any]:
    schedule_id = str(payload.get("schedule_id", "")).strip()
    if not schedule_id:
        raise HTTPException(status_code=400, detail="Missing schedule id.")
    state = _load_state()
    schedule = next(
        (s for s in state.get("schedules", []) if s.get("id") == schedule_id), None
    )
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found.")
    _set_active_schedule(state, schedule_id, schedule.get("slots", []))
    _save_state(state)
    return state


@app.post("/api/snapshot-print")
def snapshot_print(payload: Dict[str, Any]) -> Dict[str, Any]:
    state = _load_state()
    label = str(payload.get("label", "Printed schedule")).strip() or "Printed schedule"
    slots = state.get("slots", [])
    if not slots:
        raise HTTPException(status_code=400, detail="No schedule to snapshot.")

    active_schedule = _get_active_schedule(state)
    if active_schedule and active_schedule.get("type") == "noshow":
        if state.get("noshow_locked"):
            return state
        label = "Printed no-show recovery"
        schedule_id = _schedule_id("printed-noshow")
        schedule_type = "printed-noshow"
        state["noshow_locked"] = True
    else:
        if state.get("locked"):
            return state
        schedule_id = _schedule_id("printed")
        schedule_type = "printed"
        state["locked"] = True
    schedule_version = _build_schedule_version(
        schedule_id,
        label,
        schedule_type,
        slots,
    )
    state.setdefault("schedules", []).append(schedule_version)
    _set_active_schedule(state, schedule_id, slots)
    _save_state(state)
    return state


@app.post("/api/reset")
def reset_all() -> Dict[str, Any]:
    if STATE_PATH.exists():
        STATE_PATH.unlink()
    for path in DATA_DIR.glob("*.json"):
        path.unlink(missing_ok=True)
    return {}


@app.post("/api/generate-noshow")
def generate_no_show_schedule(payload: Dict[str, Any]) -> Dict[str, Any]:
    state = _load_state()
    if state.get("noshow_locked"):
        raise HTTPException(status_code=400, detail="No-show schedule is locked after printing.")
    config = state.get("config", {})
    slot_minutes = int(config.get("slot_minutes", 10))
    judge_pairs = int(config.get("judge_pairs", 1))
    block_minutes = int(config.get("block_minutes", 0))
    damp_delta = timedelta(minutes=block_minutes / 2) if block_minutes > 0 else timedelta(0)

    active_schedule = _get_active_schedule(state)
    base_slots = active_schedule.get("slots", []) if active_schedule else state.get("slots", [])
    existing_by_judge = _collect_judge_intervals(base_slots)

    no_show_teams = [s.get("team") for s in state.get("no_show_suggestions", []) if s.get("team")]
    if not no_show_teams:
        raise HTTPException(status_code=400, detail="No no-show teams to schedule.")

    slots: List[Dict[str, Any]] = []
    judge_ids = list(range(1, judge_pairs + 1))
    target_per_judge = max(1, len(no_show_teams) // judge_pairs)
    remainder = len(no_show_teams) % judge_pairs
    extra_judges = set(random.sample(judge_ids, remainder)) if remainder else set()
    judge_targets = {
        judge_id: target_per_judge + (1 if judge_id in extra_judges else 0)
        for judge_id in judge_ids
    }
    judge_counts = {judge_id: 0 for judge_id in judge_ids}
    for suggestion in state.get("no_show_suggestions", []):
        team = suggestion.get("team")
        gaps = suggestion.get("gaps", [])
        if not team or not gaps:
            continue
        best_overall: Tuple[int, Tuple[datetime, datetime, Dict[str, Any]]] | None = None
        for judge_id in judge_ids:
            if judge_counts[judge_id] >= judge_targets[judge_id]:
                continue
            intervals = existing_by_judge.setdefault(judge_id, [])
            candidate = _find_best_slot_for_judge(gaps, slot_minutes, intervals, damp_delta)
            if not candidate:
                continue
            if best_overall is None or candidate[0] < best_overall[1][0]:
                best_overall = (judge_id, candidate)

        if not best_overall:
            for judge_id in judge_ids:
                intervals = existing_by_judge.setdefault(judge_id, [])
                candidate = _find_best_slot_for_judge(gaps, slot_minutes, intervals, damp_delta)
                if not candidate:
                    continue
                if best_overall is None or candidate[0] < best_overall[1][0]:
                    best_overall = (judge_id, candidate)

        if not best_overall:
            continue

        chosen_judge = best_overall[0]
        chosen_slot = best_overall[1]

        intervals = existing_by_judge.setdefault(chosen_judge, [])
        intervals.append((chosen_slot[0], chosen_slot[1]))
        intervals.sort()
        judge_counts[chosen_judge] += 1
        slots.append(
            {
                "judge_id": chosen_judge,
                "start": chosen_slot[0].isoformat(),
                "end": chosen_slot[1].isoformat(),
                "team": team,
                "status": "rescheduled",
                "between": chosen_slot[2].get("between"),
            }
        )

    if not slots:
        raise HTTPException(status_code=400, detail="No gaps available for no-show teams.")

    schedule_id = _upsert_schedule_by_type(
        state,
        "noshow",
        _schedule_id("noshow_schedule"),
        "No-show recovery",
        slots,
    )
    _set_active_schedule(state, schedule_id, slots)
    _save_state(state)
    return state
