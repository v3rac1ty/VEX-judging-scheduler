"""Admin PIN sign-in: signed session cookie, login throttle and the `require_admin` dependency.

The PIN comes from JUDGING_ADMIN_PIN (settings.py) or, when that is unset, from
`<data dir>/admin_pin.txt`, generated on first use. Sessions are signed with a random
key kept in `<data dir>/session_key`, so signed-in devices stay signed in across a
restart. Both files are written with mode 0600, read lazily (importing or creating
the app touches no files) and never deleted by reset, which only removes `*.json`.

Cookie value: `<issued unix seconds>.<hex HMAC-SHA256(key, "<issued>|<sha256(pin)>")>`.
Folding the PIN's hash into the MAC means changing the PIN signs every device out.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import math
import os
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from .models import AdminLoginRequest
from .settings import Settings, is_valid_admin_pin
from .store import write_atomic

log = logging.getLogger(__name__)

COOKIE_NAME = "judging_admin"
SESSION_SECONDS = 12 * 60 * 60
MAX_CLOCK_SKEW_SECONDS = 60

MAX_FAILURES = 5
FAILURE_WINDOW_SECONDS = 5 * 60
MAX_TRACKED_CLIENTS = 4096

GENERATED_PIN_DIGITS = 6
SESSION_KEY_BYTES = 32
SECRET_FILE_MODE = 0o600

ADMIN_REQUIRED = "Admin PIN required."
INCORRECT_PIN = "Incorrect PIN."

PinSource = Literal["env", "file"]
AdminStatus = dict[str, bool]


# ---------------------------------------------------------------- secret files


def generate_pin() -> str:
    return f"{secrets.randbelow(10 ** GENERATED_PIN_DIGITS):0{GENERATED_PIN_DIGITS}d}"


def _read_secret(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None


def load_or_create_pin(path: Path) -> str:
    """The PIN in `path`; a missing or invalid file is (re)written with a fresh random PIN."""
    existing = _read_secret(path)
    if existing is not None and is_valid_admin_pin(existing):
        return existing
    if existing is not None:
        log.warning("%s does not hold a valid PIN (4-12 digits); generating a new one.", path)
    pin = generate_pin()
    write_atomic(path, pin + "\n", mode=SECRET_FILE_MODE)
    return pin


def load_or_create_session_key(path: Path) -> bytes:
    """The signing key in `path` (hex); a missing or malformed file is replaced with a new key."""
    existing = _read_secret(path)
    if existing is not None:
        try:
            key = bytes.fromhex(existing)
        except ValueError:
            key = b""
        if len(key) == SESSION_KEY_BYTES:
            return key
        log.warning("%s is malformed; generating a new key (admins must sign in again).", path)
    key = secrets.token_bytes(SESSION_KEY_BYTES)
    write_atomic(path, key.hex() + "\n", mode=SECRET_FILE_MODE)
    return key


def display_path(path: Path) -> str:
    """`path` relative to the working directory when it is inside it (for console output)."""
    try:
        return os.path.relpath(path) if path.is_relative_to(Path.cwd()) else str(path)
    except (OSError, ValueError):
        return str(path)


@dataclass(frozen=True)
class Credentials:
    pin: str = field(repr=False)
    source: PinSource
    key: bytes = field(repr=False)


# -------------------------------------------------------------------- throttle


class TooManyAttempts(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__(retry_after)
        self.retry_after = retry_after


class LoginThrottle:
    """Per-client sliding window: `max_failures` failed logins within `window` seconds lock
    that client out until its oldest failure ages out. Thread-safe; tracks at most
    `max_clients` clients, forgetting the least recently failed first.
    """

    def __init__(
        self,
        max_failures: int = MAX_FAILURES,
        window: float = FAILURE_WINDOW_SECONDS,
        max_clients: int = MAX_TRACKED_CLIENTS,
    ) -> None:
        self.max_failures = max_failures
        self.window = window
        self.max_clients = max_clients
        self._lock = threading.Lock()
        self._failures: dict[str, deque[float]] = {}  # insertion order = least recent first

    def attempt(self, client: str, now: float, check: Callable[[], bool]) -> bool:
        """Run `check` unless `client` is locked out (raises TooManyAttempts).

        Holding the lock across the check means parallel requests can't overshoot the limit.
        """
        with self._lock:
            failures = self._recent(client, now)
            if len(failures) >= self.max_failures:
                raise TooManyAttempts(max(1, math.ceil(failures[0] + self.window - now)))
            if check():
                self._failures.pop(client, None)
                return True
            self._record(client, failures, now)
            return False

    def tracked_clients(self) -> int:
        with self._lock:
            return len(self._failures)

    def _recent(self, client: str, now: float) -> deque[float]:
        failures = self._failures.get(client, deque())
        while failures and failures[0] <= now - self.window:
            failures.popleft()
        return failures

    def _record(self, client: str, failures: deque[float], now: float) -> None:
        self._failures.pop(client, None)
        if len(self._failures) >= self.max_clients:
            self._drop_stale(now)
        while len(self._failures) >= self.max_clients:
            del self._failures[next(iter(self._failures))]
        failures.append(now)
        self._failures[client] = failures

    def _drop_stale(self, now: float) -> None:
        for client in [c for c, f in self._failures.items() if not f or f[-1] <= now - self.window]:
            del self._failures[client]


# ------------------------------------------------------------------- sessions


def _signature(key: bytes, pin: str, issued: int) -> str:
    pin_hash = hashlib.sha256(pin.encode()).hexdigest()
    return hmac.new(key, f"{issued}|{pin_hash}".encode(), hashlib.sha256).hexdigest()


class AdminAuth:
    """PIN check and session tokens for one app. Secrets are resolved on first use."""

    def __init__(self, settings: Settings, clock: Callable[[], float] = time.time) -> None:
        self._settings = settings
        self.clock = clock
        self.throttle = LoginThrottle()
        # `python -m judging` prints the PIN in its banner and turns the lifespan log line off.
        self.log_on_startup = True
        self._lock = threading.Lock()
        self._credentials: Credentials | None = None

    @property
    def credentials(self) -> Credentials:
        with self._lock:
            if self._credentials is None:
                self._credentials = self._load_credentials()
            return self._credentials

    def _load_credentials(self) -> Credentials:
        settings = self._settings
        key = load_or_create_session_key(settings.session_key_path)
        if settings.admin_pin is not None:
            return Credentials(pin=settings.admin_pin, source="env", key=key)
        return Credentials(pin=load_or_create_pin(settings.admin_pin_path), source="file", key=key)

    def startup_line(self) -> str:
        """Console line naming where the PIN comes from; echoes it only when file-based."""
        creds = self.credentials
        if creds.source == "env":
            return "Admin PIN: set via JUDGING_ADMIN_PIN"
        where = display_path(self._settings.admin_pin_path)
        return f"Admin PIN: {creds.pin}  (from {where} — set JUDGING_ADMIN_PIN to choose your own)"

    def pin_matches(self, pin: str) -> bool:
        return hmac.compare_digest(pin.encode(), self.credentials.pin.encode())

    def issue_token(self, issued: int | None = None) -> str:
        issued = int(self.clock()) if issued is None else issued
        creds = self.credentials
        return f"{issued}.{_signature(creds.key, creds.pin, issued)}"

    def verify_token(self, token: str | None) -> bool:
        """True for an untampered token signed with the current PIN and key, within its lifetime."""
        if not token:
            return False
        issued_text, _, signature = token.partition(".")
        if not (issued_text.isascii() and issued_text.isdigit() and len(issued_text) <= 12):
            return False
        issued = int(issued_text)
        now = self.clock()
        if issued > now + MAX_CLOCK_SKEW_SECONDS or now - issued > SESSION_SECONDS:
            return False
        creds = self.credentials
        expected = _signature(creds.key, creds.pin, issued)
        return hmac.compare_digest(signature.encode(), expected.encode())

    def is_admin(self, request: Request) -> bool:
        return self.verify_token(request.cookies.get(COOKIE_NAME))


def get_auth(request: Request) -> AdminAuth:
    return request.app.state.auth


def require_admin(request: Request) -> None:
    """Route dependency: 401 unless the request carries a valid admin session cookie."""
    if not get_auth(request).is_admin(request):
        raise HTTPException(status_code=401, detail=ADMIN_REQUIRED)


def _client_id(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def is_loopback_client(host: str) -> bool:
    """True for 127.0.0.0/8, ::1 and their IPv4-mapped form; non-IP hosts like "unknown" are not."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped  # dual-stack sockets report 127.0.0.1 as ::ffff:127.0.0.1
    return address.is_loopback


