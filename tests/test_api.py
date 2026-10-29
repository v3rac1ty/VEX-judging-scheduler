from __future__ import annotations

import json

import pytest

from tm_fixture import TEAMS, as_log, make_matches

STATE_KEYS = {
    "config", "locked", "noshow_locked", "team_count", "final_match_teams", "not_competing",
    "slots", "active_schedule_id", "schedules", "unassigned", "team_matches", "no_shows",
    "no_show_suggestions", "revision",
}


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def error(response, status=400):
    assert response.status_code == status, response.text
    detail = response.json()["detail"]
    assert isinstance(detail, str)
    return detail


def without_draft(state):
    return {k: v for k, v in state.items() if k != "draft"}


def active(state):
    return next(s for s in state["schedules"] if s["id"] == state["active_schedule_id"])


def slot_for(state, team):
    return next(s for s in state["slots"] if s["team"] == team)


def test_index_and_static(client):
    index = client.get("/")
    assert index.status_code == 200 and "<html" in index.text.lower()
    assert client.get("/static/styles.css").status_code == 200


def test_empty_state(client):
    state = ok(client.get("/api/state"))
    assert "slots" not in state
    assert state.pop("draft")["revision"] == 0
    assert state == {"revision": 0}


def test_full_flow(client, generate_payload, matches, first_match, data_dir):
    state = ok(client.post("/api/generate", json=generate_payload))
    assert STATE_KEYS <= set(state)
    assert state["revision"] == 1
    assert state["team_count"] == 24 and state["unassigned"] == []
    assert state["locked"] is False and state["noshow_locked"] is False
    assert state["config"]["judge_pairs"] == 4 and state["config"]["slot_minutes"] == 10
    assert state["config"]["event_stats"] == {"rounds_min": 6, "rounds_max": 6}
    assert len(state["schedules"]) == 1 and active(state)["type"] == "initial"
    assert state["slots"] == active(state)["slots"]
    assert sorted(s["team"] for s in state["slots"] if s["team"]) == sorted(TEAMS)
    initial_id = state["active_schedule_id"]
    assert (data_dir / "schedules" / f"{initial_id}.json").exists()
    persisted = json.loads((data_dir / "state.json").read_text())
    assert "slots" not in persisted and persisted["revision"] == 1

    # Actions require a printed schedule.
    assert error(client.post("/api/checkoff", json={"team": TEAMS[0]})) == "Print the schedule before checking off teams."
    assert error(client.post("/api/noshow", json={"team": TEAMS[0]})) == "Print the schedule before checking off teams."

    state = ok(client.post("/api/snapshot-print", json={"label": "Printed schedule"}))
    assert state["locked"] is True
    assert active(state)["type"] == "printed" and active(state)["label"] == "Printed schedule"
    printed_id = state["active_schedule_id"]
    assert (data_dir / "schedules" / f"{printed_id}.json").exists()
    revision = state["revision"]
    # Printing again is a no-op (no revision bump).
    assert ok(client.post("/api/snapshot-print", json={}))["revision"] == revision

    assert error(client.post("/api/generate", json=generate_payload)) == "Schedule is locked after printing."

    checked, absent, dropped = TEAMS[0], TEAMS[1], TEAMS[2]
    state = ok(client.post("/api/checkoff", json={"team": checked}))
    assert slot_for(state, checked)["status"] == "checked"
    # Status is shared by every schedule version that has the team.
    initial = next(s for s in state["schedules"] if s["id"] == initial_id)
    assert slot_for(initial, checked)["status"] == "checked"
    assert all(s["status"] == "scheduled" for s in initial["slots"] if s["team"] != checked)
    assert error(client.post("/api/checkoff", json={"team": "0000X"}), 404) == "Team not found."

    state = ok(client.post("/api/noshow", json={"team": absent}))
    state = ok(client.post("/api/noshow", json={"team": dropped}))
    assert state["no_shows"] == [absent, dropped]
    assert slot_for(state, absent)["status"] == "no-show"
    assert [s["team"] for s in state["no_show_suggestions"]] == [absent, dropped]
    assert state["last_suggestions"]["team"] == dropped
    assert state["no_show_suggestions"][0]["gaps"]

    # Final schedule drops one no-show team: it is crossed off as not competing.
    final_teams = [t if t != dropped else "9999Z" for t in TEAMS]
    final_text = as_log(make_matches(first_match, teams=final_teams, seed=11))
    state = ok(client.post("/api/generate-noshow", json={"match_schedule": final_text, "event_stats": {"x": 1}}))
    assert dropped not in state["final_match_teams"] and absent in state["final_match_teams"]
    assert dropped in state["not_competing"]
    printed = next(s for s in state["schedules"] if s["id"] == printed_id)
    assert slot_for(printed, dropped)["status"] == "checked"
    flagged = {s["team"]: s for s in state["no_show_suggestions"]}
    assert flagged[dropped].get("not_in_schedule") is True
    assert state["last_suggestions"]["team"] == absent
    assert active(state)["type"] == "noshow"
    assert [s["team"] for s in state["slots"]] == [absent]
    assert state["slots"][0]["status"] == "rescheduled" and state["slots"][0]["between"]
    assert flagged[absent]["judge_id"] == state["slots"][0]["judge_id"]
    assert state["config"]["event_stats"] == {"x": 1}

    assert error(client.post("/api/checkoff", json={"team": absent})) == "Print the no-show schedule before checking off teams."

    state = ok(client.post("/api/snapshot-print", json={"label": "ignored"}))
    assert state["noshow_locked"] is True
    assert active(state)["type"] == "printed-noshow" and active(state)["label"] == "Printed no-show recovery"
    assert error(client.post("/api/generate-noshow", json={})) == "No-show schedule is locked after printing."

    state = ok(client.post("/api/checkoff", json={"team": absent}))
    assert slot_for(state, absent)["status"] == "checked"
    assert absent not in state["no_shows"]
    assert "last_suggestions" not in state

    state = ok(client.post("/api/active-schedule", json={"schedule_id": initial_id}))
    assert state["active_schedule_id"] == initial_id
    assert state["slots"] == next(s for s in state["schedules"] if s["id"] == initial_id)["slots"]
    assert error(client.post("/api/active-schedule", json={"schedule_id": "nope"}), 404) == "Schedule not found."
    assert error(client.post("/api/active-schedule", json={})) == "Missing schedule id."
    assert len(state["schedules"]) == 4

    before = state["revision"]
    state = ok(client.post("/api/reset", json={}))
    assert state == {"revision": before + 1}
    assert without_draft(ok(client.get("/api/state"))) == {"revision": before + 1}
    assert list((data_dir / "schedules").glob("*.json")) == []
    assert (data_dir / ".gitkeep").exists()


