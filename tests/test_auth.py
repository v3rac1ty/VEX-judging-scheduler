"""Admin PIN: sign-in, session cookie, throttle, gated routes and the PIN/key files."""
from __future__ import annotations

import dataclasses
import logging
import random
import re
import stat
import threading

import pytest
from fastapi.testclient import TestClient

from judging.__main__ import banner
from judging.auth import (
    COOKIE_NAME,
    FAILURE_WINDOW_SECONDS,
    MAX_FAILURES,
    SESSION_SECONDS,
    LoginThrottle,
    TooManyAttempts,
    is_loopback_client,
)
from judging.main import create_app
from judging.settings import Settings
from conftest import ADMIN_PIN, sign_in

ADMIN_REQUIRED = {"detail": "Admin PIN required."}

GATED = [
    ("post", "/api/generate", {}),
    ("post", "/api/generate-noshow", {}),
    ("post", "/api/snapshot-print", {}),
    ("post", "/api/reset", None),
    ("post", "/api/active-schedule", {"schedule_id": "x"}),
    ("patch", "/api/draft", {"judge_pairs": "6"}),
    ("patch", "/api/draft", {"bogus": 1}),  # still 401: auth runs before body validation
    ("get", "/api/tm/status", None),
    ("post", "/api/tm/fetch", {"target": "match_schedule"}),
    ("post", "/api/tm/fetch", {"target": "nope"}),
    ("post", "/api/tm/poll", {"target": "match_schedule", "enabled": True}),
]


