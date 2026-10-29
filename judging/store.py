"""In-memory canonical state with transactional, atomically persisted updates.

Copy-on-write: `update()` deep-copies the committed state, lets the caller
mutate the copy, and swaps it in only if the callback returns normally. A
committed state object is never mutated afterwards, so views handed to the
response serializer can share structure with it safely outside the lock.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import tempfile
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import ScheduleDict, StateDict
from .services import active_schedule
from .settings import Settings

log = logging.getLogger(__name__)

StateView = dict[str, Any]
# Receives the committed state view, under the store lock, after each commit.
StateListener = Callable[[StateView], None]


def state_view(state: StateDict) -> StateView:
    """Response shape: persisted state plus `slots` derived from the active schedule.

    Read-only: shares nested objects with `state`.
    """
    view: StateView = dict(state)
    schedule = active_schedule(state)
    if schedule is not None:
        view["slots"] = schedule.get("slots", [])
    return view


def migrate_state(raw: Any) -> StateDict:
    """Upgrade older state.json layouts (top-level `slots` copy, no revision)."""
    if not isinstance(raw, dict):
        raise ValueError("state.json does not contain a JSON object")
    state: dict[str, Any] = dict(raw)
    legacy_slots = state.pop("slots", None)
    if isinstance(legacy_slots, list) and legacy_slots and active_schedule(state) is None:  # type: ignore[arg-type]
        schedule_id = "legacy"
        state.setdefault("schedules", []).append(
            {
                "id": schedule_id,
                "label": "Current",
                "type": "printed" if state.get("locked") else "initial",
                "file": f"{schedule_id}.json",
                "slots": legacy_slots,
                "created_at": datetime.now().isoformat(),
            }
        )
        state["active_schedule_id"] = schedule_id
    try:
        state["revision"] = max(0, int(state.get("revision", 0)))
    except (TypeError, ValueError):
        state["revision"] = 0
    return state  # type: ignore[return-value]


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


class StateStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._lock = threading.RLock()
        self._listeners: list[StateListener] = []
        self._state: StateDict = self._load()

    def add_listener(self, listener: StateListener) -> None:
        """`listener` runs under the store lock after each commit, so calls arrive in revision order."""
        with self._lock:
            self._listeners.append(listener)

    # ------------------------------------------------------------------ paths

    @property
    def state_path(self) -> Path:
        return self._settings.state_path

    @property
    def schedules_dir(self) -> Path:
        return self._settings.schedules_dir

    # ------------------------------------------------------------------- load

    def _load(self) -> StateDict:
        path = self.state_path
        if not path.exists():
            return {"revision": 0}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return migrate_state(raw)
        except (ValueError, OSError) as exc:
            backup = path.with_name(f"state.corrupt-{datetime.now():%Y%m%d-%H%M%S}.json")
            log.error("Could not load %s (%s); moving it to %s", path, exc, backup.name)
            try:
                os.replace(path, backup)
            except OSError:
                pass
            return {"revision": 0}

    # ------------------------------------------------------------------- read

    @property
    def revision(self) -> int:
        return int(self._state.get("revision", 0))

    def view(self) -> StateView:
        with self._lock:
            return state_view(self._state)

    # ------------------------------------------------------------------ write

    @contextmanager
    def mutate(self) -> Iterator[StateDict]:
        """Yield a working copy; commit it if the block exits without raising."""
        with self._lock:
            working: StateDict = copy.deepcopy(self._state)
            yield working
            if working != self._state:
                self._commit(working)

    def update(self, fn: Callable[[StateDict], None]) -> StateView:
        """Run `fn` on a working copy under the lock; return the committed view."""
        with self._lock:
            with self.mutate() as working:
                fn(working)
            return state_view(self._state)

    def reset(self) -> StateView:
        """Clear state and schedule archives. Revision keeps counting up."""
        with self._lock:
            fresh: StateDict = {"revision": self.revision + 1}
            if self.schedules_dir.is_dir():
                for path in self.schedules_dir.glob("*.json"):
                    path.unlink(missing_ok=True)
            # Archives written by older versions lived directly in data/.
            keep = {self.state_path, self._settings.draft_path}
            for path in self._settings.data_dir.glob("*.json"):
                if path not in keep:
                    path.unlink(missing_ok=True)
            self._persist(fresh)
            self._state = fresh
            view = state_view(self._state)
            self._notify(view)
            return view

    def _commit(self, working: StateDict) -> None:
        working["revision"] = self.revision + 1
        self._write_new_archives(working)
        self._persist(working)
        self._state = working
        self._notify(state_view(working))

    def _notify(self, view: StateView) -> None:
        for listener in self._listeners:
            try:
                listener(view)
            except Exception:  # a broken listener must not undo a committed mutation
                log.exception("State listener failed")

    def _persist(self, state: StateDict) -> None:
        write_atomic(self.state_path, json.dumps(state, indent=2))

    def _write_new_archives(self, working: StateDict) -> None:
        """Archive each schedule version when it is created or regenerated."""
        previous = {s.get("id"): s.get("created_at") for s in self._state.get("schedules", [])}
        for schedule in working.get("schedules", []):
            if previous.get(schedule.get("id"), object()) != schedule.get("created_at"):
                self._write_archive(schedule)

    def _write_archive(self, schedule: ScheduleDict) -> None:
        name = Path(schedule.get("file") or f"{schedule.get('id')}.json").name
        payload = {"id": schedule.get("id"), "slots": schedule.get("slots", [])}
        write_atomic(self.schedules_dir / name, json.dumps(payload, indent=2))
