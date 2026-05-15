"""Compatibility shim: ``websockets`` 14+ ↔ ``deepgram-sdk`` 2.12.0.

``websockets`` 14.0 renamed the top-level ``websockets.connect`` kwarg
``extra_headers`` to ``additional_headers``. ``deepgram-sdk==2.12.0``
still calls ``websockets.connect(..., extra_headers=...)`` at
``deepgram/_utils.py:230``, which raises ``TypeError`` at runtime under
``websockets>=14``.

The DBOS substrate (Phase 1.5 M1) requires ``websockets>=14``, so we
cannot pin ``websockets`` back. The clean long-term answer is upgrading
``deepgram-sdk`` (5+ major versions ahead at 7.1.1), but that is a
separate work item with its own review surface.

This shim wraps ``websockets.connect`` to translate the legacy kwarg.
Idempotent on repeated imports. Remove when ``deepgram-sdk`` upgrades.
"""

from __future__ import annotations

import websockets


def _wrap_websockets_connect() -> None:
    """Replace ``websockets.connect`` with a kwarg-translating wrapper.

    Calls with ``extra_headers=...`` get rewritten to
    ``additional_headers=...`` before delegating to the real connect.
    If ``additional_headers`` is already present, ``extra_headers`` is
    dropped silently (the caller already migrated).
    """
    real = websockets.connect
    if getattr(real, "_deepgram_compat_wrapper", False):
        return

    def _connect_compat(*args, **kwargs):  # type: ignore[no-untyped-def]
        if "extra_headers" in kwargs:
            legacy = kwargs.pop("extra_headers")
            kwargs.setdefault("additional_headers", legacy)
        return real(*args, **kwargs)

    _connect_compat._deepgram_compat_wrapper = True  # type: ignore[attr-defined]
    websockets.connect = _connect_compat  # type: ignore[assignment]


_wrap_websockets_connect()