# --------------------------------------------------------------------- routes

router = APIRouter(prefix="/api/admin")


@router.get("/status", response_model=None)
def status(request: Request, auth: AdminAuth = Depends(get_auth)) -> AdminStatus:
    return {"admin": auth.is_admin(request)}


@router.post("/login", response_model=None)
def login(
    payload: AdminLoginRequest,
    request: Request,
    response: Response,
    auth: AdminAuth = Depends(get_auth),
) -> AdminStatus:
    def check() -> bool:
        return auth.pin_matches(payload.pin)

    client = _client_id(request)
    try:
        if is_loopback_client(client):
            # Event routers often NAT every device to one address, so a few wrong guesses from
            # any phone would lock everyone out. The laptop itself is never throttled, which
            # keeps a way in; reaching it already requires physical access.
            ok = check()
        else:
            ok = auth.throttle.attempt(client, auth.clock(), check)
    except TooManyAttempts as exc:
        raise HTTPException(
            status_code=429,
            detail=f"Too many attempts. Try again in {exc.retry_after} seconds.",
            headers={"Retry-After": str(exc.retry_after)},
        ) from None
    if not ok:
        raise HTTPException(status_code=401, detail=INCORRECT_PIN)
    response.set_cookie(
        COOKIE_NAME,
        auth.issue_token(),
        max_age=SESSION_SECONDS,
        path="/",
        httponly=True,
        samesite="strict",
        secure=False,  # plain http on the event LAN
    )
    return {"admin": True}


@router.post("/logout", response_model=None)
def logout(response: Response) -> AdminStatus:
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="strict")
    return {"admin": False}
