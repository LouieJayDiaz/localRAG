"""Run the local web application in a native window when possible."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import ipaddress
import logging
import socket
import threading
import time
from collections.abc import Callable, Sequence
from typing import Any
import urllib.error
import urllib.request
import webbrowser

LOGGER = logging.getLogger(__name__)
DEFAULT_HOST = "127.0.0.1"
DEFAULT_STARTUP_TIMEOUT = 15.0
DEFAULT_WINDOW_TITLE = "LocalRAG"


def is_loopback_host(host: str) -> bool:
    """Return whether *host* refers to a local interface."""
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host.casefold() == "localhost"


def find_free_port(host: str = DEFAULT_HOST) -> int:
    """Ask the operating system for an available TCP port."""
    with socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET) as probe:
        probe.bind((host, 0))
        return int(probe.getsockname()[1])


def _url_host(host: str) -> str:
    return f"[{host}]" if ":" in host and not host.startswith("[") else host


def local_url(host: str, port: int) -> str:
    """Build the browser URL for a local server."""
    return f"http://{_url_host(host)}:{port}/"


def wait_for_server(
    url: str,
    *,
    timeout: float = DEFAULT_STARTUP_TIMEOUT,
    request: Callable[[str], Any] | None = None,
    is_alive: Callable[[], bool] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Wait until the local web server accepts requests."""
    request = request or _request_url
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if is_alive is not None and not is_alive():
            raise RuntimeError("The LocalRAG server stopped before it became ready.")
        try:
            request(url)
            return
        except (OSError, urllib.error.URLError) as error:
            last_error = error
            sleep(0.05)
    detail = f": {last_error}" if last_error else ""
    raise TimeoutError(f"LocalRAG server did not start within {timeout:g} seconds{detail}")


def _request_url(url: str) -> None:
    with urllib.request.urlopen(url, timeout=1) as response:
        response.read(1)


@dataclass
class RunningServer:
    """The uvicorn thread and URL owned by the desktop launcher."""

    url: str
    server: Any
    thread: threading.Thread

    def stop(self, *, timeout: float = 5.0) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=timeout)


def start_server(
    *,
    host: str = DEFAULT_HOST,
    port: int = 0,
    startup_timeout: float = DEFAULT_STARTUP_TIMEOUT,
) -> RunningServer:
    """Start the existing FastAPI app on a loopback-only uvicorn server."""
    if not is_loopback_host(host):
        raise ValueError("The desktop launcher only accepts a loopback host.")
    if not 0 <= port <= 65535:
        raise ValueError("Port must be between 0 and 65535.")

    import uvicorn
    from .web import app

    actual_port = find_free_port(host) if port == 0 else port
    config = uvicorn.Config(
        app,
        host=host,
        port=actual_port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="localrag-server", daemon=True)
    thread.start()
    url = local_url(host, actual_port)
    try:
        wait_for_server(url, timeout=startup_timeout, is_alive=thread.is_alive)
    except Exception:
        server.should_exit = True
        thread.join(timeout=5)
        raise
    return RunningServer(url=url, server=server, thread=thread)


def load_webview() -> Any | None:
    """Load optional pywebview without making it a required import."""
    try:
        import webview
    except Exception as error:
        LOGGER.debug("pywebview is unavailable: %s", error)
        return None
    return webview


def open_in_native_window(
    url: str,
    *,
    title: str = DEFAULT_WINDOW_TITLE,
    webview_module: Any | None = None,
    browser_open: Callable[[str], bool] | None = None,
) -> str:
    """Open *url* in pywebview, falling back to the default browser.

    A browser fallback is deliberately retained for installations without a
    native webview backend and for hosts where pywebview cannot initialize.
    """
    browser_open = webbrowser.open if browser_open is None else browser_open
    webview_module = load_webview() if webview_module is None else webview_module
    if webview_module is not None:
        try:
            webview_module.create_window(title, url, width=1280, height=820)
            webview_module.start()
            return "webview"
        except Exception as error:
            LOGGER.warning("Native window unavailable; opening a browser instead: %s", error)
    browser_open(url)
    return "browser"


def wait_for_server_shutdown(running: RunningServer) -> None:
    """Keep the browser fallback alive until the server exits or Ctrl+C is used."""
    try:
        while running.thread.is_alive():
            running.thread.join(timeout=0.25)
    except KeyboardInterrupt:
        LOGGER.info("Stopping LocalRAG")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run LocalRAG in a desktop window")
    parser.add_argument("--host", default=DEFAULT_HOST, help="loopback host to bind")
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="TCP port, or 0 to choose an available local port",
    )
    parser.add_argument(
        "--browser",
        action="store_true",
        help="use the default browser instead of pywebview",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Start LocalRAG's API and keep its desktop window open."""
    arguments = _parser().parse_args(argv)
    if not is_loopback_host(arguments.host):
        _parser().error("binding beyond localhost is not supported by the desktop launcher")

    running = start_server(host=arguments.host, port=arguments.port)
    try:
        if arguments.browser:
            webbrowser.open(running.url)
            wait_for_server_shutdown(running)
        else:
            mode = open_in_native_window(running.url)
            if mode == "browser":
                wait_for_server_shutdown(running)
    finally:
        running.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
