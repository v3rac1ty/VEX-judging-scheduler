"""Pure scheduling functions: slots, balanced assignment, gaps, no-show placement.

Randomness is injectable (`rng`) so tests can be deterministic.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable

from .models import GapDict, SlotDict, SuggestionDict
from .parsing import MatchEntry

_DEFAULT_RNG = random.Random()

Interval = tuple[datetime, datetime]


@dataclass
class Slot:
    judge_id: int
    start: datetime
    end: datetime
    team: str | None = None
    status: str = "scheduled"

    def to_dict(self) -> SlotDict:
        return {
            "judge_id": self.judge_id,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "team": self.team,
            "status": self.status,  # type: ignore[typeddict-item]
        }


# ------------------------------------------------------------------ initial slots


def snap_to_five(value: datetime) -> datetime:
    """Round up to the next 5-minute boundary (no-op if already on one)."""
    floored = value.replace(second=0, microsecond=0)
    if floored == value and value.minute % 5 == 0:
        return value
    minutes_to_add = 5 - (floored.minute % 5)
    return floored + timedelta(minutes=minutes_to_add)


def build_slots(
    judge_pairs: int,
    start_time: datetime,
    end_time: datetime,
    slot_minutes: int,
) -> list[Slot]:
    """Back-to-back slots per judge from the snapped start; last one ends <= end_time.

    Only the first start is snapped to 5 minutes; later slots follow directly so
    slot lengths that are not multiples of 5 never overlap.
    """
    if judge_pairs < 1 or slot_minutes < 1:
        raise ValueError("judge_pairs and slot_minutes must be positive.")
    first_start = snap_to_five(start_time)
    slot_delta = timedelta(minutes=slot_minutes)
    available = end_time - first_start
    total_slots_per_judge = max(0, available // slot_delta) if available > timedelta(0) else 0
    slots: list[Slot] = []
    for judge_id in range(1, judge_pairs + 1):
        for i in range(total_slots_per_judge):
            slot_start = first_start + i * slot_delta
            slots.append(Slot(judge_id=judge_id, start=slot_start, end=slot_start + slot_delta))
    return slots


def assign_slots_balanced_random(
    slots: list[Slot],
    teams: Iterable[str],
    judge_pairs: int,
    rng: random.Random | None = None,
) -> tuple[list[Slot], list[str]]:
    """Give each judge an equal share (+/-1) of teams, filling each judge's slots in order.

    Returns (slots, unassigned). `slots` is mutated in place.
    """
    rng = rng or _DEFAULT_RNG
    team_list = list(teams)
    unassigned: list[str] = []
    if not team_list:
        return slots, unassigned

    judge_ids = list(range(1, judge_pairs + 1))
    base, remainder = divmod(len(team_list), judge_pairs)
    extra_judges = set(rng.sample(judge_ids, remainder)) if remainder else set()

    assignments: list[int] = []
    for judge_id in judge_ids:
        assignments.extend([judge_id] * (base + (1 if judge_id in extra_judges else 0)))

    rng.shuffle(assignments)
    rng.shuffle(team_list)

    judge_slots: dict[int, list[Slot]] = {jid: [] for jid in judge_ids}
    for slot in sorted(slots, key=lambda s: (s.judge_id, s.start)):
        judge_slots.setdefault(slot.judge_id, []).append(slot)

    next_index = {jid: 0 for jid in judge_ids}
    for team, judge_id in zip(team_list, assignments):
        slot_list = judge_slots[judge_id]
        index = next_index[judge_id]
        if index >= len(slot_list):
            unassigned.append(team)
            continue
        slot_list[index].team = team
        next_index[judge_id] += 1

    return slots, unassigned


# ------------------------------------------------------------------------- gaps


def compute_gaps_sorted(match_entries: list[MatchEntry]) -> list[dict[str, Any]]:
    """Gaps between consecutive matches, largest first (ties stay chronological)."""
    gaps: list[dict[str, Any]] = []
    for current, next_entry in zip(match_entries, match_entries[1:]):
        start, end = current["time"], next_entry["time"]
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


def build_no_show_suggestion(team: str, team_matches: list[MatchEntry]) -> SuggestionDict:
    gaps: list[GapDict] = [
        {
            "start": g["start"].isoformat(),
            "end": g["end"].isoformat(),
            "minutes": g["minutes"],
            "between": g["between"],
        }
        for g in compute_gaps_sorted(team_matches)
    ]
    return {"team": team, "judge_id": None, "gaps": gaps}


def not_in_schedule_suggestion(team: str) -> SuggestionDict:
    return {"team": team, "judge_id": None, "gaps": [], "not_in_schedule": True}


# ----------------------------------------------------------- no-show placement


def collect_judge_intervals(slots: Iterable[SlotDict]) -> dict[int, list[Interval]]:
    by_judge: dict[int, list[Interval]] = {}
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


def find_slot_in_gap(
    gap_start: datetime,
    gap_end: datetime,
    slot_minutes: int,
    intervals: list[Interval],
) -> Interval | None:
    """Earliest [start, start+slot) inside the gap that overlaps no interval."""
    start = gap_start
    slot_delta = timedelta(minutes=slot_minutes)
    index = 0
    while start + slot_delta <= gap_end:
        end = start + slot_delta
        while index < len(intervals) and intervals[index][1] <= start:
            index += 1
        if index >= len(intervals) or intervals[index][0] >= end:
            return start, end
        start = intervals[index][1]
    return None


def find_best_slot_for_judge(
    gaps: list[GapDict],
    slot_minutes: int,
    intervals: list[Interval],
    damp_delta: timedelta,
) -> tuple[datetime, datetime, GapDict] | None:
    """Earliest fit across all gaps, each gap shrunk by `damp_delta` on both ends."""
    best: tuple[datetime, datetime, GapDict] | None = None
    for gap in gaps:
        gap_start = datetime.fromisoformat(gap["start"]) + damp_delta
        gap_end = datetime.fromisoformat(gap["end"]) - damp_delta
        if gap_end <= gap_start:
            continue
        candidate = find_slot_in_gap(gap_start, gap_end, slot_minutes, intervals)
        if candidate and (best is None or candidate[0] < best[0]):
            best = (candidate[0], candidate[1], gap)
    return best


def place_no_shows(
    suggestions: list[SuggestionDict],
    base_slots: Iterable[SlotDict],
    judge_pairs: int,
    slot_minutes: int,
    block_minutes: int,
    rng: random.Random | None = None,
) -> tuple[list[SlotDict], dict[str, int]]:
    """Place each suggested team into the earliest free slot inside one of its gaps.

    Judges are kept roughly balanced; if no under-target judge fits, any judge may
    take the team. Returns (no-show slots, team -> judge_id).
    """
    rng = rng or _DEFAULT_RNG
    teams = [s["team"] for s in suggestions if s.get("team")]
    if not teams:
        return [], {}

    damp_delta = timedelta(minutes=block_minutes / 2) if block_minutes > 0 else timedelta(0)
    existing_by_judge = collect_judge_intervals(base_slots)

    judge_ids = list(range(1, judge_pairs + 1))
    target_per_judge = max(1, len(teams) // judge_pairs)
    remainder = len(teams) % judge_pairs
    extra_judges = set(rng.sample(judge_ids, remainder)) if remainder else set()
    judge_targets = {
        jid: target_per_judge + (1 if jid in extra_judges else 0) for jid in judge_ids
    }
    judge_counts = {jid: 0 for jid in judge_ids}

    def best_for(candidates: list[int], gaps: list[GapDict]) -> tuple[int, tuple[datetime, datetime, GapDict]] | None:
        best: tuple[int, tuple[datetime, datetime, GapDict]] | None = None
        for jid in candidates:
            found = find_best_slot_for_judge(
                gaps, slot_minutes, existing_by_judge.setdefault(jid, []), damp_delta
            )
            if found and (best is None or found[0] < best[1][0]):
                best = (jid, found)
        return best

    slots: list[SlotDict] = []
    assigned: dict[str, int] = {}
    for suggestion in suggestions:
        team = suggestion.get("team")
        gaps = suggestion.get("gaps") or []
        if not team or not gaps:
            continue
        under_target = [jid for jid in judge_ids if judge_counts[jid] < judge_targets[jid]]
        chosen = best_for(under_target, gaps) or best_for(judge_ids, gaps)
        if not chosen:
            continue
        judge_id, (start, end, gap) = chosen
        intervals = existing_by_judge[judge_id]
        intervals.append((start, end))
        intervals.sort()
        judge_counts[judge_id] += 1
        assigned[team] = judge_id
        slots.append(
            {
                "judge_id": judge_id,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "team": team,
                "status": "rescheduled",
                "between": gap.get("between"),
            }
        )
    return slots, assigned
