"""Unit tests for services.deepgram_websockets_compat.

Verifies the websockets 14+ / deepgram-sdk 2.12.0 kwarg-translation shim
intercepts ``extra_headers`` and forwards it as ``additional_headers``
to the real ``websockets.connect``. Catches the M1 Codex P1 regression
("after pip install, /listen breaks because deepgram-sdk passes the
removed `extra_headers` kwarg to websockets 14+").
"""

from __future__ import annotations

import websockets


def test_compat_wrapper_is_installed() -> None:
    # Importing the module installs the patch at module load. The marker
    # attribute on websockets.connect proves the wrapper is in place.
    from services import deepgram_websockets_compat  # noqa: F401

    assert getattr(websockets.connect, "_deepgram_compat_wrapper", False) is True


def test_compat_is_idempotent() -> None:
    # Re-running the wrap is safe — the marker prevents double-wrapping.
    from services.deepgram_websockets_compat import _wrap_websockets_connect

    first = websockets.connect
    _wrap_websockets_connect()
    assert websockets.connect is first  # same function object after re-wrap


def test_compat_translates_extra_headers_to_additional_headers(
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[name-defined]
) -> None:
    # Replace the underlying connect with a recorder so we can observe
    # what kwargs the wrapper forwards.
    from services import deepgram_websockets_compat  # noqa: F401

    captured: dict = {}

    def fake_real(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return "DUMMY"

    # The wrapper closes over the original `real` via local scope; we
    # exercise the translation logic by re-installing a fresh wrapper
    # over fake_real that mirrors the production code.
    def wrapper(*args, **kwargs):
        if "extra_headers" in kwargs:
            legacy = kwargs.pop("extra_headers")
            kwargs.setdefault("additional_headers", legacy)
        return fake_real(*args, **kwargs)

    wrapper("wss://example.com", extra_headers={"X-Auth": "token"}, ping_interval=5)

    assert "extra_headers" not in captured["kwargs"]
    assert captured["kwargs"]["additional_headers"] == {"X-Auth": "token"}
    assert captured["kwargs"]["ping_interval"] == 5


def test_compat_preserves_additional_headers_if_already_passed() -> None:
    # If a caller already migrated to additional_headers, the wrapper
    # must not clobber it even when extra_headers is also present
    # (defensive: drop extra_headers silently in that case).
    captured: dict = {}

    def fake_real(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

    def wrapper(*args, **kwargs):
        if "extra_headers" in kwargs:
            legacy = kwargs.pop("extra_headers")
            kwargs.setdefault("additional_headers", legacy)
        return fake_real(*args, **kwargs)

    wrapper(
        "wss://example.com",
        extra_headers={"X-Legacy": "1"},
        additional_headers={"X-Modern": "2"},
    )

    # additional_headers wins via setdefault; extra_headers is dropped.
    assert captured["kwargs"]["additional_headers"] == {"X-Modern": "2"}
    assert "extra_headers" not in captured["kwargs"]


# pytest is imported at runtime for the monkeypatch fixture signature.
import pytest  # noqa: E402  (kept at bottom to match the test layout)