class FakeClock:
    def __init__(self, now: float = 1_800_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def login(client: TestClient, pin: str):
    return client.post("/api/admin/login", json={"pin": pin})


def is_admin(client: TestClient) -> bool:
    response = client.get("/api/admin/status")
    assert response.status_code == 200
    return response.json()["admin"]


def file_settings(settings: Settings) -> Settings:
    """`settings` without an injected PIN: the app uses data_dir/admin_pin.txt."""
    return dataclasses.replace(settings, admin_pin=None)


def mode(path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# ------------------------------------------------------------------ login


def test_login_sets_strict_httponly_session_cookie(anon_client):
    assert is_admin(anon_client) is False
    response = login(anon_client, ADMIN_PIN)
    assert response.status_code == 200
    assert response.json() == {"admin": True}

    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{COOKIE_NAME}=")
    attrs = {part.strip().split("=")[0].lower(): part.strip() for part in cookie.split(";")[1:]}
    assert "httponly" in attrs
    assert attrs["samesite"].lower() == "samesite=strict"
    assert attrs["path"] == "Path=/"
    assert attrs["max-age"] == f"Max-Age={SESSION_SECONDS}"
    assert "secure" not in attrs  # plain http on the LAN
    assert ADMIN_PIN not in cookie
    assert is_admin(anon_client) is True


def test_wrong_pin_is_401_without_cookie(anon_client):
    for pin in ["000000", ADMIN_PIN + "0", f" {ADMIN_PIN}", "pin-ñ", "abcdef"]:
        response = login(anon_client, pin)
        assert response.status_code == 401
        assert response.json() == {"detail": "Incorrect PIN."}
        assert "set-cookie" not in response.headers
    assert is_admin(anon_client) is False


@pytest.mark.parametrize("body", [{}, {"pin": ""}, {"pin": None}, {"pin": ADMIN_PIN, "extra": 1}])
def test_login_body_is_validated(anon_client, body):
    response = anon_client.post("/api/admin/login", json=body)
    assert response.status_code == 400
    assert isinstance(response.json()["detail"], str)


def test_logout_clears_cookie(client):
    assert is_admin(client) is True
    response = client.post("/api/admin/logout")
    assert response.status_code == 200
    assert response.json() == {"admin": False}
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f'{COOKIE_NAME}="";') and "Max-Age=0" in cookie
    assert is_admin(client) is False
    assert client.post("/api/reset").status_code == 401


# ---------------------------------------------------------------- gating


@pytest.mark.parametrize(("method", "path", "body"), GATED)
def test_admin_routes_reject_anonymous(anon_client, method, path, body):
    response = anon_client.request(method, path, json=body)
    assert response.status_code == 401
    assert response.json() == ADMIN_REQUIRED
    assert anon_client.get("/api/state").json()["revision"] == 0
    assert anon_client.get("/api/draft").json()["revision"] == 0


def test_malformed_json_on_admin_route_changes_nothing(anon_client):
    # FastAPI parses JSON before running dependencies, so this is a 400, never a mutation.
    response = anon_client.patch(
        "/api/draft", content="{nope", headers={"content-type": "application/json"}
    )
    assert response.status_code in (400, 401)
    assert anon_client.get("/api/draft").json()["revision"] == 0


def test_open_routes_work_anonymously(client, anon_client, generate_payload):
    assert client.post("/api/generate", json=generate_payload).status_code == 200
    assert client.post("/api/snapshot-print", json={}).status_code == 200  # check-off needs a print
    teams = [slot["team"] for slot in anon_client.get("/api/state").json()["slots"]]

    assert anon_client.get("/").status_code == 200
    assert anon_client.get("/api/draft").status_code == 200
    assert anon_client.get("/api/admin/status").status_code == 200
    for endpoint, team in [("checkoff", teams[0]), ("noshow", teams[1]), ("not-competing", teams[2])]:
        response = anon_client.post(f"/api/{endpoint}", json={"team": team})
        assert response.status_code == 200, (endpoint, response.text)
    assert anon_client.post("/api/admin/logout").status_code == 200


def test_admin_routes_work_when_signed_in(client, generate_payload):
    assert client.post("/api/generate", json=generate_payload).status_code == 200
    state = client.post("/api/snapshot-print", json={}).json()
    assert client.post("/api/active-schedule", json={"schedule_id": state["schedules"][0]["id"]}).status_code == 200
    assert client.patch("/api/draft", json={"judge_pairs": "6"}).status_code == 200
    assert client.post("/api/reset").status_code == 200


# --------------------------------------------------------------- cookies


def test_tampered_and_malformed_cookies_are_rejected(anon_client):
    auth = anon_client.app.state.auth
    token = auth.issue_token()
    issued, signature = token.split(".")
    flipped = signature[:-1] + ("0" if signature[-1] != "0" else "1")
    bad = [
        f"{issued}.{flipped}",
        f"{int(issued) - 1}.{signature}",
        f"{issued}.{signature.upper()}",
        issued,
        f".{signature}",
        f"{issued}.",
        f"-{issued}.{signature}",
        f"{issued}x.{signature}",
        "garbage",
    ]
    for value in bad:
        anon_client.cookies.set(COOKIE_NAME, value)
        assert is_admin(anon_client) is False, value
        assert anon_client.post("/api/reset").status_code == 401
    assert auth.verify_token(f"{token}ñ") is False  # non-ASCII can't reach compare_digest as str
    assert auth.verify_token(f"١٢٣.{signature}") is False  # non-ASCII digits
    anon_client.cookies.set(COOKIE_NAME, token)
    assert is_admin(anon_client) is True


def test_expired_and_future_cookies_are_rejected(anon_client):
    auth = anon_client.app.state.auth
    clock = FakeClock()
    auth.clock = clock
    now = int(clock.now)
    cases = {
        now - SESSION_SECONDS - 1: False,  # expired
        now - SESSION_SECONDS + 5: True,
        now + 30: True,  # small clock skew between requests is fine
        now + 120: False,  # issued in the future
    }
    for issued, expected in cases.items():
        anon_client.cookies.set(COOKIE_NAME, auth.issue_token(issued))
        assert is_admin(anon_client) is expected, issued

    anon_client.cookies.set(COOKIE_NAME, auth.issue_token())
    assert is_admin(anon_client) is True
    clock.now += SESSION_SECONDS + 1
    assert is_admin(anon_client) is False


def test_sessions_survive_restart_but_not_a_pin_change(settings, make_client):
    client = make_client(admin=True)
    token = client.cookies[COOKIE_NAME]

    restarted = TestClient(create_app(settings))
    restarted.cookies.set(COOKIE_NAME, token)
    assert is_admin(restarted) is True

    new_pin = TestClient(create_app(dataclasses.replace(settings, admin_pin="135790")))
    new_pin.cookies.set(COOKIE_NAME, token)
    assert is_admin(new_pin) is False
    assert new_pin.post("/api/reset").status_code == 401

    (settings.session_key_path).unlink()  # a new signing key also signs everyone out
    new_key = TestClient(create_app(settings))
    new_key.cookies.set(COOKIE_NAME, token)
    assert is_admin(new_key) is False


# -------------------------------------------------------------- throttle


def test_throttle_locks_out_after_five_failures_until_oldest_ages_out(anon_client):
    clock = FakeClock()
    anon_client.app.state.auth.clock = clock
    for _ in range(MAX_FAILURES):
        assert login(anon_client, "000000").status_code == 401
        clock.now += 10

    locked = login(anon_client, ADMIN_PIN)  # even the right PIN waits
    assert locked.status_code == 429
    wait = FAILURE_WINDOW_SECONDS - 10 * MAX_FAILURES
    assert locked.headers["retry-after"] == str(wait)
    assert locked.json() == {"detail": f"Too many attempts. Try again in {wait} seconds."}
    assert "set-cookie" not in locked.headers

    clock.now += wait - 1
    assert login(anon_client, ADMIN_PIN).headers["retry-after"] == "1"
    clock.now += 1  # the first failure is now out of the window: one more try is allowed
    assert login(anon_client, "000000").status_code == 401
    assert login(anon_client, ADMIN_PIN).status_code == 429
    clock.now += 10
    assert login(anon_client, ADMIN_PIN).status_code == 200


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2", "::1"])
def test_loopback_client_is_never_throttled(settings, host):
    client = TestClient(create_app(settings), client=(host, 50000))
    for _ in range(MAX_FAILURES * 3):
        assert login(client, "000000").status_code == 401
    assert login(client, ADMIN_PIN).status_code == 200
    assert is_admin(client) is True


@pytest.mark.parametrize("host", ["192.168.1.20", "10.0.0.1", "testclient"])
def test_non_loopback_client_is_still_throttled(settings, host):
    client = TestClient(create_app(settings), client=(host, 50000))
    for _ in range(MAX_FAILURES):
        assert login(client, "000000").status_code == 401
    assert login(client, ADMIN_PIN).status_code == 429


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", True),
        ("127.1.2.3", True),
        ("::1", True),
        ("::ffff:127.0.0.1", True),
        ("192.168.1.5", False),
        ("::2", False),
        ("0.0.0.0", False),
        ("testclient", False),
        ("unknown", False),
        ("localhost", False),
        ("", False),
    ],
)
def test_is_loopback_client(host, expected):
    assert is_loopback_client(host) is expected


