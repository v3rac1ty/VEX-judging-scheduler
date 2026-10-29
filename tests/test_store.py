from __future__ import annotations

import json
import random
import threading

from fastapi.testclient import TestClient

from judging.main import create_app
from judging.store import StateStore
from tm_fixture import TEAMS


def _old_slots(first_match):
    start = first_match.replace(hour=9)
    return [
        {"judge_id": 1, "start": start.replace(minute=10 * i).isoformat(),
         "end": start.replace(minute=10 * i + 10).isoformat(), "team": team, "status": "scheduled"}
        for i, team in enumerate(["1001A", "1002B", "1003A"])
    ]


def test_old_format_state_migrates(settings, data_dir, first_match):
    slots = _old_slots(first_match)
    old_state = {
        "config": {"judge_pairs": 1, "slot_minutes": 10, "block_minutes": 8},
        "locked": True,
        "noshow_locked": False,
        "slots": slots,
        "active_schedule_id": "printed-1",
        "schedules": [
            {"id": "schedule-1", "label": "Initial schedule", "type": "initial", "file": "schedule-1.json", "slots": slots},
            {"id": "printed-1", "label": "Printed schedule", "type": "printed", "file": "printed-1.json", "slots": slots},
        ],
        "no_shows": [],
        "no_show_suggestions": [],
    }
    (data_dir / "state.json").write_text(json.dumps(old_state))
    (data_dir / "schedule-1.json").write_text("{}")  # legacy archive location
    client = TestClient(create_app(settings))

    state = client.get("/api/state").json()
    assert state["revision"] == 0
    assert state["slots"] == slots

    state = client.post("/api/checkoff", json={"team": "1002B"}).json()
    assert [s["status"] for s in state["slots"]] == ["scheduled", "checked", "scheduled"]
    persisted = json.loads((data_dir / "state.json").read_text())
    assert "slots" not in persisted and persisted["revision"] == 1
    assert persisted["schedules"][1]["slots"][1]["status"] == "checked"
    assert persisted["schedules"][0]["slots"][1]["status"] == "checked"  # every version agrees

    client.post("/api/reset")
    assert not (data_dir / "schedule-1.json").exists()
    assert (data_dir / ".gitkeep").exists()


def test_very_old_state_with_only_slots(settings, data_dir, first_match):
    slots = _old_slots(first_match)
    (data_dir / "state.json").write_text(json.dumps({"locked": True, "slots": slots}))
    state = TestClient(create_app(settings)).get("/api/state").json()
    assert state["slots"] == slots
    assert state["active_schedule_id"] == "legacy"
    assert state["schedules"][0]["type"] == "printed"


def test_corrupt_state_is_set_aside(settings, data_dir):
    (data_dir / "state.json").write_text("{not json")
    state = TestClient(create_app(settings)).get("/api/state").json()
    state.pop("draft")
    assert state == {"revision": 0}
    assert list(data_dir.glob("state.corrupt-*.json"))


def test_state_survives_restart_and_writes_no_temp_files(settings, data_dir, generate_payload):
    client = TestClient(create_app(settings, rng=random.Random(1)))
    generated = client.post("/api/generate", json=generate_payload).json()
    reloaded = StateStore(settings).view()
    assert reloaded == generated
    assert not list(data_dir.rglob("*.tmp"))


def test_concurrent_actions_all_persist(settings, generate_payload):
    client = TestClient(create_app(settings, rng=random.Random(2)))
    assert client.post("/api/generate", json=generate_payload).status_code == 200
    printed = client.post("/api/snapshot-print", json={}).json()
    start_revision = printed["revision"]

    checked, absent = TEAMS[:10], TEAMS[10:20]
    jobs = [("checkoff", t) for t in checked] + [("noshow", t) for t in absent]
    barrier = threading.Barrier(len(jobs))
    failures: list[str] = []

    def run(endpoint: str, team: str) -> None:
        barrier.wait()
        response = client.post(f"/api/{endpoint}", json={"team": team})
        if response.status_code != 200:
            failures.append(response.text)

    threads = [threading.Thread(target=run, args=job) for job in jobs]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert failures == []

    for state in (client.get("/api/state").json(), StateStore(settings).view()):
        assert state["revision"] == start_revision + len(jobs)
        status = {s["team"]: s["status"] for s in state["slots"] if s["team"]}
        assert all(status[t] == "checked" for t in checked)
        assert all(status[t] == "no-show" for t in absent)
        assert sorted(state["no_shows"]) == sorted(absent)
        assert sorted(s["team"] for s in state["no_show_suggestions"]) == sorted(absent)


def test_mutate_rolls_back_on_error(settings):
    store = StateStore(settings)
    try:
        with store.mutate() as working:
            working["locked"] = True
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert store.view() == {"revision": 0}
    assert not settings.state_path.exists()
