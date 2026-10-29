"""Optional `schedule_id` on team actions and snapshot-print (per-device schedule view)."""
from __future__ import annotations

from tm_fixture import TEAMS


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def error(response, status=400):
    assert response.status_code == status, response.text
    return response.json()["detail"]


def schedule(state, schedule_type):
    return next(s for s in state["schedules"] if s["type"] == schedule_type)


def status_in(sched, team):
    return next(s["status"] for s in sched["slots"] if s["team"] == team)


def test_unknown_schedule_id_is_404(client, generate_payload):
    ok(client.post("/api/generate", json=generate_payload))
    ok(client.post("/api/snapshot-print", json={}))
    for endpoint in ("checkoff", "noshow", "not-competing"):
        body = {"team": TEAMS[0], "schedule_id": "nope"}
        assert error(client.post(f"/api/{endpoint}", json=body), 404) == "Schedule not found."
    assert error(client.post("/api/snapshot-print", json={"schedule_id": "nope"}), 404) == "Schedule not found."
    # Blank id means "active", like omitting it.
    state = ok(client.post("/api/checkoff", json={"team": TEAMS[0], "schedule_id": "  "}))
    assert status_in(schedule(state, "printed"), TEAMS[0]) == "checked"


def test_actions_target_the_named_schedule(client, generate_payload):
    ok(client.post("/api/generate", json=generate_payload | {"judge_pairs": "1", "end_time": "1:00 PM"}))
    printed = ok(client.post("/api/snapshot-print", json={}))
    printed_id = schedule(printed, "printed")["id"]
    initial_id = schedule(printed, "initial")["id"]

    # Targeting the (non-active) initial schedule leaves the active id alone; the
    # team's status is shared by every version that has it.
    state = ok(client.post("/api/checkoff", json={"team": TEAMS[0], "schedule_id": initial_id}))
    assert state["active_schedule_id"] == printed_id
    assert status_in(schedule(state, "initial"), TEAMS[0]) == "checked"
    assert status_in(schedule(state, "printed"), TEAMS[0]) == "checked"

    for team in TEAMS[1:3]:
        ok(client.post("/api/noshow", json={"team": team, "schedule_id": printed_id}))
    state = ok(client.post("/api/generate-noshow", json={}))
    noshow_id = schedule(state, "noshow")["id"]
    assert state["active_schedule_id"] == noshow_id

    # Lock check follows the targeted schedule's type, not the active one's.
    assert error(client.post("/api/checkoff", json={"team": TEAMS[1]})) == "Print the no-show schedule before checking off teams."
    assert error(client.post("/api/checkoff", json={"team": TEAMS[1], "schedule_id": noshow_id})) == "Print the no-show schedule before checking off teams."
    state = ok(client.post("/api/checkoff", json={"team": TEAMS[3], "schedule_id": printed_id}))
    assert status_in(schedule(state, "printed"), TEAMS[3]) == "checked"
    state = ok(client.post("/api/not-competing", json={"team": TEAMS[4], "schedule_id": printed_id}))
    assert TEAMS[4] in state["not_competing"]

    # Someone switches the server's current schedule; printing by id still prints the no-show one.
    ok(client.post("/api/active-schedule", json={"schedule_id": printed_id}))
    state = ok(client.post("/api/snapshot-print", json={"schedule_id": noshow_id}))
    assert state["noshow_locked"] is True
    printed_noshow = schedule(state, "printed-noshow")
    assert state["active_schedule_id"] == printed_noshow["id"]
    assert printed_noshow["slots"] == schedule(state, "noshow")["slots"]

    # Printing an already-printed kind again is a no-op.
    revision = state["revision"]
    assert ok(client.post("/api/snapshot-print", json={"schedule_id": printed_noshow["id"]}))["revision"] == revision
    assert ok(client.post("/api/snapshot-print", json={"schedule_id": initial_id}))["revision"] == revision

    state = ok(client.post("/api/checkoff", json={"team": TEAMS[1], "schedule_id": printed_noshow["id"]}))
    for schedule_type in ("initial", "printed", "noshow", "printed-noshow"):
        assert status_in(schedule(state, schedule_type), TEAMS[1]) == "checked"


