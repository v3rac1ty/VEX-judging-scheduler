"""Tournament Manager source: client (token, signing, 304, errors), service, poller and routes.

Nothing here touches the real network: the client runs on a fake transport, or on a
loopback http.server for the stdlib transport, and the app runs on a fake `TMSource`.
"""
from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import hmac
import json
import logging
import random
import re
import signal
import socket
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from judging.__main__ import LanServer, banner
from judging.draft import DraftStore
from judging.main import create_app
from judging.settings import Settings
from judging.store import StateStore
from judging.tm import (
    API_DISABLED,
    AUTH_UNEXPECTED,
    AUTH_UNREACHABLE,
    CREDENTIALS_REJECTED,
    NOT_CONFIGURED,
    UNEXPECTED_RESPONSE,
    HTTPRequest,
    HTTPResponse,
    TMClient,
    TMError,
    TMService,
    canonical_matches,
    sign,
    string_to_sign,
    urllib_transport,
)
from conftest import ADMIN_PIN, sign_in
from test_live import admin_cookies, next_message, parse

BASE = "http://tm.test:8080"
AUTH = "https://auth.example.test/oauth2/token"
CLIENT_ID = "tm-client-id-4c1e"
CLIENT_SECRET = "tm-client-secret-Zq81xP"
API_KEY = "tm-api-key-77Ac90"
TOKEN_PREFIX = "tok-Hk3q-"  # FakeTM issues tok-Hk3q-1, tok-Hk3q-2, ...
SECRETS = (CLIENT_ID, CLIENT_SECRET, API_KEY, TOKEN_PREFIX)
LAST_MODIFIED = "Thu, 29 Oct 2026 15:00:00 GMT"
TM_NAME = "Tournament Manager · Division 3"


