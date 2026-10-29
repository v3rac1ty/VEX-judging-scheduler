from __future__ import annotations

import json
import random
from datetime import timedelta

from fastapi.testclient import TestClient

from judging.main import create_app
from judging.models import EVENT_STATS_DEFAULTS, MAX_SCHEDULE_TEXT_BYTES
from judging.parsing import format_clock, preview_matches
from tm_fixture import as_log

DEFAULT_DRAFT = {
    "revision": 0,
    "judge_pairs": "4",
    "slot_minutes": "10",
    "block_minutes": "8",
    "start_time": "",
    "end_time": "",
    "event_stats": EVENT_STATS_DEFAULTS,
    "match_schedule": {"text": "", "name": None},
    "final_schedule": {"text": "", "name": None},
    "preview": None,
    "final_preview": None,
}


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def error(response, status=400):
    assert response.status_code == status, response.text
    return response.json()["detail"]


def patch(client, body):
    return ok(client.patch("/api/draft", json=body))


# ------------------------------------------------------------------ parsing helpers


def test_format_clock(first_match):
    assert format_clock(first_match.replace(hour=9, minute=5)) == "9:05 AM"
    assert format_clock(first_match.replace(hour=0, minute=0)) == "12:00 AM"
    assert format_clock(first_match.replace(hour=12, minute=30)) == "12:30 PM"
    assert format_clock(first_match.replace(hour=13, minute=0)) == "1:00 PM"


def test_preview_matches(matches, first_match, tz):
    assert preview_matches("", tz) is None
    assert preview_matches("   ", tz) is None
    preview = preview_matches(as_log(matches), tz)
    assert preview == {
        "match_count": len(matches),
        "team_count": 24,
        "first_match": first_match.isoformat(),
        "error": None,
    }
    bad = preview_matches("garbage", tz)
    assert bad["error"] == "Could not find JSON array in match schedule input."
    assert bad["first_match"] is None and bad["match_count"] == 0


def test_preview_prefers_first_qual(matches, first_match, tz):
    practice = json.loads(json.dumps(matches[0]))
    practice["matchInfo"]["matchTuple"] = {"round": "PRACTICE", "match": 1}
    practice["matchInfo"]["timeScheduled"] = int((first_match - timedelta(hours=1)).timestamp())
    assert preview_matches(json.dumps([practice] + matches), tz)["first_match"] == first_match.isoformat()
    # No quals at all: earliest of any round.
    assert preview_matches(json.dumps([practice]), tz)["first_match"] == (first_match - timedelta(hours=1)).isoformat()


# ---------------------------------------------------------------------- endpoints


def test_draft_defaults_and_state_embedding(client, generate_payload):
    assert ok(client.get("/api/draft")) == DEFAULT_DRAFT
    assert ok(client.get("/api/state"))["draft"] == DEFAULT_DRAFT
    generated = ok(client.post("/api/generate", json=generate_payload))
    assert "draft" not in generated
    assert "draft" not in ok(client.post("/api/snapshot-print", json={}))


def test_patch_simple_fields_and_noop(client, settings):
    result = patch(client, {"judge_pairs": "5", "start_time": "9:00 AM", "block_minutes": 6})
    assert result == {"revision": 1, "changes": {"judge_pairs": "5", "start_time": "9:00 AM", "block_minutes": "6"}}
    draft = ok(client.get("/api/draft"))
    assert draft["revision"] == 1 and draft["judge_pairs"] == "5" and draft["block_minutes"] == "6"
    assert json.loads(settings.draft_path.read_text())["judge_pairs"] == "5"
    # Draft edits never touch scheduling state.
    assert ok(client.get("/api/state"))["revision"] == 0
    assert not settings.state_path.exists()

    # Only changed fields are reported; an unchanged patch is a no-op.
    assert patch(client, {"judge_pairs": "5", "slot_minutes": "12"}) == {"revision": 2, "changes": {"slot_minutes": "12"}}
    assert patch(client, {"judge_pairs": "5"}) == {"revision": 2, "changes": {}}
    assert patch(client, {}) == {"revision": 2, "changes": {}}
    assert patch(client, {"end_time": None}) == {"revision": 2, "changes": {}}  # null == ""


def test_patch_allowlist_and_validation(client):
    assert error(client.patch("/api/draft", json={"judge_pairs": "4", "bogus": 1})) == "Unknown field: bogus."
    assert error(client.patch("/api/draft", json={"match_schedule": {"text": "", "extra": 1}})) == "Unknown field: match_schedule.extra."
    assert error(client.patch("/api/draft", json={"event_stats": {"nope": 1}})) == "Unknown event_stats field: nope."
    assert error(client.patch("/api/draft", json={"event_stats": [1]})) == "event_stats must be an object."
    assert error(client.patch("/api/draft", json={"judge_pairs": True})) == "Setup fields must be strings."
    assert error(client.patch("/api/draft", json={"match_schedule": {"text": 5}})) == "Schedule text must be a string."
    assert error(client.patch("/api/draft", json=[1])) == "Request body must be a JSON object."
    assert ok(client.get("/api/draft"))["revision"] == 0


