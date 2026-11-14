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
    "lunch_start": "",
    "lunch_end": "",
    "judge_during_lunch": "false",
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
        "lunch_start": None,
        "lunch_end": None,
        # 36 quals every 5 min from 11:00: the last starts 1:55, so quals end at 2:00.
        "elims_start": (first_match + timedelta(minutes=180)).isoformat(),
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
        "preview": {
            "match_count": len(matches),
            "team_count": 24,
            "first_match": first_match.isoformat(),
            "lunch_start": None,
            "lunch_end": None,
            "elims_start": (first_match + timedelta(minutes=180)).isoformat(),
            "error": None,
        },
        # Interviews open at the 11:00 first qual: 24 teams / 4 pairs = 6 slots x 10 min.
        "start_time": "11:00 AM",
        "end_time": "12:00 PM",
    }
    assert ok(client.get("/api/draft"))["start_time"] == "11:00 AM"

    # Judge pairs / slot length changes recompute the end (start is unchanged so not resent).
    assert patch(client, {"judge_pairs": "3"})["changes"] == {"judge_pairs": "3", "end_time": "12:20 PM"}
    assert patch(client, {"slot_minutes": "7"})["changes"] == {"slot_minutes": "7", "end_time": "11:56 AM"}
    # An unusable value leaves the times alone.
    assert patch(client, {"judge_pairs": ""})["changes"] == {"judge_pairs": ""}
    assert patch(client, {"judge_pairs": "abc"})["changes"] == {"judge_pairs": "abc"}
    # A knob change in the same patch as a typed time: the derived time wins (6 x 7 min).
    assert patch(client, {"judge_pairs": "4", "start_time": "8:00 AM"})["changes"] == {"judge_pairs": "4", "end_time": "11:42 AM"}
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
    assert (draft["start_time"], draft["end_time"]) == ("11:00 AM", "12:00 PM")
    # Clearing the upload clears the preview.
    changes = patch(client, {"match_schedule": None})["changes"]
    assert changes == {"match_schedule": {"text": "", "name": None}, "preview": None}


def test_upload_without_teams_uses_default_window(client, matches):
    for match in matches:
        match["matchInfo"]["alliances"] = []
    changes = patch(client, {"match_schedule": {"text": json.dumps(matches)}})["changes"]
    assert changes["preview"]["team_count"] == 0
    assert (changes["start_time"], changes["end_time"]) == ("11:00 AM", "12:40 PM")


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


def test_draft_survives_restart_and_corrupt_file(settings, make_client, data_dir, match_text):
    client = make_client(rng=random.Random(1))
    patch(client, {"match_schedule": {"text": match_text, "name": "a.json"}})
    before = ok(client.get("/api/draft"))
    assert ok(TestClient(create_app(settings)).get("/api/draft")) == before

    (data_dir / "draft.json").write_text("{nope")
    assert ok(TestClient(create_app(settings)).get("/api/draft")) == DEFAULT_DRAFT
    assert list(data_dir.glob("draft.corrupt-*.json"))


# ------------------------------------------------------------------------- lunch


def with_lunch(matches, after_match: int, minutes: int):
    """Shift every match after the `after_match`-th by `minutes` (a lunch break)."""
    for index, match in enumerate(matches):
        if index >= after_match:
            match["matchInfo"]["timeScheduled"] += minutes * 60
    return matches


def test_preview_detects_lunch(matches, first_match, tz):
    # Matches every 5 min from 11:00; a 60-minute pause after the 12th (11:55 AM).
    preview = preview_matches(json.dumps(with_lunch(matches, 12, 60)), tz)
    assert preview["lunch_start"] == (first_match + timedelta(minutes=60)).isoformat()  # 11:55 + one cycle
    assert preview["lunch_end"] == (first_match + timedelta(minutes=120)).isoformat()  # 12:00 + 60
    # Ordinary spacing is not lunch.
    assert preview_matches(json.dumps(matches[:12]), tz)["lunch_start"] is None