def test_success_clears_failures(anon_client):
    for _ in range(MAX_FAILURES - 1):
        assert login(anon_client, "000000").status_code == 401
    assert login(anon_client, ADMIN_PIN).status_code == 200
    for _ in range(MAX_FAILURES - 1):
        assert login(anon_client, "000000").status_code == 401
    assert login(anon_client, ADMIN_PIN).status_code == 200


def test_throttle_is_per_client():
    throttle = LoginThrottle(max_failures=2, window=60)
    for _ in range(2):
        assert throttle.attempt("10.0.0.5", 0, lambda: False) is False
    with pytest.raises(TooManyAttempts) as locked:
        throttle.attempt("10.0.0.5", 1, lambda: True)
    assert locked.value.retry_after == 59
    assert throttle.attempt("10.0.0.6", 1, lambda: True) is True


def test_throttle_is_bounded_and_drops_stale_clients_first():
    throttle = LoginThrottle(max_failures=5, window=60, max_clients=3)
    throttle.attempt("stale", 0, lambda: False)
    throttle.attempt("a", 100, lambda: False)
    throttle.attempt("b", 101, lambda: False)
    throttle.attempt("c", 102, lambda: False)  # full: "stale" (older than the window) goes
    assert throttle.tracked_clients() == 3
    for i in range(50):
        throttle.attempt(f"flood-{i}", 103, lambda: False)
    assert throttle.tracked_clients() == 3
    throttle.attempt("flood-49", 104, lambda: True)
    assert throttle.tracked_clients() == 2


