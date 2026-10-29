"""Pure-ASGI request body cap (413 "Request too large.").

Rejects up front when Content-Length is over the limit, and otherwise counts
bytes as the body streams in (chunked uploads), answering 413 the moment the
cap is crossed. Requests without a body -- including the `GET /api/events`
stream -- pass straight through.
"""
from __future__ import annotations

import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_BODY_BYTES = 12 * 1024 * 1024

_DETAIL = json.dumps({"detail": "Request too large."}).encode()


async def _send_413(send: Send) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(_DETAIL)).encode()),
                (b"connection", b"close"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": _DETAIL})


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        for name, value in scope.get("headers", ()):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    break  # the server rejects malformed lengths; fall back to counting
                if declared > self.max_bytes:
                    await _send_413(send)
                    return
                break

        received = 0
        rejected = False
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received, rejected
            if rejected:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    rejected = True
                    if not response_started:
                        await _send_413(send)
                    # Tell the app the client went away so it stops reading.
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal response_started
            if rejected:
                return  # our 413 already answered; drop the app's error response
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except Exception:
            if not rejected:
                raise
            # The app failed on the truncated body after we answered 413; nothing to report.