class FakeClock:
    def __init__(self, now: float = 1_793_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def independent_signature(request: HTTPRequest) -> str:
    """The guide's scheme, computed from scratch: HMAC-SHA256(API key, five newline-ended lines)."""
    parts = urlsplit(request.url)
    path = parts.path + (f"?{parts.query}" if parts.query else "")
    token = request.headers["Authorization"].removeprefix("Bearer ")
    message = (
        b"GET\n"
        + path.encode()
        + b"\ntoken:" + token.encode()
        + b"\nhost:" + request.headers["Host"].encode()
        + b"\nx-tm-date:" + request.headers["x-tm-date"].encode()
        + b"\n"
    )
    return hmac.new(API_KEY.encode(), message, hashlib.sha256).hexdigest()


class FakeTM:
    """Transport standing in for both the DWAB token endpoint and TM itself."""

    def __init__(self, body: bytes, clock: FakeClock) -> None:
        self.body = body
        self.clock = clock
        self.last_modified: str | None = LAST_MODIFIED
        self.requests: list[HTTPRequest] = []
        self.valid_tokens: set[str] = set()
        self.issued = 0
        self.expires_in: object = 3600
        self.token_error: OSError | None = None
        self.token_response: HTTPResponse | None = None
        self.tm_error: OSError | None = None
        self.tm_response: HTTPResponse | None = None

    def __call__(self, request: HTTPRequest) -> HTTPResponse:
        self.requests.append(request)
        if request.url == AUTH:
            if self.token_error:
                raise self.token_error
            if self.token_response:
                return self.token_response
            self.issued += 1
            token = f"{TOKEN_PREFIX}{self.issued}"
            self.valid_tokens.add(token)
            payload = {"access_token": token, "token_type": "Bearer", "expires_in": self.expires_in}
            return HTTPResponse(200, {"content-type": "application/json"}, json.dumps(payload).encode())
        if self.tm_error:
            raise self.tm_error
        if self.tm_response:
            return self.tm_response
        assert request.headers["x-tm-signature"] == independent_signature(request)
        if request.headers["Authorization"].removeprefix("Bearer ") not in self.valid_tokens:
            return HTTPResponse(401, {})
        if self.last_modified and request.headers.get("If-Modified-Since") == self.last_modified:
            return HTTPResponse(304, {})
        headers = {"last-modified": self.last_modified} if self.last_modified else {}
        return HTTPResponse(200, headers, self.body)

    @property
    def token_requests(self) -> list[HTTPRequest]:
        return [r for r in self.requests if r.url == AUTH]

    @property
    def tm_requests(self) -> list[HTTPRequest]:
        return [r for r in self.requests if r.url != AUTH]


class FakeSource:
    """A `TMSource`; `release` (when set) makes `matches` block until it is set."""

    def __init__(self, body: str) -> None:
        self.body = body
        self.calls = 0
        self.error: Exception | None = None
        self.entered = threading.Event()
        self.release: threading.Event | None = None

    def matches(self, division: int) -> str:
        self.calls += 1
        self.division = division
        self.entered.set()
        if self.release is not None:
            self.release.wait(10)
        if self.error is not None:
            raise self.error
        return self.body


# --------------------------------------------------------------------- fixtures


@pytest.fixture
def tm_body(matches) -> str:
    return json.dumps({"matches": matches})  # the guide's shape: {"matches": [...]}


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def fake_tm(tm_body, clock) -> FakeTM:
    return FakeTM(tm_body.encode(), clock)


@pytest.fixture
def tm_client(fake_tm, clock) -> TMClient:
    return TMClient(BASE, CLIENT_ID, CLIENT_SECRET, API_KEY, auth_url=AUTH, transport=fake_tm, clock=clock)


@pytest.fixture
def tm_settings(settings) -> Settings:
    return dataclasses.replace(
        settings,
        tm_url=BASE,
        tm_client_id=CLIENT_ID,
        tm_client_secret=CLIENT_SECRET,
        tm_api_key=API_KEY,
        tm_division=3,
        tm_auth_url=AUTH,
    )


@pytest.fixture
def source(tm_body) -> FakeSource:
    return FakeSource(tm_body)


@pytest.fixture
def tm_app_client(tm_settings, source) -> TestClient:
    return sign_in(TestClient(create_app(tm_settings, rng=random.Random(5), tm_source=source)))


def make_service(settings: Settings, source, **kwargs) -> TMService:
    return TMService(settings, DraftStore(settings), StateStore(settings), source=source, **kwargs)


def draft_of(client: TestClient) -> dict:
    return client.get("/api/draft").json()


def tm_status(client: TestClient) -> dict:
    return client.app.state.tm.status()


def poll(client: TestClient) -> None:
    """One poller tick, as if the interval had passed."""
    client.app.state.tm.poll_once()


# ------------------------------------------------------------------ signing


def test_string_to_sign_is_five_newline_terminated_lines():
    date = "Thu, 29 Oct 2026 15:04:05 GMT"
    text = string_to_sign("GET", "/api/matches/1?x=1", "abc", "192.168.1.50:8080", date)
    assert text.encode() == (
        b"GET\n/api/matches/1?x=1\ntoken:abc\nhost:192.168.1.50:8080\n"
        b"x-tm-date:Thu, 29 Oct 2026 15:04:05 GMT\n"
    )
    expected = hmac.new(b"k3y", text.encode(), hashlib.sha256).hexdigest()
    assert sign("k3y", text) == expected


@pytest.mark.parametrize(
    ("base", "host", "path"),
    [
        (BASE, "tm.test:8080", "/api/matches/3"),
        ("http://192.168.1.50", "192.168.1.50", "/api/matches/3"),
        ("http://tm.test:8080/proxy/", "tm.test:8080", "/proxy/api/matches/3"),
    ],
)
def test_requests_carry_signed_headers(fake_tm, clock, base, host, path):
    client = TMClient(base, CLIENT_ID, CLIENT_SECRET, API_KEY, auth_url=AUTH, transport=fake_tm, clock=clock)
    client.matches(3)
    (request,) = fake_tm.tm_requests
    assert request.method == "GET"
    assert urlsplit(request.url).path == path
    assert request.timeout == 5.0
    assert request.headers["Host"] == host
    assert request.headers["Authorization"] == f"Bearer {TOKEN_PREFIX}1"
    assert request.headers["x-tm-date"] == formatdate(clock.now, usegmt=True)
    assert re.fullmatch(r"\w{3}, \d{2} \w{3} \d{4} \d{2}:\d{2}:\d{2} GMT", request.headers["x-tm-date"])
    assert request.headers["x-tm-signature"] == independent_signature(request)
    assert "If-Modified-Since" not in request.headers


def test_token_request_is_client_credentials_form(tm_client, fake_tm):
    tm_client.matches(1)
    (request,) = fake_tm.token_requests
    assert request.method == "POST" and request.timeout == 5.0
    assert request.headers["Content-Type"] == "application/x-www-form-urlencoded"
    assert parse_qs(request.body.decode()) == {
        "grant_type": ["client_credentials"],
        "client_id": [CLIENT_ID],
        "client_secret": [CLIENT_SECRET],
    }


# -------------------------------------------------------------- token cache


def test_token_is_cached_until_a_minute_before_expiry(tm_client, fake_tm, clock):
    tm_client.matches(1)
    clock.now += 3600 - 61
    tm_client.matches(1)
    assert len(fake_tm.token_requests) == 1
    clock.now += 1  # now within 60 s of expiry: renew first
    tm_client.matches(1)
    assert len(fake_tm.token_requests) == 2
    assert fake_tm.tm_requests[-1].headers["Authorization"] == f"Bearer {TOKEN_PREFIX}2"


def test_short_lived_token_is_renewed_every_time(tm_client, fake_tm):
    fake_tm.expires_in = 30
    tm_client.matches(1)
    tm_client.matches(1)
    assert len(fake_tm.token_requests) == 2


def test_401_renews_the_token_and_retries_once(tm_client, fake_tm, tm_body):
    tm_client.matches(1)
    fake_tm.valid_tokens.clear()  # TM restarted: the cached token is no longer accepted
    assert tm_client.matches(1) == tm_body
    assert len(fake_tm.token_requests) == 2
    assert len(fake_tm.tm_requests) == 3

    fake_tm.tm_response = HTTPResponse(401, {})
    with pytest.raises(TMError) as caught:
        tm_client.matches(1)
    assert caught.value.message == CREDENTIALS_REJECTED
    assert len(fake_tm.tm_requests) == 5  # one retry, not a loop


# ---------------------------------------------------------------------- 304


def test_if_modified_since_and_304_return_the_cached_body(tm_client, fake_tm, tm_body):
    assert tm_client.matches(1) == tm_body
    assert tm_client.matches(1) == tm_body
    first, second = fake_tm.tm_requests
    assert "If-Modified-Since" not in first.headers
    assert second.headers["If-Modified-Since"] == LAST_MODIFIED

    fake_tm.body, fake_tm.last_modified = b"[]", "Thu, 29 Oct 2026 15:10:00 GMT"
    assert tm_client.matches(1) == "[]"
    tm_client.matches(1)
    assert fake_tm.tm_requests[-1].headers["If-Modified-Since"] == fake_tm.last_modified


def test_no_last_modified_means_no_conditional_request(tm_client, fake_tm):
    fake_tm.last_modified = None
    tm_client.matches(1)
    tm_client.matches(1)
    assert all("If-Modified-Since" not in r.headers for r in fake_tm.tm_requests)


# ------------------------------------------------------------- error mapping

ERROR_CASES: list[tuple[str, Callable[[FakeTM], None], str]] = [
    ("tm refused", lambda f: setattr(f, "tm_error", ConnectionRefusedError(61, "refused")), f"TM not reachable at {BASE}"),
    ("tm timeout", lambda f: setattr(f, "tm_error", TimeoutError("timed out")), f"TM not reachable at {BASE}"),
    ("auth offline", lambda f: setattr(f, "token_error", OSError("no route")), AUTH_UNREACHABLE),
    ("auth 401", lambda f: setattr(f, "token_response", HTTPResponse(401, {}, b'{"error":"invalid_client"}')), CREDENTIALS_REJECTED),
    ("auth 400", lambda f: setattr(f, "token_response", HTTPResponse(400, {}, b"{}")), CREDENTIALS_REJECTED),
    ("auth 500", lambda f: setattr(f, "token_response", HTTPResponse(500, {}, b"oops")), AUTH_UNEXPECTED),
    ("auth not json", lambda f: setattr(f, "token_response", HTTPResponse(200, {}, b"<html>")), AUTH_UNEXPECTED),
    ("auth no token", lambda f: setattr(f, "token_response", HTTPResponse(200, {}, b'{"expires_in": 60}')), AUTH_UNEXPECTED),
    ("auth bad token", lambda f: setattr(f, "token_response", HTTPResponse(200, {}, b'{"access_token": "a\\r\\nb"}')), AUTH_UNEXPECTED),
    ("auth bad expiry", lambda f: setattr(f, "expires_in", "soon"), AUTH_UNEXPECTED),
    ("tm 403", lambda f: setattr(f, "tm_response", HTTPResponse(403, {})), API_DISABLED),
    ("tm 404", lambda f: setattr(f, "tm_response", HTTPResponse(404, {})), "TM has no division 3 (or its API is not enabled: Tools > Options > Web Publishing)"),
    ("tm 500", lambda f: setattr(f, "tm_response", HTTPResponse(500, {}, b"boom")), UNEXPECTED_RESPONSE),
    ("tm redirect", lambda f: setattr(f, "tm_response", HTTPResponse(302, {"location": "http://evil.test/"})), UNEXPECTED_RESPONSE),
    ("tm 304 uncached", lambda f: setattr(f, "tm_response", HTTPResponse(304, {})), UNEXPECTED_RESPONSE),
    ("tm huge", lambda f: setattr(f, "tm_response", HTTPResponse(200, {}, b" " * (5 * 1024 * 1024 + 1))), UNEXPECTED_RESPONSE),
    ("tm not utf8", lambda f: setattr(f, "tm_response", HTTPResponse(200, {}, b"\xff\xfe\xfa")), UNEXPECTED_RESPONSE),
]


@pytest.mark.parametrize(("_name", "breaks", "message"), ERROR_CASES, ids=[c[0] for c in ERROR_CASES])
def test_failures_map_to_safe_messages(tm_client, fake_tm, _name, breaks, message):
    breaks(fake_tm)
    with pytest.raises(TMError) as caught:
        tm_client.matches(3)
    assert caught.value.message == message == str(caught.value)
    exc = caught.value  # no chained exception whose text could hold details
    assert exc.__cause__ is None and (exc.__context__ is None or exc.__suppress_context__)


def test_canonical_matches_requires_a_match_list(tm_body):
    text = canonical_matches(tm_body)
    assert json.loads(text) == json.loads(tm_body) and "\n  " in text
    assert canonical_matches("[]") == "[]"
    for bad in ("<html>", '{"error": "nope"}', "[1, 2]", ""):
        with pytest.raises(TMError, match=UNEXPECTED_RESPONSE):
            canonical_matches(bad)


def test_no_secret_in_any_error_log_or_repr(fake_tm, clock, tm_settings, caplog):
    """Every failure path: message, str/repr, logs, request reprs, client and settings reprs."""
    seen: list[str] = [repr(tm_settings), str(tm_settings)]
    with caplog.at_level(logging.DEBUG):
        for _name, breaks, _message in ERROR_CASES:
            fake = FakeTM(fake_tm.body, clock)
            client = TMClient(BASE, CLIENT_ID, CLIENT_SECRET, API_KEY, auth_url=AUTH, transport=fake, clock=clock)
            breaks(fake)
            with pytest.raises(TMError) as caught:
                client.matches(3)
            exc = caught.value
            seen += [exc.message, str(exc), repr(exc), repr(exc.args), repr(client)]
            seen += [repr(r) for r in fake.requests]
    seen += [record.getMessage() for record in caplog.records]
    seen.append(caplog.text)
    for text in seen:
        for secret in SECRETS:
            assert secret not in text, (secret, text)


# ------------------------------------------------------- real stdlib transport


class _LoopbackTM(BaseHTTPRequestHandler):
    """Token endpoint + TM on 127.0.0.1, checking the signature over real HTTP."""

    seen: list[dict[str, str]] = []

    def log_message(self, *args) -> None:  # keep test output quiet
        pass

    def _reply(self, status: int, body: bytes = b"", headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        form = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
        ok = form.get("client_secret") == [CLIENT_SECRET]
        body = json.dumps({"access_token": f"{TOKEN_PREFIX}9", "token_type": "Bearer", "expires_in": 3600})
        self._reply(200 if ok else 401, body.encode() if ok else b"{}")

    def do_GET(self) -> None:
        headers = {name: self.headers[name] for name in self.headers}
        type(self).seen.append({"path": self.path, **headers})
        if self.path == "/redirect":
            self._reply(302, headers={"Location": "/api/matches/1"})
            return
        request = HTTPRequest("GET", f"http://x{self.path}", {
            "Host": self.headers["Host"],
            "Authorization": self.headers["Authorization"],
            "x-tm-date": self.headers["x-tm-date"],
        })
        if self.headers["x-tm-signature"] != independent_signature(request):
            self._reply(401)
        elif self.headers.get("If-Modified-Since") == LAST_MODIFIED:
            self._reply(304)
        else:
            self._reply(200, b'{"matches": []}', {"Last-Modified": LAST_MODIFIED})


@pytest.fixture
def loopback_tm():
    _LoopbackTM.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _LoopbackTM)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def test_stdlib_transport_end_to_end_on_loopback(loopback_tm):
    client = TMClient(loopback_tm, CLIENT_ID, CLIENT_SECRET, API_KEY, auth_url=f"{loopback_tm}/token")
    assert client.matches(1) == '{"matches": []}'
    assert client.matches(1) == '{"matches": []}'  # served from cache after a 304
    first, second = _LoopbackTM.seen
    assert first["Host"] == loopback_tm.removeprefix("http://")  # the Host that was signed
    assert second["If-Modified-Since"] == LAST_MODIFIED

    # Redirects are returned, never followed (they would replay the signed headers).
    headers = {"Host": "127.0.0.1"}
    response = urllib_transport(HTTPRequest("GET", f"{loopback_tm}/redirect", headers, timeout=2))
    assert response.status == 302 and len(_LoopbackTM.seen) == 3


def test_stdlib_transport_wrong_secret_and_closed_port(loopback_tm):
    client = TMClient(loopback_tm, CLIENT_ID, "wrong", API_KEY, auth_url=f"{loopback_tm}/token")
    with pytest.raises(TMError, match=CREDENTIALS_REJECTED):
        client.matches(1)

    with socket.socket() as probe:  # a port nothing listens on
        probe.bind(("127.0.0.1", 0))
        closed = f"http://127.0.0.1:{probe.getsockname()[1]}"
    with pytest.raises(OSError):
        urllib_transport(HTTPRequest("GET", f"{closed}/api/matches/1", {}, timeout=2))
    client = TMClient(closed, CLIENT_ID, CLIENT_SECRET, API_KEY, auth_url=f"{loopback_tm}/token")
    with pytest.raises(TMError, match=f"TM not reachable at {closed}"):
        client.matches(1)


# --------------------------------------------------------- not configured


def test_not_configured(client):
    response = client.get("/api/tm/status")
    assert response.status_code == 200
    assert response.json() == {
        "configured": False,
        "division": 1,
        "poll_seconds": 60,
        "polling": {"match_schedule": False, "final_schedule": False},
        "last_fetch": None,
        "last_change": None,
        "error": None,
    }
    for path, body in [
        ("/api/tm/fetch", {"target": "match_schedule"}),
        ("/api/tm/poll", {"target": "final_schedule", "enabled": True}),
    ]:
        response = client.post(path, json=body)
        assert response.status_code == 409
        assert response.json() == {"detail": NOT_CONFIGURED}


def test_partial_config_is_not_configured(settings):
    partial = dataclasses.replace(settings, tm_url=BASE, tm_api_key=API_KEY)
    app = create_app(partial)
    assert app.state.tm.configured is False and app.state.tm.source is None
    line = app.state.tm.startup_line()
    assert line == (
        "Tournament Manager: not configured (file upload only; "
        "missing JUDGING_TM_CLIENT_ID, JUDGING_TM_CLIENT_SECRET)"
    )
    assert API_KEY not in line


@pytest.mark.parametrize(
    ("path", "body", "detail"),
    [
        ("/api/tm/fetch", {"target": "bogus"}, 'target must be "match_schedule" or "final_schedule".'),
        ("/api/tm/fetch", {}, "target: Field required"),
        ("/api/tm/fetch", {"target": "match_schedule", "x": 1}, "Unknown field: x."),
        ("/api/tm/poll", {"target": "match_schedule"}, "enabled: Field required"),
        ("/api/tm/poll", {"target": "match_schedule", "enabled": "yes"}, "enabled must be true or false."),
    ],
)
def test_tm_bodies_are_validated(tm_app_client, path, body, detail):
    response = tm_app_client.post(path, json=body)
    assert response.status_code == 400
    assert response.json() == {"detail": detail}


# ------------------------------------------------------------ manual fetch


def test_fetch_patches_draft_like_an_upload(tm_app_client, source, tm_body, first_match):
    before = draft_of(tm_app_client)["revision"]
    response = tm_app_client.post("/api/tm/fetch", json={"target": "match_schedule"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["changed"] is True
    assert source.calls == 1 and source.division == 3

    draft = draft_of(tm_app_client)
    assert draft["revision"] == before + 1
    assert draft["match_schedule"] == {"text": canonical_matches(tm_body), "name": TM_NAME}
    assert draft["preview"]["match_count"] == 36 and draft["preview"]["team_count"] == 24
    assert draft["end_time"] == "11:00 AM" and draft["start_time"] == "10:00 AM"  # auto-filled
    assert draft["final_schedule"] == {"text": "", "name": None}

    tm = result["tm"]
    assert tm == tm_app_client.get("/api/tm/status").json()
    assert tm["configured"] is True and tm["division"] == 3 and tm["error"] is None
    assert tm["last_fetch"] and tm["last_change"]
    assert tm["last_fetch"].endswith(first_match.isoformat()[-6:])  # event timezone offset


def test_fetch_final_schedule_fills_final_preview(tm_app_client):
    result = tm_app_client.post("/api/tm/fetch", json={"target": "final_schedule"}).json()
    assert result["changed"] is True
    draft = draft_of(tm_app_client)
    assert draft["final_schedule"]["name"] == TM_NAME
    assert draft["final_preview"]["match_count"] == 36
    assert draft["match_schedule"]["text"] == "" and draft["preview"] is None


def test_unchanged_fetch_does_not_bump_the_revision(tm_app_client, source):
    tm_app_client.app.state.tm.manual_min_interval = 0
    tm_app_client.post("/api/tm/fetch", json={"target": "match_schedule"})
    revision = draft_of(tm_app_client)["revision"]
    result = tm_app_client.post("/api/tm/fetch", json={"target": "match_schedule"}).json()
    assert result["changed"] is False
    assert source.calls == 2
    assert draft_of(tm_app_client)["revision"] == revision


def test_fetch_replaces_a_dropped_file(tm_app_client, tm_body):
    tm_app_client.patch("/api/draft", json={"match_schedule": {"text": "[]", "name": "old.json"}})
    assert tm_app_client.post("/api/tm/fetch", json={"target": "match_schedule"}).json()["changed"] is True
    assert draft_of(tm_app_client)["match_schedule"]["text"] == canonical_matches(tm_body)


def test_manual_fetches_are_rate_limited_to_one_call_per_10_seconds(tm_settings, source):
    clock = FakeClock()
    service = make_service(tm_settings, source, clock=clock)
    service.fetch("match_schedule")
    clock.now += 9
    service.fetch("final_schedule")
    assert source.calls == 1  # reused, yet the second target was still filled
    assert service._draft.view()["final_schedule"]["name"] == TM_NAME
    clock.now += 1
    service.fetch("match_schedule")
    assert source.calls == 2


def test_tm_failure_is_502_with_safe_message_and_recorded(tm_app_client, source):
    source.error = TMError(f"TM not reachable at {BASE}")
    response = tm_app_client.post("/api/tm/fetch", json={"target": "match_schedule"})
    assert response.status_code == 502
    assert response.json() == {"detail": f"TM not reachable at {BASE}"}
    assert tm_status(tm_app_client)["error"] == f"TM not reachable at {BASE}"
    assert draft_of(tm_app_client)["revision"] == 0

    source.error = None
    response = tm_app_client.post("/api/tm/fetch", json={"target": "match_schedule"})
    assert response.status_code == 200 and response.json()["tm"]["error"] is None


def test_non_match_body_and_foreign_exceptions_are_unexpected(tm_app_client, source, caplog):
    tm_app_client.app.state.tm.manual_min_interval = 0
    source.body = "<html>not json</html>"
    response = tm_app_client.post("/api/tm/fetch", json={"target": "match_schedule"})
    assert (response.status_code, response.json()) == (502, {"detail": UNEXPECTED_RESPONSE})

    # A swapped-in library raising its own exception: its text (may hold secrets) is never shown.
    source.error = RuntimeError(f"401 for client_secret={CLIENT_SECRET}")
    with caplog.at_level(logging.DEBUG):
        response = tm_app_client.post("/api/tm/fetch", json={"target": "match_schedule"})
    assert (response.status_code, response.json()) == (502, {"detail": UNEXPECTED_RESPONSE})
    assert CLIENT_SECRET not in caplog.text and "RuntimeError" in caplog.text


def test_fetch_broadcasts_draft_and_tm_events(tm_settings, source):
    app = create_app(tm_settings, tm_source=source)
    hub, tm = app.state.hub, app.state.tm

    async def main():
        hub.attach(asyncio.get_running_loop())
        gen = hub.stream(app.state.store.view, app.state.draft.view)
        assert await next_message(gen) == "retry: 2000\n\n"
        initial = [parse(await next_message(gen)) for _ in range(4)]
        assert [kind for kind, _ in initial] == ["state", "draft", "presence", "tm"]
        assert initial[3][1] == tm.status()

        result = await asyncio.to_thread(tm.fetch, "match_schedule")
        latest: dict[str, dict] = {}
        while "draft" not in latest or latest.get("tm") != result["tm"]:
            kind, data = parse(await next_message(gen))
            latest[kind] = data
        assert latest["draft"]["full"] is False
        assert latest["draft"]["changes"]["match_schedule"]["name"] == TM_NAME
        assert "preview" in latest["draft"]["changes"]
        await gen.aclose()

    asyncio.run(asyncio.wait_for(main(), 10))


# ------------------------------------------------------------------ polling


def test_poll_toggle_and_status(tm_app_client):
    result = tm_app_client.post("/api/tm/poll", json={"target": "final_schedule", "enabled": True})
    assert result.status_code == 200
    assert result.json()["tm"]["polling"] == {"match_schedule": False, "final_schedule": True}
    result = tm_app_client.post("/api/tm/poll", json={"target": "final_schedule", "enabled": False})
    assert result.json()["tm"]["polling"] == {"match_schedule": False, "final_schedule": False}


def test_poller_patches_only_when_tm_changes_so_a_dropped_file_survives(tm_app_client, source, matches):
    tm_app_client.post("/api/tm/poll", json={"target": "match_schedule", "enabled": True})
    poll(tm_app_client)
    assert draft_of(tm_app_client)["match_schedule"]["name"] == TM_NAME

    # The admin drops a file: the next ticks see the same TM data and leave it alone.
    tm_app_client.patch("/api/draft", json={"match_schedule": {"text": "[]", "name": "mine.json"}})
    revision = draft_of(tm_app_client)["revision"]
    poll(tm_app_client)
    poll(tm_app_client)
    assert source.calls == 3
    assert draft_of(tm_app_client)["match_schedule"] == {"text": "[]", "name": "mine.json"}
    assert draft_of(tm_app_client)["revision"] == revision

    # TM's schedule really changes: the poller takes it.
    source.body = json.dumps({"matches": matches[:-1]})
    poll(tm_app_client)
    draft = draft_of(tm_app_client)
    assert draft["match_schedule"]["name"] == TM_NAME and draft["preview"]["match_count"] == 35
    assert draft["revision"] == revision + 1


def test_turning_auto_update_on_resyncs_with_tm(tm_app_client):
    tm_app_client.post("/api/tm/fetch", json={"target": "match_schedule"})
    tm_app_client.patch("/api/draft", json={"match_schedule": {"text": "[]", "name": "mine.json"}})
    tm_app_client.post("/api/tm/poll", json={"target": "match_schedule", "enabled": True})
    poll(tm_app_client)
    assert draft_of(tm_app_client)["match_schedule"]["name"] == TM_NAME


def test_poll_without_enabled_targets_does_nothing(tm_app_client, source):
    poll(tm_app_client)
    assert source.calls == 0


def test_poll_error_is_recorded_and_polling_continues(tm_app_client, source, caplog):
    tm_app_client.post("/api/tm/poll", json={"target": "match_schedule", "enabled": True})
    source.error = TMError(API_DISABLED)
    with caplog.at_level(logging.WARNING, logger="judging.tm"):
        poll(tm_app_client)
        poll(tm_app_client)
    assert tm_status(tm_app_client)["error"] == API_DISABLED
    assert tm_status(tm_app_client)["polling"]["match_schedule"] is True
    assert caplog.text.count(API_DISABLED) == 1  # logged when it starts, not every tick
    source.error = None
    poll(tm_app_client)
    assert tm_status(tm_app_client)["error"] is None
    assert draft_of(tm_app_client)["match_schedule"]["name"] == TM_NAME


def test_printing_stops_match_schedule_auto_update(tm_app_client, generate_payload):
    for target in ("match_schedule", "final_schedule"):
        tm_app_client.post("/api/tm/poll", json={"target": target, "enabled": True})
    assert tm_app_client.post("/api/generate", json=generate_payload).status_code == 200
    assert tm_status(tm_app_client)["polling"]["match_schedule"] is True
    assert tm_app_client.post("/api/snapshot-print", json={}).status_code == 200
    assert tm_status(tm_app_client)["polling"] == {"match_schedule": False, "final_schedule": True}

    response = tm_app_client.post("/api/tm/poll", json={"target": "match_schedule", "enabled": True})
    assert response.status_code == 409 and "already printed" in response.json()["detail"]
    # Manual fetches stay available, like a file drop.
    assert tm_app_client.post("/api/tm/fetch", json={"target": "match_schedule"}).status_code == 200


def test_noshow_lock_stops_final_schedule_auto_update(tm_settings, source):
    service = make_service(tm_settings, source)
    for target in ("match_schedule", "final_schedule"):
        service.set_polling(target, True)
    service.on_state({"revision": 3, "noshow_locked": True})
    assert service.status()["polling"] == {"match_schedule": True, "final_schedule": False}
    service.on_state({"revision": 4, "locked": True, "noshow_locked": True})
    assert service.status()["polling"] == {"match_schedule": False, "final_schedule": False}


def test_poll_tick_also_checks_locks(tm_settings, source):
    service = make_service(tm_settings, source)
    service.set_polling("match_schedule", True)
    service._store.update(lambda state: state.update(locked=True))  # no listener wired here
    service.poll_once()
    assert source.calls == 0
    assert service.status()["polling"]["match_schedule"] is False


def test_reset_turns_polling_off(tm_app_client, source):
    for target in ("match_schedule", "final_schedule"):
        tm_app_client.post("/api/tm/poll", json={"target": target, "enabled": True})
    source.error = TMError(API_DISABLED)
    poll(tm_app_client)
    assert tm_app_client.post("/api/reset").status_code == 200
    status = tm_status(tm_app_client)
    assert status["polling"] == {"match_schedule": False, "final_schedule": False}
    assert status["error"] is None and status["last_fetch"] is None
    source.error = None
    poll(tm_app_client)
    assert source.calls == 1


def test_seconds_until_poll(tm_settings, source):
    clock = FakeClock()
    service = make_service(tm_settings, source, clock=clock)
    assert service.seconds_until_poll() is None
    service.set_polling("match_schedule", True)
    assert service.seconds_until_poll() == 0
    service.poll_once()
    assert service.seconds_until_poll() == 60
    clock.now += 45
    assert service.seconds_until_poll() == 15
    service.fetch("final_schedule")  # a manual fetch also counts toward TM's once-a-minute
    assert service.seconds_until_poll() == 60


async def wait_until(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "condition not reached"
        await asyncio.sleep(0.01)


def test_poller_task_wakes_when_enabled_and_repeats_on_its_interval(tm_settings, source):
    async def main():
        service = make_service(tm_settings, source)  # 60 s interval
        task = asyncio.create_task(service.run_poller())
        await asyncio.sleep(0.1)
        assert source.calls == 0  # nothing enabled: idle
        await asyncio.to_thread(service.set_polling, "match_schedule", True)
        await wait_until(lambda: source.calls == 1)  # first poll right away
        await asyncio.sleep(0.1)
        assert source.calls == 1  # next one only after 60 s
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

        fast = make_service(tm_settings, source, poll_seconds=0.05)
        fast.set_polling("final_schedule", True)
        task = asyncio.create_task(fast.run_poller())
        await wait_until(lambda: source.calls >= 4)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        assert fast._draft.view()["final_schedule"]["name"] == TM_NAME

    asyncio.run(asyncio.wait_for(main(), 10))


def test_poller_cancel_does_not_wait_for_a_hung_request(tm_settings, source):
    source.release = threading.Event()

    async def main():
        service = make_service(tm_settings, source)
        service.set_polling("match_schedule", True)
        task = asyncio.create_task(service.run_poller())
        await asyncio.to_thread(source.entered.wait, 2)
        started = time.monotonic()
        service.close()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        assert time.monotonic() - started < 0.5
        return service

    service = asyncio.run(asyncio.wait_for(main(), 10))
    revision = service._draft.revision
    source.release.set()  # the abandoned request finishes after shutdown ...
    time.sleep(0.2)
    assert service._draft.revision == revision  # ... and leaves the draft alone


def test_ctrl_c_stops_server_promptly_mid_tm_request(tm_settings, source):
    source.release = threading.Event()
    app = create_app(tm_settings, tm_source=source)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    config = uvicorn.Config(app, log_level="warning", timeout_graceful_shutdown=2)
    server = LanServer(config, app.state.hub, app.state.auth, app.state.tm)
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started:
            assert time.monotonic() < deadline and thread.is_alive(), "server did not start"
            time.sleep(0.02)
        base = f"http://127.0.0.1:{port}"
        cookies = admin_cookies(base, settings_pin(tm_settings))
        with httpx.Client(base_url=base, timeout=10, cookies=cookies) as http:
            response = http.post("/api/tm/poll", json={"target": "match_schedule", "enabled": True})
            assert response.status_code == 200
            assert source.entered.wait(2), "poller did not call TM"

            # A manual fetch is also in flight (queued behind the hung poll).
            def fetch() -> None:
                with suppress(httpx.HTTPError):
                    http.post("/api/tm/fetch", json={"target": "match_schedule"})

            fetcher = threading.Thread(target=fetch, daemon=True)
            fetcher.start()
            time.sleep(0.2)

            started = time.monotonic()
            server.handle_exit(signal.SIGINT, None)
            thread.join(5)
            assert not thread.is_alive()
            assert time.monotonic() - started < 3.5  # graceful window (2 s) + slack, not TM's 5 s
    finally:
        source.release.set()
        server.should_exit = True
        thread.join(5)
    time.sleep(0.2)
    assert app.state.draft.revision == 0  # finished after shutdown: nothing written


def settings_pin(settings: Settings) -> str:
    return settings.admin_pin or ADMIN_PIN


# ---------------------------------------------------------- startup / secrets


def test_startup_lines_and_lifespan_log(tm_settings, settings, caplog):
    app = create_app(tm_settings, tm_source=FakeSource("[]"))
    with caplog.at_level(logging.INFO, logger="uvicorn.error"), TestClient(app):
        pass
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Tournament Manager")]
    assert lines == [f"Tournament Manager: {BASE}, division 3"]
    for secret in SECRETS:
        assert secret not in caplog.text

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="uvicorn.error"), TestClient(create_app(settings)):
        pass
    assert "Tournament Manager: not configured (file upload only)" in caplog.text

    quiet = create_app(tm_settings, tm_source=FakeSource("[]"))
    quiet.state.tm.log_on_startup = False  # `python -m judging` prints it in the banner
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="uvicorn.error"), TestClient(quiet):
        pass
    assert "Tournament Manager" not in caplog.text


def test_lifespan_can_run_twice_and_the_poller_restarts(tm_settings, source):
    app = create_app(tm_settings, tm_source=source)
    with TestClient(app):
        pass
    with TestClient(app) as client:
        sign_in(client)
        client.post("/api/tm/poll", json={"target": "match_schedule", "enabled": True})
        deadline = time.monotonic() + 2
        while draft_of(client)["match_schedule"]["name"] != TM_NAME:
            assert time.monotonic() < deadline, "poller did not run"
            time.sleep(0.02)


def test_banner_includes_tm_line():
    text = banner("127.0.0.1", 8000, "Admin PIN: set via JUDGING_ADMIN_PIN", f"Tournament Manager: {BASE}, division 3")
    assert text.index("Admin PIN") < text.index("Tournament Manager") < text.index("Press Ctrl+C")


def test_secrets_never_reach_responses_sse_or_logs(tm_settings, fake_tm, clock, caplog):
    client_tm = TMClient.from_settings(tm_settings, transport=fake_tm, clock=clock)
    app = create_app(tm_settings, rng=random.Random(2), tm_source=client_tm)
    app.state.tm.manual_min_interval = 0
    broadcast: list[str] = []
    app.state.tm.add_listener(lambda status: broadcast.append(json.dumps(status)))
    app.state.draft.add_listener(lambda event: broadcast.append(json.dumps(event)))
    http = TestClient(app)
    texts: list[str] = []

    def record(response) -> None:
        texts.append(response.text + str(response.headers))

    with caplog.at_level(logging.DEBUG):
        record(http.get("/api/tm/status"))  # anonymous: 401
        sign_in(http)
        record(http.post("/api/tm/fetch", json={"target": "match_schedule"}))
        record(http.post("/api/tm/poll", json={"target": "final_schedule", "enabled": True}))
        app.state.tm.poll_once()
        for _name, breaks, message in ERROR_CASES:
            fresh = FakeTM(fake_tm.body, clock)
            breaks(fresh)
            client_tm._transport = fresh
            client_tm._token, client_tm._cache = None, {}
            response = http.post("/api/tm/fetch", json={"target": "match_schedule"})
            assert (response.status_code, response.json()) == (502, {"detail": message})
            record(response)
            app.state.tm.poll_once()
            record(http.get("/api/tm/status"))
        record(http.get("/api/state"))
        record(http.get("/api/draft"))
    texts += broadcast
    texts += [app.state.hub._tm[1], repr(app.state.tm.source), repr(tm_settings), caplog.text]
    texts += [record.getMessage() for record in caplog.records]
    assert len(broadcast) > 5
    for text in texts:
        for secret in SECRETS:
            assert secret not in text, (secret, text[:200])
