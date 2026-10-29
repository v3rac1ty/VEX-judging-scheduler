"""The shared setup form ("draft"): what everyone sees typed into Setup.

Kept apart from scheduling state on purpose: it has its own file
(data/draft.json), lock and revision counter, so keystroke-rate edits never
deep-copy or re-persist the schedules and never bump the state revision.

Copy-on-write like StateStore: a committed draft dict is never mutated, so it
can be handed to serializers outside the lock.
"""
from __future__ import annotations

import json
import logging
import math
import os
import threading
from collections.abc import Callable
from datetime import datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any

from .models import (
    DRAFT_SCHEDULE_FIELDS,
    DRAFT_TEXT_DEFAULTS,
    DRAFT_TEXT_FIELDS,
    EVENT_STATS_DEFAULTS,
    DraftDict,
    DraftPatch,
    DraftPatchResult,
    ScheduleTextDict,
)
from .parsing import MatchPreview, format_clock, preview_matches
from .settings import Settings
from .store import write_atomic

log = logging.getLogger(__name__)

# Receives {"revision", "full", "changes"} after each commit, under the draft lock.
DraftListener = Callable[[dict[str, Any]], None]

DEFAULT_DURATION_MINUTES = 100  # judging window when the schedule names no teams


def default_draft(revision: int = 0) -> DraftDict:
    return {
        "revision": revision,
        **DRAFT_TEXT_DEFAULTS,  # type: ignore[typeddict-item]
        "event_stats": dict(EVENT_STATS_DEFAULTS),
        "match_schedule": {"text": "", "name": None},
        "final_schedule": {"text": "", "name": None},
        "preview": None,
        "final_preview": None,
    }


def _coerce_schedule_text(raw: Any) -> ScheduleTextDict:
    if not isinstance(raw, dict):
        return {"text": "", "name": None}
    text = raw.get("text")
    name = raw.get("name")
    return {
        "text": text if isinstance(text, str) else "",
        "name": name if isinstance(name, str) and name else None,
    }


def coerce_draft(raw: Any) -> DraftDict:
    """Best-effort load of a persisted draft; unknown or mistyped keys fall back to defaults."""
    if not isinstance(raw, dict):
        raise ValueError("draft.json does not contain a JSON object")
    draft = default_draft()
    try:
        draft["revision"] = max(0, int(raw.get("revision", 0)))
    except (TypeError, ValueError):
        draft["revision"] = 0
    for key in DRAFT_TEXT_FIELDS:
        if isinstance(raw.get(key), str):
            draft[key] = raw[key]  # type: ignore[literal-required]
    stats = raw.get("event_stats")
    if isinstance(stats, dict):
        draft["event_stats"].update(
            (k, v) for k, v in stats.items() if k in EVENT_STATS_DEFAULTS and not isinstance(v, (dict, list))
        )
    for field, preview_field in DRAFT_SCHEDULE_FIELDS.items():
        draft[field] = _coerce_schedule_text(raw.get(field))  # type: ignore[literal-required]
        preview = raw.get(preview_field)
        draft[preview_field] = preview if isinstance(preview, dict) else None  # type: ignore[literal-required]
    return draft


def _positive_int(text: str) -> int | None:
    try:
        number = float(str(text).strip())
    except ValueError:
        return None
    if not math.isfinite(number) or number < 1 or number != int(number):
        return None
    return int(number)


def autofill_times(
    preview: MatchPreview | None, judge_pairs: str, slot_minutes: str, tz: tzinfo
) -> dict[str, str] | None:
    """Judging window ending at the first match: ceil(teams / pairs) slots long.

    None when there is no usable first match or the knobs are not valid numbers.
    """
    if not preview or preview.get("error") or not preview.get("first_match"):
        return None
    pairs, slot = _positive_int(judge_pairs), _positive_int(slot_minutes)
    if pairs is None or slot is None:
        return None
    end = datetime.fromisoformat(str(preview["first_match"])).astimezone(tz)
    team_count = int(preview.get("team_count") or 0)
    minutes = math.ceil(team_count / pairs) * slot if team_count > 0 else DEFAULT_DURATION_MINUTES
    start = end - timedelta(minutes=minutes)
    return {"start_time": format_clock(start), "end_time": format_clock(end)}