def test_parallel_attempts_cannot_exceed_the_limit():
    throttle = LoginThrottle()
    checks: list[int] = []
    barrier = threading.Barrier(40)
    outcomes: list[str] = []

    def check() -> bool:
        checks.append(1)
        return False

    def attempt() -> None:
        barrier.wait()
        try:
            throttle.attempt("10.0.0.9", 0, check)
            outcomes.append("401")
        except TooManyAttempts:
            outcomes.append("429")

    threads = [threading.Thread(target=attempt) for _ in range(40)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(checks) == MAX_FAILURES
    assert sorted(outcomes) == ["401"] * MAX_FAILURES + ["429"] * (40 - MAX_FAILURES)


# ------------------------------------------------------- PIN and key files


def test_pin_file_is_generated_once_with_mode_0600(settings, data_dir):
    file_based = file_settings(settings)
    client = TestClient(create_app(file_based))
    # Creating the app (and anonymous reads) touch no secret files.
    assert is_admin(client) is False
    assert not file_based.admin_pin_path.exists()
    assert not file_based.session_key_path.exists()

    assert login(client, "nope").status_code == 401
    pin = file_based.admin_pin_path.read_text().strip()
    assert re.fullmatch(r"\d{6}", pin)
    assert re.fullmatch(r"[0-9a-f]{64}", file_based.session_key_path.read_text().strip())
    assert mode(file_based.admin_pin_path) == 0o600
    assert mode(file_based.session_key_path) == 0o600
    assert not list(data_dir.glob(".*.tmp"))

    sign_in(client, pin)
    key_before = file_based.session_key_path.read_text()
    restarted = TestClient(create_app(file_based))  # reused, not regenerated
    sign_in(restarted, pin)
    assert file_based.admin_pin_path.read_text().strip() == pin
    assert file_based.session_key_path.read_text() == key_before


def test_invalid_pin_or_key_file_is_replaced(settings):
    file_based = file_settings(settings)
    file_based.admin_pin_path.write_text("12\n")
    file_based.session_key_path.write_text("not hex\n")
    client = TestClient(create_app(file_based))
    login(client, "12")
    pin = file_based.admin_pin_path.read_text().strip()
    assert re.fullmatch(r"\d{6}", pin)
    assert len(bytes.fromhex(file_based.session_key_path.read_text().strip())) == 32
    sign_in(client, pin)


@pytest.mark.parametrize("contents", ["hunter-22", "1" * 13, "12ab34"])
def test_non_digit_or_overlong_pin_file_is_replaced(settings, contents):
    file_based = file_settings(settings)
    file_based.admin_pin_path.write_text(contents + "\n")
    client = TestClient(create_app(file_based))
    assert login(client, contents).status_code == 401
    assert re.fullmatch(r"\d{6}", file_based.admin_pin_path.read_text().strip())


def test_existing_pin_file_is_used(settings):
    file_based = file_settings(settings)
    file_based.admin_pin_path.write_text("  48291357  \n")  # surrounding whitespace is trimmed
    client = TestClient(create_app(file_based))
    sign_in(client, "48291357")
    assert client.app.state.auth.startup_line() == (
        f"Admin PIN: 48291357  (from {file_based.admin_pin_path} — "
        "set JUDGING_ADMIN_PIN to choose your own)"
    )


def test_env_pin_wins_over_file(monkeypatch, settings, tz):
    settings.admin_pin_path.write_text("111111\n")
    monkeypatch.setenv("JUDGING_ADMIN_PIN", "2222")
    env_settings = Settings(data_dir=settings.data_dir, event_tz=tz)
    assert env_settings.admin_pin == "2222"
    client = TestClient(create_app(env_settings))
    assert login(client, "111111").status_code == 401
    sign_in(client, "2222")
    assert settings.admin_pin_path.read_text() == "111111\n"

    line = client.app.state.auth.startup_line()
    assert line == "Admin PIN: set via JUDGING_ADMIN_PIN"
    assert "2222" not in line


def test_responses_never_contain_pin_or_key(client, generate_payload, settings):
    client.post("/api/generate", json=generate_payload)
    key = settings.session_key_path.read_text().strip()
    responses = [
        client.get("/api/admin/status"),
        client.get("/api/state"),
        client.get("/api/draft"),
        login(client, ADMIN_PIN),
        client.post("/api/admin/logout"),
    ]
    for response in responses:
        blob = response.text + str(response.headers)
        assert ADMIN_PIN not in blob and key not in blob
    assert ADMIN_PIN not in repr(settings)


def test_reset_keeps_pin_and_key_files(settings, data_dir, generate_payload):
    file_based = file_settings(settings)
    client = TestClient(create_app(file_based, rng=random.Random(1)))
    login(client, "x")  # creates both files
    sign_in(client, file_based.admin_pin_path.read_text().strip())
    client.post("/api/generate", json=generate_payload)
    (data_dir / "stray.json").write_text("{}")

    assert client.post("/api/reset").status_code == 200
    assert not (data_dir / "stray.json").exists()
    assert file_based.admin_pin_path.exists() and file_based.session_key_path.exists()
    assert is_admin(client) is True  # still signed in after a reset


# --------------------------------------------------------------- startup


def test_lifespan_logs_the_pin_line_once(settings, caplog):
    app = create_app(file_settings(settings))
    with caplog.at_level(logging.INFO, logger="uvicorn.error"), TestClient(app):
        pass
    pin = settings.admin_pin_path.read_text().strip()
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Admin PIN")]
    assert len(lines) == 1 and pin in lines[0]

    caplog.clear()
    quiet = create_app(settings)
    quiet.state.auth.log_on_startup = False  # `python -m judging` prints it in the banner
    with caplog.at_level(logging.INFO, logger="uvicorn.error"), TestClient(quiet):
        pass
    assert not [r for r in caplog.records if "Admin PIN" in r.getMessage()]


def test_env_pin_is_not_logged(settings, caplog):
    with caplog.at_level(logging.INFO, logger="uvicorn.error"), TestClient(create_app(settings)):
        pass
    assert "Admin PIN: set via JUDGING_ADMIN_PIN" in caplog.text
    assert ADMIN_PIN not in caplog.text


def test_banner_includes_admin_line():
    text = banner("127.0.0.1", 8000, "Admin PIN: set via JUDGING_ADMIN_PIN")
    assert "http://localhost:8000" in text
    assert text.index("Admin PIN: set via JUDGING_ADMIN_PIN") < text.index("Press Ctrl+C")
    assert "Admin PIN" not in banner("127.0.0.1", 8000)
