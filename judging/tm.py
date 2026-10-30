"""Tournament Manager (TM) public API: the match list as a schedule source.

Read-only and minimal on purpose: one resource, `GET /api/matches/{division}`, polled at
most once a minute (TM API guide etiquette), with `If-Modified-Since` so an unchanged
list costs TM a 304. Nothing here talks to TM except `TMClient`; the rest of the app sees
a `TMSource` (`matches(division) -> str`), so the HTTP client can be swapped for another
TM library later.

Secrets (client secret, API key, access token) stay inside `TMClient`. Every failure
becomes a `TMError` whose message is built from fixed text plus the configured base URL
and division, never from exception text or response bodies, so it is safe to log, return
in a 502 and broadcast in the status.

`TMService` turns fetches into draft patches (so preview, auto-fill and the SSE broadcast
happen exactly as for a file upload), owns the in-memory auto-update flags and runs the
poller task started by the app lifespan.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import http.client
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from email.message import Message
from email.utils import formatdate
from typing import Any, Literal, Protocol, TypedDict, TypeVar, get_args
from urllib.parse import urlencode, urlsplit

from .draft import DraftStore
from .models import DraftPatch
from .parsing import parse_matches
from .services import ServiceError
from .settings import TM_AUTH_URL, Settings
from .store import StateStore, StateView

log = logging.getLogger(__name__)

T = TypeVar("T")

REQUEST_TIMEOUT_SECONDS = 5.0
TOKEN_REFRESH_MARGIN_SECONDS = 60  # renew the access token this long before it expires
MANUAL_MIN_INTERVAL_SECONDS = 10  # "Fetch from TM" clicks reuse a fetch this recent
MAX_RESPONSE_BYTES = 5 * 1024 * 1024

TMTarget = Literal["match_schedule", "final_schedule"]
TARGETS: tuple[TMTarget, ...] = get_args(TMTarget)
# Auto-update for a target stops once the schedule it feeds is printed.
LOCK_FLAGS: dict[TMTarget, str] = {"match_schedule": "locked", "final_schedule": "noshow_locked"}
LOCKED_MESSAGES: dict[TMTarget, str] = {
    "match_schedule": "The schedule is already printed, so it no longer auto-updates from TM.",
    "final_schedule": "The no-show schedule is already printed, so it no longer auto-updates from TM.",
}

NOT_CONFIGURED = "Tournament Manager is not configured on the server."
CREDENTIALS_REJECTED = "TM rejected the credentials"
API_DISABLED = "TM API not enabled for this event (Tools > Options > Web Publishing)"
UNEXPECTED_RESPONSE = "Unexpected response from TM"
AUTH_UNREACHABLE = (
    "Could not reach the TM sign-in server (auth.vextm.dwabtech.com); "
    "this computer needs internet access to get a TM token"
)
AUTH_UNEXPECTED = "Unexpected response from the TM sign-in server"


class TMError(Exception):
    """A TM failure. `message` is safe to show anyone: it never holds a secret."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def not_reachable(base_url: str) -> TMError:
    return TMError(f"TM not reachable at {base_url}")


def division_not_found(division: int) -> TMError:
    return TMError(
        f"TM has no division {division} (or its API is not enabled: Tools > Options > Web Publishing)"
    )


# ------------------------------------------------------------------- transport


@dataclass(frozen=True)
class HTTPRequest:
    method: str
    url: str
    headers: dict[str, str] = field(repr=False)  # carries the token and signature
    body: bytes | None = field(default=None, repr=False)  # carries the client secret
    timeout: float = REQUEST_TIMEOUT_SECONDS


@dataclass(frozen=True)
class HTTPResponse:
    status: int
    headers: Mapping[str, str]  # lower-case names
    body: bytes = b""


