"""Double-click entry point (the Windows .exe is built from this; see build_exe.bat).

Starts the server like `python -m judging`, opens the browser once it answers,
and keeps the window open on errors so a non-technical user can read them.
Event data and an optional .env live next to the .exe (see settings.APP_HOME).
"""
from __future__ import annotations

import os
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser

TITLE = "VEX Judging Scheduler"


def _port() -> int:
    raw = os.environ.get("JUDGING_PORT", "").strip()
    return int(raw) if raw.isdigit() else 8000


def _answers(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/admin/status", timeout=1):
            return True
    except OSError:
        return False


def _open_when_ready(port: int) -> None:
    for _ in range(60):
        if _answers(port):
            webbrowser.open(f"http://localhost:{port}")
            return
        time.sleep(0.5)


def _pause(message: str) -> None:
    print(message)
    try:
        input("Press Enter to close this window.")
    except EOFError:
        pass


def main() -> None:
    if os.name == "nt":
        os.system(f"title {TITLE}")

    # Importing settings loads .env (next to the .exe), so read the port afterwards.
    from judging import settings

    port = _port()
    if _answers(port):
        print(f"{TITLE} is already running. Opening http://localhost:{port} ...")
        webbrowser.open(f"http://localhost:{port}")
        time.sleep(3)
        return

    print(f"Starting {TITLE}... (event data is saved in {settings.APP_HOME / 'data'})")
    print("Keep this window open while you use the app. Close it to stop the server.")
    threading.Thread(target=_open_when_ready, args=(port,), daemon=True).start()

    from judging.__main__ import main as run_server

    run_server()


if __name__ == "__main__":
    try:
        main()
    except SystemExit as exc:
        if exc.code not in (0, None):
            _pause(f"\n{TITLE} could not start (is another program using port {_port()}?).")
        raise
    except Exception:
        traceback.print_exc()
        _pause(f"\n{TITLE} stopped because of the error above.")
        sys.exit(1)