def test_not_competing(client, generate_payload):
    ok(client.post("/api/generate", json=generate_payload))
    assert error(client.post("/api/not-competing", json={"team": TEAMS[3]})) == "Print the schedule before checking off teams."
    ok(client.post("/api/snapshot-print", json={}))
    ok(client.post("/api/noshow", json={"team": TEAMS[3]}))
    state = ok(client.post("/api/not-competing", json={"team": TEAMS[3]}))
    assert state["not_competing"] == [TEAMS[3]]
    assert state["no_shows"] == [] and state["no_show_suggestions"] == []
    assert "last_suggestions" not in state
    # Marking no-show again removes it from not_competing.
    state = ok(client.post("/api/noshow", json={"team": TEAMS[3]}))
    assert state["not_competing"] == []


def test_generate_reuses_team_matches_and_reports_unassigned(client, generate_payload):
    ok(client.post("/api/generate", json=generate_payload))
    short = generate_payload | {"match_schedule": "", "judge_pairs": 2, "end_time": "10:00 AM"}
    state = ok(client.post("/api/generate", json=short))
    assert state["team_count"] == 24
    assert len(state["unassigned"]) == 12
    assert [s["team"] for s in state["no_show_suggestions"]] == state["unassigned"]
    assert state["last_suggestions"]["team"] == state["unassigned"][-1]
    assert len(state["schedules"]) == 1