# Sends one request. Network failures raise OSError; any HTTP status is returned.
Transport = Callable[[HTTPRequest], HTTPResponse]


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """Never follow redirects: they would replay the signed headers to another URL."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirects)


def _lower_headers(headers: Message | None) -> dict[str, str]:
    return {name.lower(): value for name, value in (headers or {}).items()}


def urllib_transport(request: HTTPRequest) -> HTTPResponse:
    """Stdlib HTTP. Reads at most MAX_RESPONSE_BYTES + 1 bytes of the body."""
    req = urllib.request.Request(
        request.url, data=request.body, headers=request.headers, method=request.method
    )
    try:
        with _OPENER.open(req, timeout=request.timeout) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
            return HTTPResponse(response.status, _lower_headers(response.headers), body)
    except urllib.error.HTTPError as exc:  # non-2xx (including 304) still carries a response
        with exc:
            body = exc.read(MAX_RESPONSE_BYTES + 1) if exc.fp else b""
            return HTTPResponse(exc.code, _lower_headers(exc.headers), body)
    except http.client.HTTPException:  # garbled response: report it like a network failure
        raise ConnectionError("Malformed HTTP response") from None


# ---------------------------------------------------------------------- signing


def string_to_sign(method: str, path_and_query: str, token: str, host: str, date: str) -> str:
    """Five lines, each ending in a newline (TM API guide, "Request signing")."""
    return f"{method}\n{path_and_query}\ntoken:{token}\nhost:{host}\nx-tm-date:{date}\n"


def sign(api_key: str, message: str) -> str:
    """Hex HMAC-SHA256 of `message` keyed with the TM API key."""
    return hmac.new(api_key.encode(), message.encode(), hashlib.sha256).hexdigest()


def _usable_token(value: Any) -> bool:
    return isinstance(value, str) and bool(value) and value.isascii() and value.isprintable() and " " not in value


# ----------------------------------------------------------------------- client


class TMSource(Protocol):
    """Where match lists come from. Raises TMError (safe message) on failure."""

    def matches(self, division: int) -> str:
        """The division's match list as JSON text."""
        ...


@dataclass
class _Cached:
    body: str
    last_modified: str | None


class TMClient:
    """TM public API over plain HTTP: token flow, request signing and 304 caching.

    Thread-safe (one request at a time). `transport` and `clock` (unix seconds) are
    injectable so tests never touch the network or the real clock.
    """

    def __init__(
        self,
        base_url: str,
        client_id: str,
        client_secret: str,
        api_key: str,
        *,
        auth_url: str = TM_AUTH_URL,
        transport: Transport = urllib_transport,
        clock: Callable[[], float] = time.time,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.auth_url = auth_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._api_key = api_key
        self._transport = transport
        self._clock = clock
        self._timeout = timeout
        self._lock = threading.Lock()
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._cache: dict[str, _Cached] = {}

    @classmethod
    def from_settings(cls, settings: Settings, **kwargs: Any) -> TMClient:
        if not settings.tm_configured:
            raise ValueError(NOT_CONFIGURED)
        return cls(
            settings.tm_url or "",
            settings.tm_client_id or "",
            settings.tm_client_secret or "",
            settings.tm_api_key or "",
            auth_url=settings.tm_auth_url,
            **kwargs,
        )

    def __repr__(self) -> str:
        return f"TMClient({self.base_url!r})"

    def matches(self, division: int) -> str:
        try:
            return self.get(f"/api/matches/{int(division)}")
        except _NotFound:
            raise division_not_found(division) from None

    def get(self, path: str) -> str:
        """GET `path` (relative to the base URL); a 304 returns the cached body."""
        with self._lock:
            cached = self._cache.get(path)
            response = self._send(path, cached)
            if response.status == 401:  # token revoked or expired early: renew once
                self._token = None
                response = self._send(path, cached)
                if response.status == 401:
                    raise TMError(CREDENTIALS_REJECTED)
            if response.status == 304 and cached is not None:
                return cached.body
            if response.status == 403:
                raise TMError(API_DISABLED)
            if response.status == 404:
                raise _NotFound()
            if response.status != 200 or len(response.body) > MAX_RESPONSE_BYTES:
                raise TMError(UNEXPECTED_RESPONSE)
            try:
                body = response.body.decode("utf-8-sig")
            except UnicodeDecodeError:
                raise TMError(UNEXPECTED_RESPONSE) from None
            self._cache[path] = _Cached(body, response.headers.get("last-modified"))
            return body

    def _send(self, path: str, cached: _Cached | None) -> HTTPResponse:
        token = self._access_token()
        url = self.base_url + path
        parts = urlsplit(url)
        path_and_query = parts.path + (f"?{parts.query}" if parts.query else "")
        date = formatdate(self._clock(), usegmt=True)  # RFC 1123, GMT
        headers = {
            "Host": parts.netloc,
            "Authorization": f"Bearer {token}",
            "x-tm-date": date,
            "x-tm-signature": sign(
                self._api_key, string_to_sign("GET", path_and_query, token, parts.netloc, date)
            ),
            "Accept": "application/json",
        }
        if cached is not None and cached.last_modified:
            headers["If-Modified-Since"] = cached.last_modified
        try:
            return self._transport(HTTPRequest("GET", url, headers, timeout=self._timeout))
        except OSError as exc:
            log.debug("TM request failed: %s", type(exc).__name__)
            raise not_reachable(self.base_url) from None

    def _access_token(self) -> str:
        """Cached bearer token, renewed TOKEN_REFRESH_MARGIN_SECONDS before it expires."""
        now = self._clock()
        if self._token and now < self._token_expires_at - TOKEN_REFRESH_MARGIN_SECONDS:
            return self._token
        self._token = None
        body = urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            }
        ).encode()
        headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
        try:
            response = self._transport(
                HTTPRequest("POST", self.auth_url, headers, body, timeout=self._timeout)
            )
        except OSError as exc:
            log.debug("TM token request failed: %s", type(exc).__name__)
            raise TMError(AUTH_UNREACHABLE) from None
        if response.status in (400, 401, 403):  # OAuth: invalid_client / unauthorized_client
            raise TMError(CREDENTIALS_REJECTED)
        if response.status != 200:
            raise TMError(AUTH_UNEXPECTED)
        try:
            data = json.loads(response.body)
            token = data["access_token"]
            expires_in = float(data.get("expires_in", 0))
        except (ValueError, TypeError, KeyError, AttributeError):
            raise TMError(AUTH_UNEXPECTED) from None
        if not _usable_token(token) or expires_in != expires_in:  # NaN
            raise TMError(AUTH_UNEXPECTED)
        self._token = token
        self._token_expires_at = now + max(0.0, expires_in)
        return token


