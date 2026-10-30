"""SSE hub: unit tests on the hub, plus one end-to-end test against a real uvicorn server."""
from __future__ import annotations

import asyncio
import json
import random
import signal
import socket
import threading
import time
from collections.abc import Iterator

import httpx
import uvicorn

from judging.__main__ import LanServer
from judging.live import PING, LiveHub
from judging.main import create_app

WAIT = 2.0  # seconds; every await in these tests is bounded


def parse(message: str) -> tuple[str, dict]:
    fields = dict(line.split(": ", 1) for line in message.strip().splitlines())
    return fields["event"], json.loads(fields["data"])


class Snapshots:
    """Stand-ins for StateStore.view / DraftStore.view."""

    def __init__(self) -> None:
        self.state = {"revision": 0}
        self.draft = {"revision": 0, "judge_pairs": "4"}


async def next_message(gen) -> str:
    return await asyncio.wait_for(gen.__anext__(), WAIT)


async def connect(hub: LiveHub, snaps: Snapshots):
    gen = hub.stream(lambda: snaps.state, lambda: snaps.draft)
    assert await next_message(gen) == "retry: 2000\n\n"
    initial = [parse(await next_message(gen)) for _ in range(3)]
    return gen, initial


def run(coro) -> None:
    asyncio.run(asyncio.wait_for(coro, 10))


def in_thread(fn, *args) -> None:
    thread = threading.Thread(target=fn, args=args)
    thread.start()
    thread.join(WAIT)
    assert not thread.is_alive()


def test_initial_messages_and_publish_from_worker_thread():
    async def main():
        hub, snaps = LiveHub(), Snapshots()
        hub.attach(asyncio.get_running_loop())
        gen, initial = await connect(hub, snaps)
        assert initial == [
            ("state", {"revision": 0}),
            ("draft", {"revision": 0, "full": True, "changes": {"revision": 0, "judge_pairs": "4"}}),
            ("presence", {"clients": 1}),
        ]
        in_thread(hub.publish_state, {"revision": 1, "locked": True})
        assert parse(await next_message(gen)) == ("state", {"revision": 1, "locked": True})
        event = {"revision": 1, "full": False, "changes": {"judge_pairs": "5"}}
        in_thread(hub.publish_draft, event)
        assert parse(await next_message(gen)) == ("draft", event)
        await gen.aclose()
        assert hub.client_count == 0

    run(main())


def test_state_is_coalesced_and_ping_is_sent():
    async def main():
        hub, snaps = LiveHub(ping_seconds=0.2), Snapshots()
        hub.attach(asyncio.get_running_loop())
        gen, _ = await connect(hub, snaps)

        def burst():
            for revision in range(1, 51):
                hub.publish_state({"revision": revision})

        in_thread(burst)
        await asyncio.sleep(0.05)  # let the loop run every delivery callback
        assert parse(await next_message(gen)) == ("state", {"revision": 50})
        assert await next_message(gen) == PING  # no backlog of older states
        # Stale or duplicate revisions are never re-sent.
        in_thread(hub.publish_state, {"revision": 50})
        in_thread(hub.publish_state, {"revision": 7})
        assert await next_message(gen) == PING
        await gen.aclose()

    run(main())


def test_draft_patches_in_order_and_overflow_sends_one_full_draft():
    async def main():
        hub, snaps = LiveHub(ping_seconds=0.2, draft_queue_limit=3), Snapshots()
        hub.attach(asyncio.get_running_loop())
        gen, _ = await connect(hub, snaps)

        def patches(revisions):
            for revision in revisions:
                hub.publish_draft({"revision": revision, "full": False, "changes": {"n": revision}})

        in_thread(patches, range(1, 4))  # fits the queue: delivered in order
        await asyncio.sleep(0.05)
        assert [parse(await next_message(gen))[1]["revision"] for _ in range(3)] == [1, 2, 3]

        snaps.draft = {"revision": 20, "judge_pairs": "2"}
        in_thread(patches, range(4, 21))  # overflows: replaced by a single full draft
        await asyncio.sleep(0.05)
        assert parse(await next_message(gen)) == (
            "draft", {"revision": 20, "full": True, "changes": {"revision": 20, "judge_pairs": "2"}}
        )
        assert await next_message(gen) == PING
        await gen.aclose()

    run(main())


