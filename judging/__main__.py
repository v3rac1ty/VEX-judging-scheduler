"""LAN runner: `python -m judging`.

Serves on JUDGING_HOST:JUDGING_PORT (default 0.0.0.0:8000, see settings.py) and prints every
address other devices on the network can open.
"""
from __future__ import annotations

import socket
import sys
from types import FrameType

import uvicorn

from .live import LiveHub
from .main import app

GRACEFUL_SHUTDOWN_SECONDS = 2


def lan_ipv4_addresses() -> list[str]:
    """Non-loopback IPv4 addresses of this machine, primary route first; [] when offline."""
    found: list[str] = []
    try:
        # Connecting a UDP socket only picks the outbound interface; no packet is sent.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("10.255.255.255", 1))
            found.append(probe.getsockname()[0])
    except OSError:
        pass
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except (OSError, UnicodeError):
        infos = []
    found.extend(str(info[4][0]) for info in infos)

    addresses: list[str] = []
    for address in found:
        if address.startswith("127.") or address == "0.0.0.0" or address in addresses:
            continue
        addresses.append(address)
    return addresses


def banner(host: str, port: int) -> str:
    if host in ("0.0.0.0", ""):
        urls = [f"http://{ip}:{port}" for ip in lan_ipv4_addresses()]
        urls.append(f"http://localhost:{port}")
    elif host in ("127.0.0.1", "localhost"):
        urls = [f"http://localhost:{port}"]
    else:
        urls = [f"http://{host}:{port}"]
    lines = ["", "VEX Judging Scheduler is running. Open on any device on this network:"]
    lines += [f"    {url}" for url in urls]
    if host in ("0.0.0.0", "") and len(urls) == 1:
        lines.append("    (no LAN address found - is Wi-Fi/Ethernet connected?)")
    lines += ["Press Ctrl+C to stop.", ""]
    return "\n".join(lines)


class LanServer(uvicorn.Server):
    """uvicorn.Server that ends event streams as soon as shutdown is requested.

    uvicorn waits for open connections before running the lifespan shutdown,
    so closing the hub there alone would leave Ctrl+C waiting on SSE clients.
    """

    def __init__(self, config: uvicorn.Config, hub: LiveHub) -> None:
        super().__init__(config)
        self._hub = hub

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        self._hub.close_threadsafe()
        super().handle_exit(sig, frame)

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await super().startup(sockets)
        if self.started:
            port = self.config.port
            for server in self.servers:  # report the real port when JUDGING_PORT=0
                for sock in server.sockets:
                    port = sock.getsockname()[1]
            print(banner(self.config.host, port), flush=True)


def main() -> None:
    settings = app.state.settings
    config = uvicorn.Config(
        app,
        host=settings.host,
        port=settings.port,
        log_level="info",
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS,
    )
    server = LanServer(config, app.state.hub)
    try:
        server.run()
    except KeyboardInterrupt:  # uvicorn re-raises the captured SIGINT after a clean shutdown
        pass
    if not server.started:
        sys.exit(1)


if __name__ == "__main__":
    main()
