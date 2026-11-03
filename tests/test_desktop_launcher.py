from types import SimpleNamespace

from localrag import desktop_launcher


def test_loopback_validation_and_ipv6_url() -> None:
    assert desktop_launcher.is_loopback_host("127.0.0.1")
    assert desktop_launcher.is_loopback_host("localhost")
    assert desktop_launcher.is_loopback_host("::1")
    assert not desktop_launcher.is_loopback_host("192.168.1.20")
    assert desktop_launcher.local_url("::1", 8123) == "http://[::1]:8123/"


def test_wait_for_server_retries_until_request_succeeds() -> None:
    attempts: list[str] = []

    def request(url: str) -> None:
        attempts.append(url)
        if len(attempts) < 3:
            raise OSError("not ready")

    desktop_launcher.wait_for_server(
        "http://127.0.0.1:8123/",
        timeout=1,
        request=request,
        sleep=lambda _delay: None,
    )

    assert len(attempts) == 3


def test_native_window_falls_back_when_webview_cannot_start() -> None:
    opened: list[str] = []

    def browser_open(url: str) -> bool:
        opened.append(url)
        return True

    webview = SimpleNamespace(
        create_window=lambda *_args, **_kwargs: None,
        start=lambda: (_ for _ in ()).throw(RuntimeError("backend unavailable")),
    )

    mode = desktop_launcher.open_in_native_window(
        "http://127.0.0.1:8123/",
        webview_module=webview,
        browser_open=browser_open,
    )

    assert mode == "browser"
    assert opened == ["http://127.0.0.1:8123/"]