def compute_changes(draft: DraftDict, patch: DraftPatch, tz: tzinfo) -> dict[str, Any]:
    """Fields of `draft` that `patch` actually changes, plus server-derived fields.

    Nested objects (event_stats, match_schedule, previews) are always sent whole
    so a client can apply `changes` by plain key assignment.
    """
    sent = patch.model_fields_set
    changes: dict[str, Any] = {}

    for key in DRAFT_TEXT_FIELDS:
        if key in sent and getattr(patch, key) != draft[key]:  # type: ignore[literal-required]
            changes[key] = getattr(patch, key)

    if "event_stats" in sent:
        merged = {**draft["event_stats"], **patch.event_stats}
        if merged != draft["event_stats"]:
            changes["event_stats"] = merged

    text_changed: set[str] = set()
    for field, preview_field in DRAFT_SCHEDULE_FIELDS.items():
        if field not in sent:
            continue
        current: ScheduleTextDict = draft[field]  # type: ignore[literal-required]
        incoming = getattr(patch, field)
        if incoming is None:  # explicit null clears the upload
            updated: ScheduleTextDict = {"text": "", "name": None}
        else:
            given = incoming.model_fields_set
            updated = {
                "text": incoming.text if "text" in given else current["text"],
                "name": incoming.name if "name" in given else current["name"],
            }
        if updated == current:
            continue
        changes[field] = updated
        if updated["text"] != current["text"]:
            text_changed.add(field)
            preview = preview_matches(updated["text"], tz)
            if preview != draft[preview_field]:  # type: ignore[literal-required]
                changes[preview_field] = preview

    if "match_schedule" in text_changed or "judge_pairs" in changes or "slot_minutes" in changes:
        merged_draft = {**draft, **changes}
        times = autofill_times(
            merged_draft["preview"], merged_draft["judge_pairs"], merged_draft["slot_minutes"], tz
        )
        for key, value in (times or {}).items():
            if value != draft[key]:  # type: ignore[literal-required]
                changes[key] = value
            else:
                changes.pop(key, None)

    return changes


class DraftStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._lock = threading.RLock()
        self._listeners: list[DraftListener] = []
        self._draft: DraftDict = self._load()

    @property
    def path(self) -> Path:
        return self._settings.draft_path

    @property
    def revision(self) -> int:
        return self._draft["revision"]

    def add_listener(self, listener: DraftListener) -> None:
        """`listener` runs under the draft lock after each commit, so calls arrive in revision order."""
        with self._lock:
            self._listeners.append(listener)

    # ------------------------------------------------------------------- load

    def _load(self) -> DraftDict:
        path = self.path
        if not path.exists():
            return default_draft()
        try:
            return coerce_draft(json.loads(path.read_text(encoding="utf-8")))
        except (ValueError, OSError) as exc:
            backup = path.with_name(f"draft.corrupt-{datetime.now():%Y%m%d-%H%M%S}.json")
            log.error("Could not load %s (%s); moving it to %s", path, exc, backup.name)
            try:
                os.replace(path, backup)
            except OSError:
                pass
            return default_draft()

    # ------------------------------------------------------------- read/write

    def view(self) -> DraftDict:
        """The committed draft (read-only: never mutated after commit)."""
        with self._lock:
            return self._draft

    def patch(self, patch: DraftPatch) -> DraftPatchResult:
        with self._lock:
            changes = compute_changes(self._draft, patch, self._settings.event_tz)
            if not changes:
                return {"revision": self.revision, "changes": {}}
            revision = self.revision + 1
            draft: DraftDict = {**self._draft, **changes, "revision": revision}  # type: ignore[typeddict-item]
            self._commit(draft, {"revision": revision, "full": False, "changes": changes})
            return {"revision": revision, "changes": changes}

    def reset(self) -> None:
        """Back to defaults (revision keeps counting up); no-op when already default."""
        with self._lock:
            fresh = default_draft(self.revision + 1)
            if {**fresh, "revision": self.revision} == self._draft:
                return
            self._commit(fresh, {"revision": fresh["revision"], "full": True, "changes": fresh})

    def _commit(self, draft: DraftDict, event: dict[str, Any]) -> None:
        write_atomic(self.path, json.dumps(draft))
        self._draft = draft
        for listener in self._listeners:
            try:
                listener(event)
            except Exception:  # a broken listener must not undo a committed edit
                log.exception("Draft listener failed")
