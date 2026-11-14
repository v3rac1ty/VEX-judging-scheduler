from __future__ import annotations

import random
from collections import Counter
from datetime import datetime, timedelta

import pytest

from judging.parsing import extract_team_matches
from judging.scheduling import (
    assign_slots_balanced_random,
    build_no_show_suggestion,
    build_slots,
    compute_gaps_sorted,
    find_slot_in_gap,
    place_no_shows,
    snap_to_five,
    window_end_for,
)
from tm_fixture import TEAMS


def at(base: datetime, hour: int, minute: int = 0) -> datetime:
    return base.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _overlaps(intervals):
    ordered = sorted(intervals)
    return any(a[1] > b[0] for a, b in zip(ordered, ordered[1:]))


def test_snap_to_five(first_match):
    assert snap_to_five(at(first_match, 9, 0)) == at(first_match, 9, 0)
    assert snap_to_five(at(first_match, 9, 3)) == at(first_match, 9, 5)
    assert snap_to_five(at(first_match, 9, 59)) == at(first_match, 10, 0)
    assert snap_to_five(at(first_match, 9, 5).replace(second=30)) == at(first_match, 9, 10)


@pytest.mark.parametrize("slot_minutes", [5, 7, 10, 12])
@pytest.mark.parametrize("start_minute", [0, 3])
def test_build_slots_back_to_back(first_match, slot_minutes, start_minute):
    start = at(first_match, 9, start_minute)
    end = at(first_match, 11, 0)
    slots = build_slots(3, start, end, slot_minutes)
    snapped = snap_to_five(start)
    per_judge = (end - snapped) // timedelta(minutes=slot_minutes)
    assert len(slots) == 3 * per_judge
    for judge_id in (1, 2, 3):
        mine = sorted((s.start, s.end) for s in slots if s.judge_id == judge_id)
        assert mine[0][0] == snapped
        assert mine[0][0].minute % 5 == 0
        assert not _overlaps(mine)
        for (_, prev_end), (next_start, _) in zip(mine, mine[1:]):
            assert prev_end == next_start
        assert all(e - s == timedelta(minutes=slot_minutes) for s, e in mine)
        assert mine[-1][1] <= end


def test_build_slots_snapped_past_end_yields_none(first_match):
    assert build_slots(2, at(first_match, 9, 1), at(first_match, 9, 4), 5) == []


@pytest.mark.parametrize("judge_pairs", [1, 3, 4, 5, 7])
def test_balanced_assignment_places_everyone(first_match, judge_pairs):
    slots = build_slots(judge_pairs, at(first_match, 8), at(first_match, 12), 10)
    slots, unassigned = assign_slots_balanced_random(slots, TEAMS, judge_pairs, random.Random(judge_pairs))
    assert unassigned == []
    placed = [s.team for s in slots if s.team]
    assert sorted(placed) == sorted(TEAMS)
    counts = Counter(s.judge_id for s in slots if s.team)
    assert max(counts.values()) - min(counts.values()) <= 1
    for judge_id in range(1, judge_pairs + 1):
        # Each judge's teams fill its earliest slots first.
        mine = sorted((s for s in slots if s.judge_id == judge_id), key=lambda s: s.start)
        filled = [s.team is not None for s in mine]
        assert filled == sorted(filled, reverse=True)


def test_assignment_does_not_mutate_input_and_is_deterministic(first_match):
    teams = list(TEAMS)
    a = assign_slots_balanced_random(build_slots(4, at(first_match, 8), at(first_match, 11), 10), teams, 4, random.Random(5))
    b = assign_slots_balanced_random(build_slots(4, at(first_match, 8), at(first_match, 11), 10), teams, 4, random.Random(5))
    assert teams == TEAMS
    assert [s.team for s in a[0]] == [s.team for s in b[0]]


def test_unassigned_when_capacity_short(first_match):
    # 2 judges x 6 slots = 12 seats for 24 teams.
    slots = build_slots(2, at(first_match, 9), at(first_match, 10), 10)
    slots, unassigned = assign_slots_balanced_random(slots, TEAMS, 2, random.Random(1))
    assert len(unassigned) == 12
    assigned = {s.team for s in slots if s.team}
    assert len(assigned) == 12
    assert assigned.isdisjoint(unassigned)
    assert assigned | set(unassigned) == set(TEAMS)


