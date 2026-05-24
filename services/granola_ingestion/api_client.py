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
    ) -> None:
        if not api_key:
            # Fail loud at construction so the empty/missing-key case
            # doesn't masquerade as a 401 at call time.
            raise ValueError("GranolaAPIClient requires a non-empty api_key")
        self._api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._max_retries = max_retries
        self._retry_base_delay_s = retry_base_delay_s
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
        ``folders`` and validate each entry against
        :class:`GranolaFolder`. Pagination via ``cursor`` is deferred
        to Phase 2.1 (current scale: ~tens of folders per user).
        """
        body = await self._request_json("GET", "/folders", endpoint_kind="folder")
        items = body.get("folders") if isinstance(body, dict) else body
        return self._parse_list(items, GranolaFolder, field_name="folders")

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

        Pagination is deferred to Phase 2.1; current scale (3 design
        partners on 5-min cadence with new-note filtering) makes a
        single page sufficient.
        """
        params: dict[str, str | int] = {
            "folder_id": folder_id,
            "limit": limit,
        }
        if created_after is not None:
            params["created_after"] = _format_created_after(created_after)

        body = await self._request_json(
            "GET", "/notes", params=params, endpoint_kind="folder"
        )
        # Granola is consistent with /folders and wraps lists; defensively
        # accept a bare list too in case a future endpoint version drops
        # the wrapper.
        items = body.get("notes") if isinstance(body, dict) else body
        return self._parse_list(items, GranolaNoteSummary, field_name="notes")

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
        * 429 → honor ``Retry-After``; does NOT consume the retry budget
          (a rate-limited request is the SERVER asking us to slow down,
          not a transient transport failure).
        * 401/403 → :attr:`GranolaErrorCode.GRANOLA_AUTH_FAILED`, no retry.
        * 404 → :attr:`GranolaErrorCode.GRANOLA_FOLDER_NOT_FOUND`, no retry
          (the message clarifies whether the missing resource is a folder
          or a note, based on ``endpoint_kind``).
        * Other 4xx → :attr:`GranolaErrorCode.GRANOLA_HTTP_ERROR`, no retry
          (the caller has an input bug; retrying won't help).
        """
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
        }

        retry_attempt = 0
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
                    message = "Granola note not found (may have been deleted)"
                else:
                    message = "Granola folder not found"
                raise GranolaError(
                    GranolaErrorCode.GRANOLA_FOLDER_NOT_FOUND,
                    message,
                    http_status=status,
                )

            if status == 429:
                fallback = _build_jittered_delay(
                    self._retry_base_delay_s, retry_attempt
                )
                retry_after = _parse_retry_after(
                    response.headers.get("Retry-After"),
                    fallback_s=fallback,
                )
                logger.info(
                    "granola_api_client: 429 from %s; sleeping %.2fs",
                    path,
                    retry_after,
                )
                await asyncio.sleep(retry_after)
                # Intentional: 429 does NOT consume the retry budget.
                continue

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
