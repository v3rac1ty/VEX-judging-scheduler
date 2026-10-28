from __future__ import annotations

import json
from datetime import date

import pytest

from judging.parsing import (
    extract_team_matches,
    match_label,
    parse_matches,
    parse_time,
    strip_log_preamble,
    team_sort_key,
)
from tm_fixture import TEAMS, as_log


def test_bare_array(matches):
    assert parse_matches(json.dumps(matches)) == matches


def test_matches_object(matches):
    assert parse_matches(json.dumps({"Matches": matches})) == matches


def test_log_with_preamble_and_trailing_lines(matches):
    assert parse_matches(as_log(matches)) == matches


def test_log_marker_followed_by_object(matches):
    text = "[INFO] start\n>> Matches: " + json.dumps({"Matches": matches}) + "\nbye [ok]"
    assert parse_matches(text) == matches


def test_array_embedded_in_log_without_marker(matches):
    text = "[INFO] fetching [1/1]\n" + json.dumps(matches) + "\n[INFO] done"
    assert parse_matches(text) == matches


def test_strip_log_preamble_mirrors_frontend():
    assert strip_log_preamble("noise\n>> Fetched matches:\n[1]") == "[1]"
    assert strip_log_preamble(">> Matches: {\n  [") == "{\n  ["
    assert strip_log_preamble("no marker [1]") == "no marker [1]"


@pytest.mark.parametrize("garbage", ["", "   ", "hello world", "[not json", '{"foo": 1}', "[1, 2, 3]"])
def test_garbage_raises(garbage):
    with pytest.raises(ValueError, match="Could not find JSON array"):
        parse_matches(garbage)


def test_empty_array_is_valid_but_teamless():
    assert parse_matches("[]") == []


def test_extract_team_matches(matches, tz):
    team_matches = extract_team_matches(matches, tz)
    assert sorted(team_matches) == sorted(TEAMS)
    for entries in team_matches.values():
        assert len(entries) == 6
        times = [e["time"] for e in entries]
        assert times == sorted(times)
        assert all(t.tzinfo is tz for t in times)
        assert all(e["label"].startswith("Q") for e in entries)


def test_extract_skips_unscheduled_and_bad_rows(matches, tz):
    extra = [
        {"matchInfo": {"timeScheduled": 0, "matchTuple": {"round": "QF", "match": 1},
                       "alliances": [{"teams": [{"number": "9999Z"}]}]}},
        {"matchInfo": {"timeScheduled": None}},
        {"matchInfo": {}},
        {},
    ]
    team_matches = extract_team_matches(matches + extra, tz)
    assert "9999Z" not in team_matches
    assert len(team_matches) == len(TEAMS)


def test_match_labels():
    assert match_label({"matchTuple": {"round": "QUAL", "match": 12}}) == "Q12"
    assert match_label({"matchTuple": {"round": "sf", "match": 2}}) == "SF2"
    assert match_label({"matchTuple": {"match": 3}}) == "Match 3"
    assert match_label({}) == "Match"


def test_team_sort_key():
    assert sorted(["1234B", "99", "100", "1234A"], key=team_sort_key) == ["99", "100", "1234A", "1234B"]


@pytest.mark.parametrize(
    "text,expected",
    [("9:00 AM", (9, 0)), ("12:15 pm", (12, 15)), ("12:00 AM", (0, 0)), ("1:05PM", (13, 5)),
     ("9:00 AM", (9, 0)), ("9 am", (9, 0)), ("13:30", (13, 30))],
)
def test_parse_time(text, expected, tz):
    result = parse_time(text, "start", tz, today=date(2026, 3, 7))
    assert (result.hour, result.minute) == expected
    assert result.date() == date(2026, 3, 7)
    assert result.tzinfo is tz


@pytest.mark.parametrize("text,message", [("", "Missing start."), ("13:00 PM", "Invalid start."),
                                          ("noon", "Start must be like 9:00 AM."), ("25:00", "Start must be like 9:00 AM.")])
def test_parse_time_errors(text, message, tz):
    with pytest.raises(ValueError) as exc:
        parse_time(text, "start", tz)
    assert str(exc.value) == message