def test_snapshot_print_by_id_before_lock(client, generate_payload):
    state = ok(client.post("/api/generate", json=generate_payload))
    initial_id = state["active_schedule_id"]
    state = ok(client.post("/api/snapshot-print", json={"schedule_id": initial_id, "label": "Morning"}))
    assert state["locked"] is True
    assert schedule(state, "printed")["label"] == "Morning"
    # Team actions targeting the initial schedule are allowed once the main schedule is printed.
    ok(client.post("/api/noshow", json={"team": TEAMS[0], "schedule_id": initial_id}))


def test_checkoff_via_printed_shows_in_every_version(client, generate_payload):
    ok(client.post("/api/generate", json=generate_payload))
    ok(client.post("/api/snapshot-print", json={}))
    state = ok(client.post("/api/checkoff", json={"team": TEAMS[0]}))
    assert status_in(schedule(state, "printed"), TEAMS[0]) == "checked"
    assert status_in(schedule(state, "initial"), TEAMS[0]) == "checked"
    # Other teams are untouched in both copies.
    for schedule_type in ("initial", "printed"):
        others = [s["status"] for s in schedule(state, schedule_type)["slots"] if s["team"] and s["team"] != TEAMS[0]]
        assert set(others) == {"scheduled"}
    persisted = client.get("/api/state").json()
    assert status_in(schedule(persisted, "initial"), TEAMS[0]) == "checked"


def test_noshow_rescheduled_then_checked_everywhere(client, generate_payload):
    ok(client.post("/api/generate", json=generate_payload | {"judge_pairs": "1", "end_time": "1:00 PM"}))
    ok(client.post("/api/snapshot-print", json={}))
    absent = TEAMS[1]
    state = ok(client.post("/api/noshow", json={"team": absent}))
    assert status_in(schedule(state, "initial"), absent) == "no-show"
    assert status_in(schedule(state, "printed"), absent) == "no-show"

    state = ok(client.post("/api/generate-noshow", json={}))
    noshow_id = schedule(state, "noshow")["id"]
    assert status_in(schedule(state, "noshow"), absent) == "rescheduled"
    ok(client.post("/api/snapshot-print", json={"schedule_id": noshow_id}))

    state = ok(client.post("/api/checkoff", json={"team": absent, "schedule_id": noshow_id}))
    for schedule_type in ("initial", "printed", "noshow", "printed-noshow"):
        assert status_in(schedule(state, schedule_type), absent) == "checked", schedule_type
    assert absent not in state["no_shows"]


def test_lock_check_and_membership_follow_targeted_schedule(client, generate_payload):
    ok(client.post("/api/generate", json=generate_payload | {"judge_pairs": "1", "end_time": "1:00 PM"}))
    state = ok(client.post("/api/snapshot-print", json={}))
    printed_id = schedule(state, "printed")["id"]
    absent, late = TEAMS[1], TEAMS[2]
    ok(client.post("/api/noshow", json={"team": absent}))
    state = ok(client.post("/api/generate-noshow", json={}))
    noshow_id = schedule(state, "noshow")["id"]

    # The unprinted no-show draft is still locked against team actions...
    assert error(client.post("/api/checkoff", json={"team": absent, "schedule_id": noshow_id})) == (
        "Print the no-show schedule before checking off teams."
    )
    # ...but the printed main schedule is open, and its action reaches the draft copy too.
    state = ok(client.post("/api/checkoff", json={"team": absent, "schedule_id": printed_id}))
    for schedule_type in ("initial", "printed", "noshow"):
        assert status_in(schedule(state, schedule_type), absent) == "checked", schedule_type

    # A team the targeted schedule doesn't contain is 404 even though other versions have it.
    state = ok(client.post("/api/snapshot-print", json={"schedule_id": noshow_id}))
    printed_noshow_id = schedule(state, "printed-noshow")["id"]
    for endpoint in ("checkoff", "noshow"):
        body = {"team": late, "schedule_id": printed_noshow_id}
        assert error(client.post(f"/api/{endpoint}", json=body), 404) == "Team not found."
    assert status_in(schedule(client.get("/api/state").json(), "printed"), late) == "scheduled"
