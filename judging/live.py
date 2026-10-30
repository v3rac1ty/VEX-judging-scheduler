"""Server-Sent Events hub behind `GET /api/events`.

Threading model: mutations commit in threadpool threads and call
`publish_state` / `publish_draft` from there (under the store's lock, so calls
arrive in revision order). Each publish serializes its JSON once, in the
calling thread, then hands the finished SSE message to the event loop with
`loop.call_soon_threadsafe`; everything touching client subscriptions runs on
the loop thread only, so no locking is needed there.

Per client:
- state: a single "latest" slot. A slow client gets only the newest state
  (coalesced), never a backlog.
- draft: an ordered queue of partial patches (they cannot be dropped). If it
  grows past `draft_queue_limit`, it is discarded and the client is sent one
  full draft instead.
- presence: the latest client count.
- tm: Tournament Manager status (tm.py). The hub keeps the latest status message
  itself (with a sequence number), so it is also sent once on connect, after
  presence; a client that falls behind gets only the newest one.
One asyncio.Event wakes the client's generator for any of the above.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections import deque
from collections.abc import AsyncIterator, Callable
from typing import Any

from starlette.concurrency import run_in_threadpool

log = logging.getLogger(__name__)

PING = ": ping\n\n"


def sse_message(event: str, payload: Any) -> str:
    """One SSE message; `json.dumps` never emits raw newlines, so `data:` is a single line."""
    data = json.dumps(payload, separators=(",", ":"))
    return f"event: {event}\ndata: {data}\n\n"


def _draft_event(draft: dict[str, Any]) -> tuple[int, str]:
    revision = int(draft.get("revision", 0))
    return revision, sse_message("draft", {"revision": revision, "full": True, "changes": draft})


class _Client:
    __slots__ = ("wake", "closed", "state", "state_revision", "drafts", "draft_revision",
                 "needs_full_draft", "presence", "tm_pending", "tm_seq")

    def __init__(self) -> None:
        self.wake = asyncio.Event()
        self.closed = False
        self.state: tuple[int, str] | None = None  # latest undelivered state
        self.state_revision = -1  # last state revision sent
        self.drafts: deque[tuple[int, str]] = deque()
        self.draft_revision = -1  # last draft revision sent
        self.needs_full_draft = False
        self.presence: str | None = None
        self.tm_pending = False  # a newer TM status than tm_seq was published
        self.tm_seq = -1  # sequence number of the last TM status sent

    def has_pending(self) -> bool:
        return bool(
            self.closed
            or self.state
            or self.drafts
            or self.needs_full_draft
            or self.presence
            or self.tm_pending
        )


class LiveHub:
    def __init__(
        self,
        *,
        ping_seconds: float = 15.0,
        draft_queue_limit: int = 256,
        retry_ms: int = 2000,
    ) -> None:
        self.ping_seconds = ping_seconds
        self.draft_queue_limit = draft_queue_limit
        self.retry_ms = retry_ms
        self._loop: asyncio.AbstractEventLoop | None = None
        self._clients: set[_Client] = set()
        self._client_count = 0  # mirrors len(_clients); read from worker threads
        self._closed = False
        self._tm_lock = threading.Lock()
        self._tm: tuple[int, str] | None = None  # (sequence, message) of the latest TM status

    # ------------------------------------------------------------ lifecycle

    def attach(self, loop: asyncio.AbstractEventLoop) -> None:
        """Bind to the server's event loop (lifespan startup). Re-opens a closed hub."""
        self._loop = loop
        self._closed = False

    @property
    def client_count(self) -> int:
        return self._client_count

    def close(self) -> None:
        """End every open stream (loop thread). New streams end immediately until re-attached."""
        self._closed = True
        for client in list(self._clients):
            client.closed = True
            client.wake.set()

    def close_threadsafe(self) -> None:
        """`close()` from any thread or a signal handler."""
        self._closed = True
        self._call_soon(self.close)

    # ------------------------------------------------------- publish (any thread)

    def publish_state(self, view: dict[str, Any]) -> None:
        if not self._client_count:
            return  # nobody listening; a new client reads a fresh snapshot anyway
        revision = int(view.get("revision", 0))
        self._call_soon(self._deliver_state, revision, sse_message("state", view))

    def publish_draft(self, event: dict[str, Any]) -> None:
        """`event` = {"revision", "full", "changes"} as broadcast to clients."""
        if not self._client_count:
            return
        revision = int(event.get("revision", 0))
        message = sse_message("draft", event)
        self._call_soon(self._deliver_draft, revision, message, bool(event.get("full")))

    def publish_tm(self, status: dict[str, Any]) -> None:
        """Latest Tournament Manager status; kept even with no clients (sent on connect)."""
        message = sse_message("tm", status)
        with self._tm_lock:
            seq = self._tm[0] + 1 if self._tm else 0
            self._tm = (seq, message)
        if self._client_count:
            self._call_soon(self._deliver_tm, seq)

    def _call_soon(self, callback: Callable[..., None], *args: Any) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(callback, *args)
        except RuntimeError:  # loop closed between the check and the call
            pass

    # ---------------------------------------------------------- deliver (loop)

    def _deliver_state(self, revision: int, message: str) -> None:
        for client in self._clients:
            pending = client.state
            if revision > client.state_revision and (pending is None or revision > pending[0]):
                client.state = (revision, message)
                client.wake.set()

    def _deliver_draft(self, revision: int, message: str, full: bool) -> None:
        for client in self._clients:
            if revision <= client.draft_revision:
                continue
            if full:
                client.drafts.clear()
                client.needs_full_draft = False
            elif client.needs_full_draft:
                continue  # the pending full draft is read later and includes this
            client.drafts.append((revision, message))
            if len(client.drafts) > self.draft_queue_limit:
                client.drafts.clear()
                client.needs_full_draft = True
            client.wake.set()

    def _deliver_tm(self, seq: int) -> None:
        for client in self._clients:
            if seq > client.tm_seq:
                client.tm_pending = True
                client.wake.set()

    def _take_tm(self, client: _Client) -> str | None:
        """The latest TM status message if `client` has not been sent it yet."""
        client.tm_pending = False
        latest = self._tm
        if latest is None or latest[0] <= client.tm_seq:
            return None
        client.tm_seq = latest[0]
        return latest[1]

    def _broadcast_presence(self) -> None:
        message = sse_message("presence", {"clients": len(self._clients)})
        for client in self._clients:
            client.presence = message
            client.wake.set()

    def _register(self, client: _Client) -> None:
        self._clients.add(client)
        self._client_count = len(self._clients)
        self._broadcast_presence()

    def _unregister(self, client: _Client) -> None:
        if client not in self._clients:
            return
        self._clients.discard(client)
        self._client_count = len(self._clients)
        if not self._closed:
            self._broadcast_presence()

    # ---------------------------------------------------------------- stream

    async def stream(
        self,
        state_snapshot: Callable[[], dict[str, Any]],
        draft_snapshot: Callable[[], dict[str, Any]],
    ) -> AsyncIterator[str]:
        """SSE body for one client. Ends on hub close; cleans up on disconnect/cancel."""
        if self._loop is None:
            self.attach(asyncio.get_running_loop())
        if self._closed:
            return
        client = _Client()
        # Subscribe before reading snapshots: anything committed after the
        # snapshot is then queued, and anything already in it is skipped by revision.
        self._register(client)
        try:
            yield f"retry: {self.retry_ms}\n\n"

            def initial() -> tuple[int, str, int, str]:
                view = state_snapshot()
                state_rev = int(view.get("revision", 0))
                return (state_rev, sse_message("state", view), *_draft_event(draft_snapshot()))

            state_rev, state_msg, draft_rev, draft_msg = await run_in_threadpool(initial)
            if client.state and client.state[0] <= state_rev:
                client.state = None
            client.state_revision = state_rev
            yield state_msg
            client.draft_revision = draft_rev
            yield draft_msg
            if client.presence is not None:  # set by _register (unless a newer count replaced it)
                message, client.presence = client.presence, None
                yield message
            tm_message = self._take_tm(client)
            if tm_message is not None:
                yield tm_message

            while True:
                if not client.has_pending():
                    client.wake.clear()
                    try:
                        await asyncio.wait_for(client.wake.wait(), timeout=self.ping_seconds)
                    except TimeoutError:
                        yield PING
                        continue
                if client.closed:
                    return

                if client.state is not None:
                    revision, message = client.state
                    client.state = None
                    if revision > client.state_revision:
                        client.state_revision = revision
                        yield message

                if client.needs_full_draft:
                    client.needs_full_draft = False
                    client.drafts.clear()
                    revision, message = await run_in_threadpool(lambda: _draft_event(draft_snapshot()))
                    if revision >= client.draft_revision:
                        client.draft_revision = revision
                        yield message

                while client.drafts and not client.needs_full_draft:
                    revision, message = client.drafts.popleft()
                    if revision > client.draft_revision:
                        client.draft_revision = revision
                        yield message

                if client.presence is not None:
                    message, client.presence = client.presence, None
                    yield message

                if client.tm_pending:
                    tm_message = self._take_tm(client)
                    if tm_message is not None:
                        yield tm_message
        finally:
            # Runs on disconnect (cancellation), aclose() and hub shutdown. No awaits here.
            self._unregister(client)
