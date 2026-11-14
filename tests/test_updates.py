from __future__ import annotations

import pytest

from judging import __version__
from judging.settings import Settings
from judging.updates import RELEASES_PAGE, UpdateChecker, is_newer, newest_release, parse_version

RELEASE_URL = "https://github.com/v3rac1ty/VEX-judging-scheduler/releases/tag/v9.0"


@pytest.mark.parametrize(
    ("text", "parts"),
    [("v0.4", (0, 4)), ("0.4.0", (0, 4)), ("V1.2.3", (1, 2, 3)), ("Release 5", (5,)), ("", None), ("beta", None)],
)
def test_parse_version(text, parts):
    assert parse_version(text) == parts


@pytest.mark.parametrize(
    ("latest", "current", "newer"),
    [
        ("v0.5", "0.4", True),
        ("v0.10", "0.9", True),  # numeric, not alphabetical
        ("v0.4", "0.4", False),
        ("v0.4.0", "0.4", False),
        ("v0.3", "0.4", False),
        ("nightly", "0.4", False),
    ],
)
def test_is_newer(latest, current, newer):
    assert is_newer(latest, current) is newer


def test_reports_newer_release(capsys):
    checker = UpdateChecker("0.4", fetch=lambda: {"tag": "v9.0", "url": RELEASE_URL})
    checker.print_notice = True
    checker.check()
    assert checker.info() == {
        "current": "v0.4",
        "latest": "v9.0",
        "url": RELEASE_URL,
        "update_available": True,
        "checking": False,
    }
    printed = capsys.readouterr().out
    assert "Update available: v9.0 (this is v0.4)." in printed
    assert RELEASE_URL in printed


def test_same_version_is_not_an_update(capsys):
    checker = UpdateChecker("0.4", fetch=lambda: {"tag": "v0.4", "url": RELEASE_URL})
    checker.print_notice = True
    checker.check()
    info = checker.info()
    assert info["update_available"] is False
    assert info["latest"] is None and info["url"] is None
    assert capsys.readouterr().out == ""


def test_offline_check_is_quiet(capsys):
    def offline():
        raise OSError("no network")

    checker = UpdateChecker("0.4", fetch=offline)
    checker.print_notice = True
    checker.check()
    assert checker.info()["update_available"] is False
    assert capsys.readouterr().out == ""


def test_disabled_checker_never_fetches():
    def fail():
        raise AssertionError("fetched")

    checker = UpdateChecker("0.4", enabled=False, fetch=fail)
    checker.start()
    assert checker.info()["checking"] is False


def test_version_endpoint(client):
    response = client.get("/api/version")
    assert response.status_code == 200
    assert response.json() == {
        "current": f"v{__version__}",
        "latest": None,
        "url": None,
        "update_available": False,
        "checking": False,
    }


def test_version_endpoint_reports_update(anon_client):
    updates = anon_client.app.state.updates
    updates._fetch = lambda: {"tag": "v99", "url": RELEASES_PAGE}
    updates.check()
    data = anon_client.get("/api/version").json()  # readable without admin; the page shows it to admins
    assert data["update_available"] is True
    assert data["latest"] == "v99"
    assert data["url"] == RELEASES_PAGE


@pytest.mark.parametrize(("raw", "enabled"), [("", True), ("on", True), ("off", False), ("0", False), ("No", False)])
def test_update_check_setting(monkeypatch, raw, enabled):
    monkeypatch.setenv("JUDGING_UPDATE_CHECK", raw)
    assert Settings().update_check is enabled


def test_newest_release_counts_prereleases_but_not_drafts():
    releases = [
        {"tag_name": "v0.3", "html_url": "https://github.com/x/y/releases/tag/v0.3", "prerelease": True},
        {"tag_name": "v0.10", "html_url": "https://github.com/x/y/releases/tag/v0.10", "prerelease": True},
        {"tag_name": "v1.0", "html_url": "https://github.com/x/y/releases/tag/v1.0", "draft": True},
        {"tag_name": "nightly", "html_url": "https://github.com/x/y/releases/tag/nightly"},
        {"tag_name": "v0.9", "html_url": "https://evil.example/v0.9"},
    ]
    assert newest_release(releases) == {"tag": "v0.10", "url": "https://github.com/x/y/releases/tag/v0.10"}
    assert newest_release([releases[4]]) == {"tag": "v0.9", "url": RELEASES_PAGE}  # only GitHub links
    assert newest_release([]) is None
    assert newest_release({"message": "rate limited"}) is None


def test_no_releases_is_not_an_update():
    checker = UpdateChecker("0.4", fetch=lambda: None)
    checker.check()
    assert checker.info()["update_available"] is False