class _NotFound(Exception):
    """404 from TM; `matches` turns it into a message naming the division."""


# ----------------------------------------------------------------------- threads


async def run_in_daemon_thread(fn: Callable[[], T]) -> T:
    """Await `fn()` run in a new daemon thread.

    Unlike `asyncio.to_thread`, the thread is not part of an executor, so neither the
    event loop's shutdown nor interpreter exit waits for a TM request still in flight:
    Ctrl+C stays prompt even mid-request. Cancelling the await abandons the thread.
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future[T] = loop.create_future()

    def settle(result: Any, error: BaseException | None) -> None:
        if future.done():  # the awaiting task was cancelled
            return
        if error is not None:
            future.set_exception(error)
        else:
            future.set_result(result)

    def runner() -> None:
        result: Any = None
        error: BaseException | None = None
        try:
            result = fn()
        except BaseException as exc:  # noqa: BLE001 -- handed to the awaiting task
            error = exc
        try:
            loop.call_soon_threadsafe(settle, result, error)
        except RuntimeError:  # the loop is gone (shutdown): nobody is waiting any more
            pass

    threading.Thread(target=runner, name="tm-fetch", daemon=True).start()
    return await future


# ----------------------------------------------------------------------- service


class TMStatus(TypedDict):
    configured: bool
    division: int
    poll_seconds: int
    polling: dict[str, bool]
    last_fetch: str | None  # ISO 8601 in the event timezone
    last_change: str | None
    error: str | None


class TMFetchResult(TypedDict):
    tm: TMStatus
    changed: bool


class TMPollResult(TypedDict):
    tm: TMStatus


TMListener = Callable[[TMStatus], None]


def canonical_matches(raw: str) -> str:
    """TM's JSON re-indented for the setup textarea; TMError unless it holds a match list."""
    try:
        data = json.loads(raw)
        text = json.dumps(data, indent=2)
        parse_matches(text)
    except (ValueError, RecursionError):
        raise TMError(UNEXPECTED_RESPONSE) from None
    return text