def test_lunch_is_skipped_by_default_and_toggle_includes_it(client, matches):
    text = json.dumps(with_lunch(matches, 3, 60))  # pause after 11:10 -> lunch 11:15-12:15
    changes = patch(client, {"match_schedule": {"text": text}})["changes"]
    assert (changes["lunch_start"], changes["lunch_end"]) == ("11:15 AM", "12:15 PM")
    # 6 interviews per pair from 11:00: one before lunch (11:10 would run into it), five after.
    assert (changes["start_time"], changes["end_time"]) == ("11:00 AM", "1:05 PM")

    # Judging through lunch: the window shrinks back to 6 x 10 min.
    assert patch(client, {"judge_during_lunch": True})["changes"] == {"judge_during_lunch": "true", "end_time": "12:00 PM"}
    assert patch(client, {"judge_during_lunch": "false"})["changes"] == {"judge_during_lunch": "false", "end_time": "1:05 PM"}
    # Editing the lunch window moves the end too.
    assert patch(client, {"lunch_end": "12:45 PM"})["changes"] == {"lunch_end": "12:45 PM", "end_time": "1:35 PM"}


def test_generate_skips_lunch_unless_judging_through_it(client, generate_payload):
    def generate(body):
        state = ok(client.post("/api/generate", json=generate_payload | body))
        return {slot["start"][11:16] for slot in state["slots"]}, state["config"]

    lunch = {"lunch_start": "9:30 AM", "lunch_end": "10:00 AM"}
    starts, config = generate(lunch)
    assert not any("09:30" <= start < "10:00" for start in starts)
    assert {"09:20", "10:00"} <= starts
    assert config["judge_during_lunch"] is False
    assert config["lunch_start"][11:16] == "09:30" and config["lunch_end"][11:16] == "10:00"

    starts, config = generate(lunch | {"judge_during_lunch": "true"})
    assert "09:30" in starts and config["judge_during_lunch"] is True

    bad = generate_payload | {"lunch_start": "noon", "lunch_end": "1:00 PM"}
    assert error(client.post("/api/generate", json=bad)) == "Lunch start must be like 9:00 AM."
    backwards = generate_payload | {"lunch_start": "1:00 PM", "lunch_end": "12:00 PM"}
    assert error(client.post("/api/generate", json=backwards)) == "Lunch must end after it starts."


# ------------------------------------------------------------------ eliminations


def add_elim(matches, at):
    elim = json.loads(json.dumps(matches[0]))
    elim["matchInfo"]["matchTuple"] = {"round": "QF", "match": 1}
    elim["matchInfo"]["timeScheduled"] = int(at.timestamp())
    return matches + [elim]


def test_preview_uses_first_elimination_match(matches, first_match, tz):
    at = first_match + timedelta(hours=4)
    assert preview_matches(json.dumps(add_elim(matches, at)), tz)["elims_start"] == at.isoformat()


def test_autofill_never_runs_into_eliminations(client, matches, first_match):
    # Quals end at 2:00 PM. One judge pair needs 24 x 10 min = 4 hours from 11:00.
    changes = patch(client, {"match_schedule": {"text": json.dumps(matches)}, "judge_pairs": "1"})["changes"]
    assert (changes["start_time"], changes["end_time"]) == ("11:00 AM", "2:00 PM")


def test_generate_stops_at_eliminations(client, generate_payload, first_match):
    # Typed window runs to 3:00 PM, but quals (and so interviews) end at 2:00 PM.
    body = generate_payload | {"judge_pairs": "1", "start_time": "11:00 AM", "end_time": "3:00 PM"}
    state = ok(client.post("/api/generate", json=body))
    assert state["config"]["end_time"][11:16] == "14:00"
    assert state["config"]["elims_start"][11:16] == "14:00"
    assert max(slot["end"] for slot in state["slots"] if slot["team"])[11:16] <= "14:00"
    assert len(state["unassigned"]) == 24 - 18  # 18 ten-minute slots fit before 2:00

    late = generate_payload | {"start_time": "2:30 PM", "end_time": "3:00 PM"}
    assert error(client.post("/api/generate", json=late)) == "Judging must start before eliminations do."