def test_generate_with_log_format(client, generate_payload, matches):
    state = ok(client.post("/api/generate", json=generate_payload | {"match_schedule": as_log(matches)}))
    assert state["team_count"] == 24


@pytest.mark.parametrize(
    "overrides,detail",
    [
        ({"judge_pairs": "0"}, "Judge pairs must be at least 1."),
        ({"judge_pairs": "abc"}, "Judge pairs must be a whole number."),
        ({"slot_minutes": ""}, "Slot minutes must be a whole number."),
        ({"slot_minutes": 7.5}, "Slot minutes must be a whole number."),
        ({"block_minutes": "-1"}, "Block minutes must be at least 0."),
        ({"start_time": ""}, "Missing judging start time."),
        ({"end_time": "later"}, "Judging end time must be like 9:00 AM."),
        ({"end_time": "8:00 AM"}, "Judging end time must be after the start time."),
        ({"match_schedule": "garbage"}, "Could not find JSON array in match schedule input."),
        ({"match_schedule": "[]"}, "No teams found in match schedule."),
        ({"match_schedule": ""}, "Upload match schedule before generating."),
        ({"event_stats": [1]}, "event_stats must be an object."),
    ],
)
def test_generate_validation(client, generate_payload, overrides, detail):
    assert error(client.post("/api/generate", json=generate_payload | overrides)) == detail


def test_request_body_errors(client):
    assert error(client.post("/api/checkoff", json={})) == "Missing team."
    assert error(client.post("/api/checkoff", json={"team": "  "})) == "Missing team."
    assert error(client.post("/api/checkoff", content=b"{nope", headers={"content-type": "application/json"})) == "Request body must be valid JSON."
    assert error(client.post("/api/checkoff", json=[1])) == "Request body must be a JSON object."


def test_numeric_team_is_accepted(client, generate_payload):
    ok(client.post("/api/generate", json=generate_payload))
    ok(client.post("/api/snapshot-print", json={}))
    assert error(client.post("/api/checkoff", json={"team": 1234}), 404) == "Team not found."


def test_snapshot_without_schedule(client):
    assert error(client.post("/api/snapshot-print", json={})) == "No schedule to snapshot."


def test_generate_noshow_without_gaps(client, generate_payload):
    ok(client.post("/api/generate", json=generate_payload))
    assert error(client.post("/api/generate-noshow")) == "No gaps available for no-show teams."
    assert ok(client.get("/api/state"))["revision"] == 1  # failed request changed nothing


def test_regenerating_noshow_draft_is_stable(client, generate_payload):
    ok(client.post("/api/generate", json=generate_payload | {"judge_pairs": "1", "end_time": "1:00 PM"}))
    ok(client.post("/api/snapshot-print", json={}))
    for team in TEAMS[:3]:
        ok(client.post("/api/noshow", json={"team": team}))
    first = ok(client.post("/api/generate-noshow", json={}))
    second = ok(client.post("/api/generate-noshow", json={}))
    assert first["active_schedule_id"] == second["active_schedule_id"]
    assert first["slots"] == second["slots"]
    assert len([s for s in second["schedules"] if s["type"] == "noshow"]) == 1


def test_failed_mutation_leaves_state_untouched(client, generate_payload):
    ok(client.post("/api/generate", json=generate_payload))
    before = ok(client.get("/api/state"))
    error(client.post("/api/generate-noshow", json={"match_schedule": "garbage", "event_stats": {"x": 1}}))
    assert ok(client.get("/api/state")) == before