def test_gaps_sorted_desc(matches, tz):
    for entries in extract_team_matches(matches, tz).values():
        gaps = compute_gaps_sorted(entries)
        minutes = [g["minutes"] for g in gaps]
        assert minutes == sorted(minutes, reverse=True)
        assert all(m > 0 for m in minutes)
        assert all(g["end"] > g["start"] for g in gaps)


def test_find_slot_in_gap_skips_busy(first_match):
    busy = [(at(first_match, 12, 0), at(first_match, 12, 10)), (at(first_match, 12, 15), at(first_match, 12, 30))]
    found = find_slot_in_gap(at(first_match, 12, 0), at(first_match, 13, 0), 10, busy)
    assert found == (at(first_match, 12, 30), at(first_match, 12, 40))
    assert find_slot_in_gap(at(first_match, 12, 0), at(first_match, 12, 25), 10, busy) is None


@pytest.mark.parametrize("block_minutes", [0, 8, 10])
def test_no_show_placement_inside_damped_gaps(matches, tz, first_match, block_minutes):
    team_matches = extract_team_matches(matches, tz)
    no_show_teams = TEAMS[:10]
    suggestions = [build_no_show_suggestion(t, team_matches[t]) for t in no_show_teams]
    # Judges already busy for part of the match window.
    base = [
        {"judge_id": j, "start": (first_match + timedelta(minutes=10 * i)).isoformat(),
         "end": (first_match + timedelta(minutes=10 * i + 10)).isoformat(), "team": None, "status": "scheduled"}
        for j in (1, 2) for i in range(0, 12, 2)
    ]
    slots, assigned = place_no_shows(suggestions, base, judge_pairs=3, slot_minutes=10,
                                     block_minutes=block_minutes, rng=random.Random(3))
    assert slots, "expected at least one placement"
    damp = timedelta(minutes=block_minutes / 2)
    by_team = {s["team"]: s for s in suggestions}
    per_judge: dict[int, list] = {}
    for slot in base:
        per_judge.setdefault(slot["judge_id"], []).append(
            (datetime.fromisoformat(slot["start"]), datetime.fromisoformat(slot["end"])))
    for slot in slots:
        start, end = datetime.fromisoformat(slot["start"]), datetime.fromisoformat(slot["end"])
        assert end - start == timedelta(minutes=10)
        assert slot["status"] == "rescheduled"
        assert assigned[slot["team"]] == slot["judge_id"]
        gap = next(g for g in by_team[slot["team"]]["gaps"] if g["between"] == slot["between"])
        assert datetime.fromisoformat(gap["start"]) + damp <= start
        assert end <= datetime.fromisoformat(gap["end"]) - damp
        per_judge.setdefault(slot["judge_id"], []).append((start, end))
    for intervals in per_judge.values():
        assert not _overlaps(intervals)


def test_no_show_placement_without_suggestions():
    assert place_no_shows([], [], 2, 10, 8) == ([], {})


def test_build_slots_skip_lunch(first_match):
    start, end = at(first_match, 9), at(first_match, 11)
    lunch = [(at(first_match, 9, 25), at(first_match, 10))]
    slots = build_slots(2, start, end, 10, lunch)
    starts = sorted({s.start for s in slots if s.judge_id == 1})
    # 9:00 and 9:10 fit; 9:20 would run into lunch, so the next one starts at 10:00.
    assert starts == [at(first_match, 9), at(first_match, 9, 10)] + [at(first_match, 10, m) for m in range(0, 60, 10)]
    assert not any(s.start < lunch[0][1] and s.end > lunch[0][0] for s in slots)


def test_window_end_for_matches_build_slots(first_match):
    lunch = [(at(first_match, 9, 25), at(first_match, 10))]
    end = window_end_for(at(first_match, 9), 6, 10, lunch)
    assert end == at(first_match, 10, 40)
    assert len(build_slots(1, at(first_match, 9), end, 10, lunch)) == 6
    assert window_end_for(at(first_match, 9), 6, 10) == at(first_match, 10)


def test_no_show_placement_avoids_lunch(matches, tz, first_match):
    team_matches = extract_team_matches(matches, tz)
    team = next(iter(team_matches))
    suggestion = build_no_show_suggestion(team, team_matches[team])
    everything = [(at(first_match, 0), at(first_match, 23, 59))]
    slots, _ = place_no_shows([suggestion], [], judge_pairs=2, slot_minutes=5, block_minutes=0, breaks=everything)
    assert slots == []
