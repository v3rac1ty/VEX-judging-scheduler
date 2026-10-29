"""Request body cap: 413 on oversize Content-Length and on oversize chunked bodies."""
from __future__ import annotations

import asyncio
import json
import random
import socket
import threading
import time

import httpx
import uvicorn

from judging.__main__ import LanServer
from judging.body_limit import MAX_BODY_BYTES, BodySizeLimitMiddleware
from judging.main import create_app
from test_live import sse_events

TOO_LARGE = {"detail": "Request too large."}


def oversize_json() -> bytes:
    return json.dumps({"judge_pairs": "x" * (MAX_BODY_BYTES + 1)}).encode()


def test_content_length_over_cap_is_413(client):
    response = client.patch(
        "/api/draft", content=oversize_json(), headers={"content-type": "application/json"}
    )
    assert response.status_code == 413
    assert response.json() == TOO_LARGE
    assert client.get("/api/draft").json()["revision"] == 0


def test_chunked_body_over_cap_is_413(client):
    body = oversize_json()

    def chunks():  # a generator body is sent chunked, with no Content-Length
        for i in range(0, len(body), 1 << 20):
            yield body[i:i + (1 << 20)]

    response = client.patch("/api/draft", content=chunks(), headers={"content-type": "application/json"})
    assert response.status_code == 413
    assert response.json() == TOO_LARGE
    assert client.get("/api/draft").json()["revision"] == 0


def test_body_under_cap_passes(client):
    response = client.patch("/api/draft", json={"judge_pairs": "6"})
    assert response.status_code == 200, response.text


def test_streaming_body_stops_being_read_at_the_cap():
    """Pure ASGI: the app sees a disconnect once the cap is crossed; the rest is never pulled."""
    seen: list[dict] = []

    async def app(scope, receive, send):
        while True:
            message = await receive()
            seen.append(message)
            if message["type"] == "http.disconnect" or not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    pulled = 0

    async def receive():
        nonlocal pulled
        pulled += 1
        return {"type": "http.request", "body": b"x" * 4, "more_body": True}

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/", "headers": [(b"transfer-encoding", b"chunked")]}
    asyncio.run(BodySizeLimitMiddleware(app, max_bytes=10)(scope, receive, send))

    assert pulled == 3  # 4 + 4 + 4 > 10
    assert seen[-1] == {"type": "http.disconnect"}
    assert [m.get("status") for m in sent if m["type"] == "http.response.start"] == [413]
    assert json.loads(sent[-1]["body"]) == TOO_LARGE


def test_non_http_scopes_pass_through():
    called: list[str] = []

    async def app(scope, receive, send):
        called.append(scope["type"])

    asyncio.run(BodySizeLimitMiddleware(app, max_bytes=1)({"type": "lifespan"}, None, None))
    assert called == ["lifespan"]


def test_real_server_rejects_chunked_upload_and_sse_still_streams(settings):
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
        with httpx.Client(base_url=base, timeout=5) as http, http.stream("GET", "/api/events") as stream:
            assert stream.status_code == 200
            events = sse_events(stream.iter_lines())
            assert [kind for kind, _ in (next(events) for _ in range(3))] == ["state", "draft", "presence"]

            body = oversize_json()

            def chunks():
                for i in range(0, len(body), 1 << 16):
                    yield body[i:i + (1 << 16)]

            with httpx.Client(base_url=base, timeout=5) as uploader:
                try:
                    response = uploader.patch(
                        "/api/draft", content=chunks(), headers={"content-type": "application/json"}
                    )
                except (httpx.WriteError, httpx.RemoteProtocolError, httpx.ReadError):
                    response = None  # server hung up mid-upload: also a rejection
                if response is not None:
                    assert response.status_code == 413
                    assert response.json() == TOO_LARGE

                # The event stream is unaffected and still delivers updates.
                assert uploader.patch("/api/draft", json={"judge_pairs": "6"}).status_code == 200
            assert next(events) == ("draft", {"revision": 1, "full": False, "changes": {"judge_pairs": "6"}})
            assert app.state.draft.view()["judge_pairs"] == "6"
    finally:
        server.should_exit = True
        thread.join(5)
        assert not thread.is_alive()

