"""New-release notice: one background check of the GitHub releases at startup.

Nothing is downloaded or installed. When a newer release exists, the server window
prints where to get it and `GET /api/version` reports it, so the admin page can show a
notice. Offline (the usual case at a venue) the check simply fails quietly.
Turn it off with JUDGING_UPDATE_CHECK=off (see settings.py).

The request sends nothing about the event: only a generic User-Agent with the app version.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import urllib.request
from collections.abc import Callable
from typing import Any

from . import __version__

log = logging.getLogger(__name__)

REPO = "v3rac1ty/VEX-judging-scheduler"
# The list, not /releases/latest: that one skips pre-releases, and the betas are pre-releases.
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases?per_page=20"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
TIMEOUT_SECONDS = 5

_NUMBERS = re.compile(r"\d+")

Release = dict[str, str]
Fetch = Callable[[], Release | None]
VersionInfo = dict[str, Any]


def parse_version(text: str) -> tuple[int, ...] | None:
    """`v0.4`, `0.4.1`, `Release 0.5` -> numeric parts; None when there are none."""
    parts = _NUMBERS.findall(text or "")
    if not parts:
        return None
    numbers = [int(part) for part in parts]
    while len(numbers) > 1 and numbers[-1] == 0:  # 0.4 == 0.4.0
        numbers.pop()
    return tuple(numbers)


def is_newer(latest: str, current: str) -> bool:
    latest_parts, current_parts = parse_version(latest), parse_version(current)
    return bool(latest_parts and current_parts and latest_parts > current_parts)


def newest_release(releases: Any) -> Release | None:
    """{"tag", "url"} of the highest-numbered published release (pre-releases count; drafts don't)."""
    best: tuple[tuple[int, ...], Release] | None = None
    for item in releases if isinstance(releases, list) else []:
        if not isinstance(item, dict) or item.get("draft"):
            continue
        tag = str(item.get("tag_name") or "")
        parts = parse_version(tag)
        if parts is None or (best is not None and parts <= best[0]):
            continue
        url = str(item.get("html_url") or "")
        best = (parts, {"tag": tag, "url": url if url.startswith("https://github.com/") else RELEASES_PAGE})
    return best[1] if best else None


def fetch_latest_release() -> Release | None:
    """The newest GitHub release (see newest_release); None when there are none."""
    request = urllib.request.Request(
        RELEASES_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"VEX-Judging-Scheduler/{__version__}",
        },
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return newest_release(json.loads(response.read().decode("utf-8")))


def display_version(text: str) -> str:
    return text if text[:1] in ("v", "V") else f"v{text}"


class UpdateChecker:
    def __init__(
        self,
        current: str = __version__,
        *,
        enabled: bool = True,
        fetch: Fetch | None = None,
    ) -> None:
        self.current = current
        self.enabled = enabled
        self._fetch = fetch or fetch_latest_release
        self._lock = threading.Lock()
        self._checking = False
        self._latest: Release | None = None
        self.print_notice = False  # `python -m judging` prints the notice; else it is logged

    def start(self) -> None:
        """Check once in a daemon thread (never delays startup or shutdown)."""
        if not self.enabled:
            return
        with self._lock:
            if self._checking or self._latest is not None:
                return
            self._checking = True
        threading.Thread(target=self.check, name="update-check", daemon=True).start()

    def check(self) -> None:
        try:
            latest = self._fetch()
        except Exception as exc:  # offline, rate-limited, no releases yet: not worth a fuss
            log.debug("Update check failed: %s", exc)
            latest = None
        with self._lock:
            self._checking = False
            self._latest = latest
        info = self.info()
        if info["update_available"]:
            self._report(info)

    def info(self) -> VersionInfo:
        with self._lock:
            checking, latest = self._checking, self._latest
        tag = latest["tag"] if latest else ""
        available = bool(latest) and is_newer(tag, self.current)
        return {
            "current": display_version(self.current),
            "latest": display_version(tag) if available else None,
            "url": latest["url"] if available and latest else None,
            "update_available": available,
            "checking": checking,
        }

    def notice_lines(self, info: VersionInfo) -> list[str]:
        return [
            f"Update available: {info['latest']} (this is {info['current']}).",
            f"    Download it from {info['url']}",
        ]

    def _report(self, info: VersionInfo) -> None:
        lines = self.notice_lines(info)
        if self.print_notice:
            print("\n" + "\n".join(lines) + "\n", flush=True)
        else:
            logging.getLogger("uvicorn.error").info(" ".join(line.strip() for line in lines))