def test_patch_text_size_cap(client):
    too_big = "x" * (MAX_SCHEDULE_TEXT_BYTES + 1)
    assert error(client.patch("/api/draft", json={"final_schedule": {"text": too_big}})) == "Schedule text must be 5 MB or less."
    multibyte = "é" * (MAX_SCHEDULE_TEXT_BYTES // 2 + 1)  # under the cap in chars, over it in bytes
    assert error(client.patch("/api/draft", json={"match_schedule": {"text": multibyte}})) == "Schedule text must be 5 MB or less."
    at_cap = "x" * MAX_SCHEDULE_TEXT_BYTES
    changes = patch(client, {"final_schedule": {"text": at_cap, "name": "big.txt"}})["changes"]
    assert changes["final_schedule"]["name"] == "big.txt"
    assert changes["final_preview"]["error"]


def test_event_stats_partial_merge(client):
    result = patch(client, {"event_stats": {"rounds_min": 9, "spacing_avg": "7.5"}})
    assert result["changes"] == {"event_stats": EVENT_STATS_DEFAULTS | {"rounds_min": 9, "spacing_avg": "7.5"}}
    result = patch(client, {"event_stats": {"rounds_max": 10}})
    assert result["changes"]["event_stats"] == EVENT_STATS_DEFAULTS | {"rounds_min": 9, "spacing_avg": "7.5", "rounds_max": 10}
    assert patch(client, {"event_stats": {"rounds_max": 10}})["changes"] == {}


def test_upload_derives_preview_and_times(client, match_text, matches, first_match):
    result = patch(client, {"match_schedule": {"text": match_text, "name": "initial.json"}})
    assert result["revision"] == 1
    assert result["changes"] == {
        "match_schedule": {"text": match_text, "name": "initial.json"},
        "preview": {"match_count": len(matches), "team_count": 24, "first_match": first_match.isoformat(), "error": None},
        # 24 teams / 4 pairs = 6 slots x 10 min before the 11:00 first match.
        "end_time": "11:00 AM",
        "start_time": "10:00 AM",
    }
    assert ok(client.get("/api/draft"))["start_time"] == "10:00 AM"

    # Judge pairs / slot length changes recompute the start (end is unchanged so not resent).
    assert patch(client, {"judge_pairs": "3"})["changes"] == {"judge_pairs": "3", "start_time": "9:40 AM"}
    assert patch(client, {"slot_minutes": "7"})["changes"] == {"slot_minutes": "7", "start_time": "10:04 AM"}
    # An unusable value leaves the times alone.
    assert patch(client, {"judge_pairs": ""})["changes"] == {"judge_pairs": ""}
    assert patch(client, {"judge_pairs": "abc"})["changes"] == {"judge_pairs": "abc"}
    # A knob change in the same patch as a typed time: the derived time wins (6 x 7 min).
    assert patch(client, {"judge_pairs": "4", "start_time": "8:00 AM"})["changes"] == {"judge_pairs": "4", "start_time": "10:18 AM"}
    # Other fields don't trigger autofill.
    assert patch(client, {"block_minutes": "5", "start_time": "8:30 AM"})["changes"] == {"block_minutes": "5", "start_time": "8:30 AM"}

    # Renaming only: no preview or time recompute.
    assert patch(client, {"match_schedule": {"name": "renamed.json"}})["changes"] == {
        "match_schedule": {"text": match_text, "name": "renamed.json"}
    }
    # Same text again: nothing changes.
    assert patch(client, {"match_schedule": {"text": match_text, "name": "renamed.json"}})["changes"] == {}


def test_bad_upload_sets_error_and_keeps_times(client, match_text):
    patch(client, {"match_schedule": {"text": match_text, "name": "a.json"}})
    changes = patch(client, {"match_schedule": {"text": "garbage", "name": "b.txt"}})["changes"]
    assert set(changes) == {"match_schedule", "preview"}
    assert changes["preview"]["error"] == "Could not find JSON array in match schedule input."
    assert patch(client, {"judge_pairs": "2"})["changes"] == {"judge_pairs": "2"}  # no valid preview
    draft = ok(client.get("/api/draft"))
    assert (draft["start_time"], draft["end_time"]) == ("10:00 AM", "11:00 AM")
    # Clearing the upload clears the preview.
    changes = patch(client, {"match_schedule": None})["changes"]
    assert changes == {"match_schedule": {"text": "", "name": None}, "preview": None}


def test_upload_without_teams_uses_default_window(client, matches):
    for match in matches:
        match["matchInfo"]["alliances"] = []
    changes = patch(client, {"match_schedule": {"text": json.dumps(matches)}})["changes"]
    assert changes["preview"]["team_count"] == 0
    assert (changes["start_time"], changes["end_time"]) == ("9:20 AM", "11:00 AM")


def test_final_schedule_preview_has_no_autofill(client, match_text):
    changes = patch(client, {"final_schedule": {"text": match_text, "name": "final.json"}})["changes"]
    assert set(changes) == {"final_schedule", "final_preview"}
    assert changes["final_preview"]["team_count"] == 24


def test_reset_clears_draft(client, match_text, data_dir):
    patch(client, {"match_schedule": {"text": match_text, "name": "a.json"}, "judge_pairs": "3"})
    revision = ok(client.get("/api/draft"))["revision"]
    ok(client.post("/api/reset"))
    assert ok(client.get("/api/draft")) == DEFAULT_DRAFT | {"revision": revision + 1}
    assert (data_dir / "draft.json").exists()
    # Resetting an already-default draft does not bump its revision.
    ok(client.post("/api/reset"))
    assert ok(client.get("/api/draft"))["revision"] == revision + 1


def test_draft_survives_restart_and_corrupt_file(settings, data_dir, match_text):
    client = TestClient(create_app(settings, rng=random.Random(1)))
    patch(client, {"match_schedule": {"text": match_text, "name": "a.json"}})
    before = ok(client.get("/api/draft"))
    assert ok(TestClient(create_app(settings)).get("/api/draft")) == before

    (data_dir / "draft.json").write_text("{nope")
    assert ok(TestClient(create_app(settings)).get("/api/draft")) == DEFAULT_DRAFT
    assert list(data_dir.glob("draft.corrupt-*.json"))