class TMService:
    """Fetches into the draft, auto-update flags and the poller. Thread-safe.

    "Changed" means two different things on purpose:
    - A manual fetch patches the draft whenever TM's list differs from the draft text.
    - The poller patches a target only when TM's list differs from what that target last
      took from TM. A file the admin drops in afterwards therefore survives until TM's
      data actually changes, instead of being overwritten on the next tick.
    """

    def __init__(
        self,
        settings: Settings,
        draft: DraftStore,
        store: StateStore,
        *,
        source: TMSource | None = None,
        clock: Callable[[], float] = time.time,
        poll_seconds: float | None = None,
    ) -> None:
        self.configured = settings.tm_configured
        self.url = settings.tm_url
        self.division = settings.tm_division
        self.poll_seconds = poll_seconds if poll_seconds is not None else settings.tm_poll_seconds
        self.manual_min_interval: float = MANUAL_MIN_INTERVAL_SECONDS
        self.source: TMSource | None = None
        if self.configured:
            self.source = source if source is not None else TMClient.from_settings(settings)
        # `python -m judging` prints the TM line in its banner and turns the lifespan log off.
        self.log_on_startup = True
        self._settings = settings
        self._draft = draft
        self._store = store
        self._clock = clock
        self._lock = threading.RLock()  # status fields; never held across network calls
        self._fetch_lock = threading.Lock()  # one fetch-and-patch at a time
        self._listeners: list[TMListener] = []
        self._polling: dict[TMTarget, bool] = {target: False for target in TARGETS}
        self._seen: dict[TMTarget, str | None] = {target: None for target in TARGETS}
        self._body: str | None = None  # latest list from TM (canonical text)
        self._requested_at: float | None = None  # last TM request (etiquette timer)
        self._polled_at: float | None = None
        self._last_fetch: float | None = None
        self._last_change: float | None = None
        self._error: str | None = None
        self._published: TMStatus | None = None
        self._closed = False
        self._wake: Callable[[], None] | None = None

    # ------------------------------------------------------------- status

    def startup_line(self) -> str:
        """Console line for the banner / lifespan log. Never includes a secret."""
        if self.configured:
            return f"Tournament Manager: {self.url}, division {self.division}"
        missing = self._settings.tm_missing
        if len(missing) < 4:  # partly set up: say what is still missing
            return f"Tournament Manager: not configured (file upload only; missing {', '.join(missing)})"
        return "Tournament Manager: not configured (file upload only)"

    def _iso(self, moment: float | None) -> str | None:
        if moment is None:
            return None
        return datetime.fromtimestamp(moment, tz=self._settings.event_tz).isoformat(timespec="seconds")

    def status(self) -> TMStatus:
        with self._lock:
            return {
                "configured": self.configured,
                "division": self.division,
                "poll_seconds": int(self.poll_seconds),
                "polling": dict(self._polling),
                "last_fetch": self._iso(self._last_fetch),
                "last_change": self._iso(self._last_change),
                "error": self._error,
            }

    def add_listener(self, listener: TMListener) -> None:
        """`listener` gets every changed status, in order (called under the status lock)."""
        with self._lock:
            self._listeners.append(listener)

    def _changed(self) -> None:
        """Notify listeners if the status differs from the last one sent (call under _lock)."""
        status = self.status()
        if status == self._published:
            return
        self._published = status
        for listener in self._listeners:
            try:
                listener(status)
            except Exception:
                log.exception("TM status listener failed")

    def _require_configured(self) -> TMSource:
        if self.source is None:
            raise ServiceError(409, NOT_CONFIGURED)
        return self.source

    # ------------------------------------------------------------- fetching

    def _read(self, min_interval: float) -> str:
        """TM's match list; reuses the last one if TM was asked less than `min_interval` ago."""
        source = self._require_configured()
        now = self._clock()
        if (
            self._body is not None
            and self._requested_at is not None
            and now - self._requested_at < min_interval
        ):
            return self._body
        self._requested_at = now
        try:
            text = canonical_matches(source.matches(self.division))
        except TMError as exc:
            self._record_error(exc.message)
            raise
        except Exception as exc:  # a swapped-in source failing in its own way
            log.error("TM source raised %s", type(exc).__name__)  # not str(exc): may hold secrets
            self._record_error(UNEXPECTED_RESPONSE)
            raise TMError(UNEXPECTED_RESPONSE) from None
        with self._lock:
            self._body = text
            self._last_fetch = now
            if self._error is not None:
                log.info("Tournament Manager: fetching works again")
            self._error = None
            self._changed()
        return text

    def _record_error(self, message: str) -> None:
        with self._lock:
            if message != self._error:
                log.warning("Tournament Manager: %s", message)
            self._error = message
            self._changed()

    def _apply(self, target: TMTarget, text: str) -> bool:
        """Put `text` in the draft's `target` unless it is already there. True if patched."""
        if self._closed or self._draft.view()[target]["text"] == text:
            return False
        try:
            patch = DraftPatch.model_validate(
                {target: {"text": text, "name": f"Tournament Manager · Division {self.division}"}}
            )
        except ValueError:  # over the 5 MB draft limit
            self._record_error(UNEXPECTED_RESPONSE)
            raise TMError(UNEXPECTED_RESPONSE) from None
        changed = bool(self._draft.patch(patch)["changes"])
        if changed:
            with self._lock:
                self._last_change = self._clock()
                self._changed()
        return changed

    def fetch(self, target: TMTarget) -> TMFetchResult:
        """`POST /api/tm/fetch`: patch the draft if TM's list differs from its text."""
        self._require_configured()
        with self._fetch_lock:
            text = self._read(self.manual_min_interval)
            changed = self._apply(target, text)
            with self._lock:
                self._seen[target] = text
        return {"tm": self.status(), "changed": changed}

    # -------------------------------------------------------------- polling

    def set_polling(self, target: TMTarget, enabled: bool) -> TMPollResult:
        """`POST /api/tm/poll`. Turning it on makes the next poll sync the draft to TM."""
        self._require_configured()
        if enabled and self._store.view().get(LOCK_FLAGS[target]):
            raise ServiceError(409, LOCKED_MESSAGES[target])
        with self._lock:
            if self._polling[target] != enabled:
                self._polling[target] = enabled
                if enabled:
                    self._seen[target] = None
                self._changed()
        self._wake_poller()
        return {"tm": self.status()}

    def on_state(self, view: StateView) -> None:
        """StateStore listener: printing a schedule stops its auto-update."""
        with self._lock:
            stopped = [t for t in TARGETS if self._polling[t] and view.get(LOCK_FLAGS[t])]
            for target in stopped:
                self._polling[target] = False
            if stopped:
                self._changed()

    def reset(self) -> None:
        """`POST /api/reset`: auto-update off and the fetch history forgotten."""
        with self._lock:
            self._polling = {target: False for target in TARGETS}
            self._seen = {target: None for target in TARGETS}
            self._last_fetch = self._last_change = self._error = None
            self._changed()

    def open(self) -> None:
        """Lifespan startup (also after an earlier shutdown of the same app, as in tests)."""
        self._closed = False

    def close(self) -> None:
        """Shutdown: a fetch still waiting on TM will not touch the draft when it returns."""
        self._closed = True

    def poll_once(self) -> None:
        """One poller tick (blocking): fetch, then patch each enabled target TM changed."""
        if self._closed or self.source is None:
            return
        self.on_state(self._store.view())
        with self._lock:
            if not any(self._polling.values()):
                return
            self._polled_at = self._clock()
        with self._fetch_lock:
            try:
                text = self._read(0)
            except TMError:
                return  # recorded in the status; try again next tick
            if self._closed:
                return
            for target in TARGETS:
                with self._lock:
                    if not self._polling[target] or self._seen[target] == text:
                        continue
                    self._seen[target] = text
                try:
                    self._apply(target, text)
                except TMError:
                    return

    def seconds_until_poll(self) -> float | None:
        """Delay before the next tick is due; None while auto-update is off everywhere."""
        with self._lock:
            if not any(self._polling.values()):
                return None
            times = [t for t in (self._requested_at, self._polled_at) if t is not None]
        if not times:
            return 0.0
        return max(times) + self.poll_seconds - self._clock()

    def _wake_poller(self) -> None:
        wake = self._wake
        if wake is not None:
            try:
                wake()
            except RuntimeError:  # loop already closed
                pass

    async def run_poller(self) -> None:
        """Poll while any auto-update is on. Lifespan task; ends by cancellation."""
        loop = asyncio.get_running_loop()
        wake = asyncio.Event()
        self._wake = lambda: loop.call_soon_threadsafe(wake.set)
        try:
            while True:
                delay = self.seconds_until_poll()
                if delay is None or delay > 0:
                    try:
                        await asyncio.wait_for(wake.wait(), timeout=delay)
                    except TimeoutError:
                        pass
                    wake.clear()
                    continue
                try:
                    await run_in_daemon_thread(self.poll_once)
                except Exception:  # e.g. draft.json unwritable: keep the poller alive
                    log.exception("Tournament Manager poll failed")
        finally:
            self._wake = None
