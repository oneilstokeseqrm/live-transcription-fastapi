"""Granola HTTP API client (Phase 2c).

A thin async wrapper around the three Granola endpoints the ingestion
adapter needs:

* ``GET /v1/folders``                — list a connected user's folders
* ``GET /v1/notes?folder_id=…``      — list note metadata in a folder
* ``GET /v1/notes/{note_id}?…``      — fetch one note's full payload

This module is **pure HTTP**. It does NOT import :mod:`services.vault`,
does NOT touch Postgres, and does NOT carry tenant context. The
``api_key`` is passed in as a constructor argument by callers (in
Phase 2d the caller is :mod:`services.granola_ingestion.adapter`,
which obtains the decrypted key from the vault module on behalf of a
specific ``(tenant_id, user_id)``).

Per LOCKED-23 the api_key is a per-USER secret. We never log it,
never include it in error messages, and never expose it on the client
instance's ``__repr__``.

Per the Phase 0 empirical correction (see
``docs/superpowers/specs/2026-05-22-granola-integration-brainstorm-decisions.md``
§"Empirical Granola API findings"), the live API uses
``https://public-api.granola.ai/v1`` (not ``api.granola.ai``) and
``created_after`` (not ``since``) for time-window filtering.

Tests live in ``tests/unit/granola_ingestion/test_api_client.py`` and
use ``httpx.MockTransport`` (per
``feedback_test_pattern_no_docker.md`` — no Docker, no network).
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime
from typing import Any, Iterable

import httpx
from pydantic import BaseModel, ValidationError

from .errors import GranolaError, GranolaErrorCode
from .models import GranolaFolder, GranolaNoteDetail, GranolaNoteSummary


logger = logging.getLogger(__name__)


_DEFAULT_BASE_URL = "https://public-api.granola.ai/v1"
_DEFAULT_TIMEOUT_SECONDS = 30.0

# Per Phase 2c plan: exponential backoff 1s → 2s → 4s → 8s with jitter,
# max 4 retries (= up to 5 attempts total).
_DEFAULT_MAX_RETRIES = 4
_DEFAULT_RETRY_BASE_DELAY_S = 1.0

# Separate budget for consecutive 429 responses. A 429 indicates the SERVER
# is asking us to slow down (not a transient transport failure), so we
# don't consume the main retry budget on it — but sustained rate-limit
# windows still need to surface as a structured failure rather than
# hang the poll cycle indefinitely. Reset to zero on any non-429 response.
_DEFAULT_MAX_CONSECUTIVE_429S = 3

# Defensive ceiling on pagination loops. Granola's /notes returns at most
# ~100 entries per page in our usage and is filtered by ``created_after``
# (typically the credential's last_polled_at), so realistic pages-per-call
# are 0-2. 20 pages = 2000 notes per cycle is well beyond any realistic
# 5-min window; hitting it signals a cursor that isn't advancing (a real
# bug, not a busy folder).
_DEFAULT_MAX_PAGES = 20

# Cap how long we'll honor a server-suggested Retry-After. Granola's docs
# advertise 5 req/sec sustained + 300/min — well above what the adapter
# generates — so a Retry-After larger than this is almost certainly a
# misconfigured upstream or a defensive blanket value during an outage;
# falling back to the regular 5xx-style backoff is safer than blocking
# the event loop for minutes.
_RETRY_AFTER_CAP_SECONDS = 60.0


def _build_jittered_delay(base_delay_s: float, attempt_index: int) -> float:
    """Return ``2**attempt_index * base_delay_s`` with a +0..50% jitter.

    Exposed as a module-level function so tests can monkey-patch a
    deterministic version when asserting the retry sequencing without
    real wall-clock waits.
    """
    delay = base_delay_s * (2 ** attempt_index)
    return delay + random.uniform(0.0, delay * 0.5)


def _parse_retry_after(header_value: str | None, *, fallback_s: float) -> float:
    """Parse an HTTP ``Retry-After`` header value to seconds.

    Granola almost certainly returns the integer-seconds form; the
    HTTP-date form is supported defensively. On any parse failure or
    if the value exceeds :data:`_RETRY_AFTER_CAP_SECONDS`, returns
    ``fallback_s`` so a misconfigured upstream can't hang the caller.
    """
    if not header_value:
        return fallback_s
    try:
        seconds = float(header_value.strip())
    except (TypeError, ValueError):
        # HTTP-date form (RFC 7231 §7.1.3) is allowed; parsing it
        # accurately requires timezone-aware datetime handling. We
        # don't currently expect Granola to use it, so we fall back
        # to the standard backoff rather than ship a partial parser.
        logger.debug(
            "granola_api_client: non-numeric Retry-After header (%r); using fallback",
            header_value,
        )
        return fallback_s
    if seconds <= 0:
        return fallback_s
    if seconds > _RETRY_AFTER_CAP_SECONDS:
        logger.warning(
            "granola_api_client: Retry-After=%.1fs exceeds cap %.1fs; using cap",
            seconds,
            _RETRY_AFTER_CAP_SECONDS,
        )
        return _RETRY_AFTER_CAP_SECONDS
    return seconds


def _format_created_after(value: datetime) -> str:
    """Convert a Python ``datetime`` to the ISO-8601 ``Z`` form Granola accepts.

    Phase 0 empirically validated that ``created_after`` accepts
    ``YYYY-MM-DDTHH:MM:SSZ``-style strings. We normalize aware
    datetimes to UTC and emit the trailing ``Z``; naive datetimes
    are assumed to already be UTC (Granola has no tenant-local
    semantics).
    """
    if value.tzinfo is not None:
        # Convert any aware datetime to UTC before formatting.
        from datetime import timezone

        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.isoformat(timespec="seconds") + "Z"


class GranolaAPIClient:
    """Async HTTP client for the Granola Personal API.

    The client owns its underlying :class:`httpx.AsyncClient` by default
    and provides ``aclose()`` (and the async-context-manager protocol)
    for cleanup. Tests inject a transport-wrapped ``AsyncClient`` via
    the ``http_client`` constructor argument — when ``http_client`` is
    supplied, the client does NOT own it and won't close it.

    Example::

        async with GranolaAPIClient(api_key=key) as client:
            folders = await client.list_folders()
            notes = await client.list_notes(folder_id=folders[0].id)
            detail = await client.get_note_detail(notes[0].id)

    Per LOCKED-23 the api_key is treated as opaque secret material.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
        http_client: httpx.AsyncClient | None = None,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        retry_base_delay_s: float = _DEFAULT_RETRY_BASE_DELAY_S,
        max_consecutive_429s: int = _DEFAULT_MAX_CONSECUTIVE_429S,
        max_pages: int = _DEFAULT_MAX_PAGES,
    ) -> None:
        if not api_key:
            # Fail loud at construction so the empty/missing-key case
            # doesn't masquerade as a 401 at call time.
            raise ValueError("GranolaAPIClient requires a non-empty api_key")
        self._api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._max_retries = max_retries
        self._retry_base_delay_s = retry_base_delay_s
        self._max_consecutive_429s = max_consecutive_429s
        self._max_pages = max_pages
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(timeout=timeout)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def aclose(self) -> None:
        """Close the underlying HTTP client if we own it.

        Idempotent. Tests that inject their own ``AsyncClient`` retain
        ownership and are responsible for closing it.
        """
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "GranolaAPIClient":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.aclose()

    def __repr__(self) -> str:
        # Explicitly NEVER include the api_key — repr leaks through
        # logs and exception traces by default.
        return f"GranolaAPIClient(base_url={self.base_url!r})"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def list_folders(self) -> list[GranolaFolder]:
        """``GET /v1/folders`` → list all folders the API key can see.

        Per Phase 0 empirical verification, the live response is
        ``{folders: [...], hasMore: bool, cursor: str}``. We unwrap
        ``folders``, follow the cursor across pages, and validate each
        entry against :class:`GranolaFolder`. Without pagination,
        accounts with many folders would silently lose folders past
        page 1 — and validation/folder-pick UIs built on this would
        miss valid folders for those accounts.
        """
        return await self._get_paginated(
            path="/folders",
            base_params={},
            field_name="folders",
            model_cls=GranolaFolder,
        )

    async def list_notes(
        self,
        *,
        folder_id: str,
        created_after: datetime | None = None,
        limit: int = 100,
    ) -> list[GranolaNoteSummary]:
        """``GET /v1/notes?folder_id=...&created_after=...`` → note summaries.

        ``created_after`` (Phase 0 empirical: the time-filter parameter
        Granola actually accepts; the brainstorm doc's ``since`` was
        wrong) restricts to notes created strictly after the given
        instant — passing the credential's ``last_polled_at`` keeps each
        poll cycle work-bounded.

        **Transparent cursor pagination.** Granola's ``/notes`` response
        is the same ``{notes, hasMore, cursor}`` wrapper as ``/folders``;
        when ``hasMore`` is true, we re-request with ``cursor=<value>``
        and append until exhausted. This matters during initial-backfill
        scenarios (a fresh credential with ``last_polled_at=NULL`` may
        match thousands of notes); without it later pages would be
        silently dropped and never re-fetched because the next poll
        advances ``last_polled_at`` past them.

        Phase 0 empirically verified the wrapper shape on ``/folders``;
        the ``?cursor=<value>`` request-side parameter is the canonical
        next-page pattern across Granola-class APIs (Stripe / Linear /
        Notion idiom) and is the working assumption pending Phase 4
        production verification. Capped at ``self._max_pages`` so a
        non-advancing cursor (a real bug, not a busy folder) fails
        loud as :attr:`GranolaErrorCode.GRANOLA_PARSE_ERROR` instead
        of looping forever.
        """
        base_params: dict[str, str | int] = {
            "folder_id": folder_id,
            "limit": limit,
        }
        if created_after is not None:
            base_params["created_after"] = _format_created_after(created_after)

        return await self._get_paginated(
            path="/notes",
            base_params=base_params,
            field_name="notes",
            model_cls=GranolaNoteSummary,
        )

    async def get_note_detail(self, note_id: str) -> GranolaNoteDetail:
        """``GET /v1/notes/{note_id}?include=transcript`` → full note payload.

        ``include=transcript`` is required to get the speaker-turn array
        — without it Granola returns metadata only. A 404 here means
        the note was deleted/moved between ``list_notes`` and this call
        (relatively common with snapshot-on-ingest); the adapter should
        treat it as a recoverable per-note failure.
        """
        body = await self._request_json(
            "GET",
            f"/notes/{note_id}",
            params={"include": "transcript"},
            endpoint_kind="note",
        )
        return self._parse_one(body, GranolaNoteDetail)

    # ------------------------------------------------------------------
    # Internal HTTP + parsing
    # ------------------------------------------------------------------

    async def _get_paginated(
        self,
        *,
        path: str,
        base_params: dict[str, Any],
        field_name: str,
        model_cls: type[BaseModel],
    ) -> list[Any]:
        """Walk Granola's ``{<field>, hasMore, cursor}`` wrapper across pages.

        Used by both :meth:`list_folders` and :meth:`list_notes` — keeps
        their pagination behavior identical and prevents one endpoint
        from silently truncating while the other paginates correctly.
        Defensively accepts a bare list (no wrapper) for forward
        compatibility with possible future endpoint shapes.
        """
        collected: list[Any] = []
        cursor: str | None = None
        for _ in range(self._max_pages):
            params = dict(base_params)
            if cursor:
                params["cursor"] = cursor

            body = await self._request_json(
                "GET", path, params=params, endpoint_kind="folder"
            )

            if isinstance(body, dict):
                items = body.get(field_name)
                has_more = bool(body.get("hasMore"))
                next_cursor = body.get("cursor") or None
            else:
                items = body
                has_more = False
                next_cursor = None

            collected.extend(self._parse_list(items, model_cls, field_name=field_name))

            if not has_more:
                return collected

            # ``hasMore`` is true; we need a cursor to advance. If the
            # server claims more pages but doesn't give us one, that's
            # a malformed pagination response — fail loud rather than
            # truncate.
            if not next_cursor:
                raise GranolaError(
                    GranolaErrorCode.GRANOLA_PARSE_ERROR,
                    f"Granola {path} response set hasMore=true but omitted cursor",
                )

            # Cursor must advance between pages; if it doesn't we'd
            # spin forever. This shouldn't happen on a well-behaved
            # server but the check is cheap.
            if next_cursor == cursor:
                raise GranolaError(
                    GranolaErrorCode.GRANOLA_PARSE_ERROR,
                    f"Granola {path} cursor did not advance between pages",
                )
            cursor = next_cursor

        # Exhausted page budget. Either the response has more entries
        # than our ceiling (operationally implausible at MVP scale) or
        # the cursor is misbehaving in a way the per-page guard didn't
        # catch. Surface as parse error so the adapter can bound it.
        raise GranolaError(
            GranolaErrorCode.GRANOLA_PARSE_ERROR,
            f"Granola {path} pagination exceeded {self._max_pages} pages",
        )

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        endpoint_kind: str,
    ) -> Any:
        """Issue an authenticated request, retry transient failures, return JSON.

        Retry policy (per Phase 2c plan):

        * 5xx + :class:`httpx.TimeoutException` + :class:`httpx.ConnectError`
          → exponential backoff with jitter, up to ``self._max_retries``.
        * 429 → honor ``Retry-After``; does NOT consume the main retry
          budget (the server is asking us to slow down, not a transport
          failure) — but a SEPARATE consecutive-429 budget caps repeated
          rate-limit responses so sustained throttling surfaces as
          :attr:`GranolaErrorCode.GRANOLA_RATE_LIMITED` instead of
          looping forever. Resets on any non-429 response.
        * 401/403 → :attr:`GranolaErrorCode.GRANOLA_AUTH_FAILED`, no retry.
        * 404 on ``endpoint_kind='folder'`` →
          :attr:`GranolaErrorCode.GRANOLA_FOLDER_NOT_FOUND`, no retry
          (Phase 2d treats this as credential-level breakage).
        * 404 on ``endpoint_kind='note'`` →
          :attr:`GranolaErrorCode.GRANOLA_NOTE_NOT_FOUND`, no retry
          (Phase 2d treats this as a per-note skip — a single deleted
          note must NOT mark the whole credential offline).
        * Other 4xx → :attr:`GranolaErrorCode.GRANOLA_HTTP_ERROR`, no retry
          (the caller has an input bug; retrying won't help).
        """
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
        }

        retry_attempt = 0
        consecutive_429s = 0
        while True:
            try:
                response = await self._client.request(
                    method, url, params=params, headers=headers
                )
            except httpx.TimeoutException as exc:
                if retry_attempt >= self._max_retries:
                    raise GranolaError(
                        GranolaErrorCode.GRANOLA_TIMEOUT,
                        f"Granola request timed out after {retry_attempt + 1} attempt(s)",
                        cause=exc,
                    ) from exc
                await self._sleep_backoff(retry_attempt)
                retry_attempt += 1
                continue
            except httpx.ConnectError as exc:
                # Transport-level connect failure (DNS, TCP, TLS); same
                # retry posture as 5xx. We surface as GRANOLA_5XX rather
                # than minting a separate code because the adapter's
                # response is identical (transient outage handling).
                if retry_attempt >= self._max_retries:
                    raise GranolaError(
                        GranolaErrorCode.GRANOLA_5XX,
                        f"Granola connect error after {retry_attempt + 1} attempt(s)",
                        cause=exc,
                    ) from exc
                await self._sleep_backoff(retry_attempt)
                retry_attempt += 1
                continue

            status = response.status_code

            if 200 <= status < 300:
                try:
                    return response.json()
                except ValueError as exc:
                    # 2xx body that isn't valid JSON — Granola promises
                    # JSON on all 200s, so this is a real shape failure.
                    raise GranolaError(
                        GranolaErrorCode.GRANOLA_PARSE_ERROR,
                        "Granola returned a non-JSON 2xx body",
                        http_status=status,
                        cause=exc,
                    ) from exc

            if status in (401, 403):
                raise GranolaError(
                    GranolaErrorCode.GRANOLA_AUTH_FAILED,
                    "Granola rejected the API key (HTTP %d)" % status,
                    http_status=status,
                )

            if status == 404:
                if endpoint_kind == "note":
                    raise GranolaError(
                        GranolaErrorCode.GRANOLA_NOTE_NOT_FOUND,
                        "Granola note not found (may have been deleted)",
                        http_status=status,
                    )
                raise GranolaError(
                    GranolaErrorCode.GRANOLA_FOLDER_NOT_FOUND,
                    "Granola folder not found",
                    http_status=status,
                )

            if status == 429:
                consecutive_429s += 1
                if consecutive_429s > self._max_consecutive_429s:
                    # Sustained rate-limit window — stop pretending it's
                    # transient and surface to the caller. Phase 2d's
                    # adapter classifies this as a credential-level
                    # transient error (not a permanent breakage).
                    raise GranolaError(
                        GranolaErrorCode.GRANOLA_RATE_LIMITED,
                        f"Granola returned 429 on {consecutive_429s} consecutive attempts",
                        http_status=status,
                    )
                # Fallback grows with consecutive 429s (not retry_attempt,
                # which is never incremented on this branch). Without this
                # the headerless-429 fallback would sleep the same base
                # delay on every consecutive 429 and exhaust the budget
                # well before the intended 1s/2s/4s ramp.
                fallback = _build_jittered_delay(
                    self._retry_base_delay_s, consecutive_429s - 1
                )
                retry_after = _parse_retry_after(
                    response.headers.get("Retry-After"),
                    fallback_s=fallback,
                )
                logger.info(
                    "granola_api_client: 429 from %s (%d consecutive); sleeping %.2fs",
                    path,
                    consecutive_429s,
                    retry_after,
                )
                await asyncio.sleep(retry_after)
                # 429 does NOT consume the MAIN retry budget; the
                # consecutive-429 counter above bounds the loop.
                continue

            # Any non-429 response — including 5xx that we'll retry —
            # means rate-limit pressure has eased, so reset the
            # consecutive-429 counter.
            consecutive_429s = 0

            if 500 <= status < 600:
                if retry_attempt >= self._max_retries:
                    raise GranolaError(
                        GranolaErrorCode.GRANOLA_5XX,
                        f"Granola returned {status} after {retry_attempt + 1} attempt(s)",
                        http_status=status,
                    )
                await self._sleep_backoff(retry_attempt)
                retry_attempt += 1
                continue

            # Any other 4xx (400 VALIDATION_ERROR on malformed IDs, 422,
            # etc.) is a caller bug; retrying won't help.
            raise GranolaError(
                GranolaErrorCode.GRANOLA_HTTP_ERROR,
                f"Granola rejected the request (HTTP {status})",
                http_status=status,
            )

    async def _sleep_backoff(self, attempt_index: int) -> None:
        """Sleep for the jittered exponential backoff interval.

        Wrapped as an instance method so subclasses/tests can override
        the wait without monkeypatching the module-level helper.
        """
        delay = _build_jittered_delay(self._retry_base_delay_s, attempt_index)
        await asyncio.sleep(delay)

    # ------------------------------------------------------------------
    # Pydantic adaptation
    # ------------------------------------------------------------------

    def _parse_list(
        self,
        items: Any,
        model_cls: type[BaseModel],
        *,
        field_name: str,
    ) -> list[Any]:
        """Validate a list of dicts against ``model_cls``; raise PARSE_ERROR on miss."""
        if items is None or not isinstance(items, Iterable) or isinstance(items, (str, bytes)):
            raise GranolaError(
                GranolaErrorCode.GRANOLA_PARSE_ERROR,
                f"Granola response missing list field {field_name!r}",
            )
        try:
            return [model_cls.model_validate(item) for item in items]
        except ValidationError as exc:
            raise GranolaError(
                GranolaErrorCode.GRANOLA_PARSE_ERROR,
                f"Granola {field_name} entry failed validation: {exc.error_count()} error(s)",
                cause=exc,
            ) from exc

    def _parse_one(self, body: Any, model_cls: type[BaseModel]) -> Any:
        """Validate a single dict against ``model_cls``; raise PARSE_ERROR on miss."""
        try:
            return model_cls.model_validate(body)
        except ValidationError as exc:
            raise GranolaError(
                GranolaErrorCode.GRANOLA_PARSE_ERROR,
                f"Granola response failed validation: {exc.error_count()} error(s)",
                cause=exc,
            ) from exc
