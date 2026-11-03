import sys
from types import SimpleNamespace

import pytest

from localrag import web


def test_web_refuses_non_loopback_binding_without_explicit_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "argv", ["localrag", "--host", "0.0.0.0"])

    with pytest.raises(SystemExit, match="2"):
        web.main()


def test_web_allows_non_loopback_binding_only_with_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        sys,
        "argv",
        ["localrag", "--host", "0.0.0.0", "--allow-network", "--port", "8123"],
    )
    monkeypatch.setitem(
        sys.modules,
        "uvicorn",
        SimpleNamespace(run=lambda app, **kwargs: calls.append(kwargs)),
    )

    assert web.main() == 0
    assert calls == [{"host": "0.0.0.0", "port": 8123}]