def test_presence_counts_and_close_ends_streams():
    async def main():
        hub, snaps = LiveHub(), Snapshots()
        hub.attach(asyncio.get_running_loop())
        first, initial = await connect(hub, snaps)
        assert initial[2] == ("presence", {"clients": 1})
        second, initial = await connect(hub, snaps)
        assert initial[2] == ("presence", {"clients": 2})
        assert parse(await next_message(first)) == ("presence", {"clients": 2})
        await second.aclose()
        assert parse(await next_message(first)) == ("presence", {"clients": 1})

        hub.close()
        try:
            await next_message(first)
        except StopAsyncIteration:
            pass
        else:
            raise AssertionError("stream should end when the hub closes")
        assert hub.client_count == 0

    run(main())


def test_publish_without_loop_or_clients_is_a_noop():
    hub = LiveHub()
    hub.publish_state({"revision": 1})  # never attached
    hub.publish_draft({"revision": 1, "full": False, "changes": {}})


# ------------------------------------------------------------------- end to end


def sse_events(lines: Iterator[str]) -> Iterator[tuple[str, dict]]:
    event, data = None, None
    for line in lines:
        if line == "":
            if event is not None:
                yield event, json.loads(data)
            event, data = None, None
        elif line.startswith("event: "):
            event = line[len("event: "):]
        elif line.startswith("data: "):
            data = line[len("data: "):]


def admin_cookies(base: str, pin: str) -> httpx.Cookies:
    """Session cookie for a real server, so httpx clients can call admin-only routes."""
    response = httpx.post(f"{base}/api/admin/login", json={"pin": pin}, timeout=5)
    assert response.status_code == 200, response.text
    return response.cookies


def test_sse_end_to_end_with_real_server(settings, generate_payload):
    app = create_app(settings, rng=random.Random(3))
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    config = uvicorn.Config(app, log_level="warning", timeout_graceful_shutdown=2)
    server = LanServer(config, app.state.hub)
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started:
            assert time.monotonic() < deadline, "server did not start"
            assert thread.is_alive(), "server thread died"
            time.sleep(0.02)

        base = f"http://127.0.0.1:{port}"
        cookies = admin_cookies(base, settings.admin_pin)
        with httpx.Client(base_url=base, timeout=5, cookies=cookies) as http, http.stream("GET", "/api/events") as stream:
            assert stream.status_code == 200
            assert stream.headers["content-type"].startswith("text/event-stream")
            assert stream.headers["cache-control"] == "no-cache"
            assert stream.headers["x-accel-buffering"] == "no"
            events = sse_events(stream.iter_lines())

            assert next(events) == ("state", {"revision": 0})
            kind, draft = next(events)
            assert kind == "draft" and draft["full"] is True and draft["changes"]["judge_pairs"] == "4"
            assert next(events) == ("presence", {"clients": 1})
            kind, tm = next(events)  # always sent, so the page knows whether TM is set up
            assert kind == "tm" and tm["configured"] is False

            assert http.post("/api/generate", json=generate_payload).status_code == 200
            kind, state = next(events)
            assert kind == "state" and state["revision"] == 1
            assert state["slots"] and "draft" not in state

            result = http.patch("/api/draft", json={"judge_pairs": "6"}).json()
            assert next(events) == ("draft", {"revision": result["revision"], "full": False, "changes": result["changes"]})

            # Ctrl+C with this stream still open: the stream ends and the server stops promptly.
            started = time.monotonic()
            server.handle_exit(signal.SIGINT, None)
            assert list(events) == []
        thread.join(5)
        assert not thread.is_alive()
        assert time.monotonic() - started < 3
    finally:
        server.should_exit = True
        thread.join(5)
