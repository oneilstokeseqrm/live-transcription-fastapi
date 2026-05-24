"""Granola ingestion module — structured error codes + exception types.

Per LOCKED-33, every Granola API failure surfaces as a :class:`GranolaError`
carrying a :class:`GranolaErrorCode`. The string value of each code is the
canonical wire format; it is persisted to
``public.external_integration_runs.error_code`` (Phase 2d) so downstream
filters and dashboards can match on a stable identifier without parsing
free-form messages.

The codes here are module-prefixed (``granola_*``) so they cannot collide
with vault error codes (``vault_*``) when both modules' failures land in
the same audit/log tables.
"""

from __future__ import annotations

from enum import Enum


class GranolaErrorCode(str, Enum):
    """Structured failure modes for the Granola HTTP API client.

    Mapping rules used by :class:`GranolaAPIClient`:

    * 401 → :attr:`GRANOLA_AUTH_FAILED` (no retry — auth failures don't
      improve with retry; mark credential ``status='revoked'`` upstream).
    * 404 on a folder-scoped call → :attr:`GRANOLA_FOLDER_NOT_FOUND`
      (no retry — folder was deleted; mark ``status='error'`` upstream).
    * 429 → :attr:`GRANOLA_RATE_LIMITED` (retry, honoring ``Retry-After``).
    * 5xx → :attr:`GRANOLA_5XX` (retry with exponential backoff).
    * :class:`httpx.TimeoutException` → :attr:`GRANOLA_TIMEOUT` (retry).
    * Pydantic validation failure on a 2xx body →
      :attr:`GRANOLA_PARSE_ERROR` (no retry — the body shape is wrong;
      retrying won't change it).
    * Any other 4xx (e.g., 400 ``VALIDATION_ERROR`` on a malformed
      folder/note ID, 422) → :attr:`GRANOLA_HTTP_ERROR` (no retry —
      Granola rejected the request itself; the caller has an input bug).

    The plan-spec'd six codes (everything except :attr:`GRANOLA_HTTP_ERROR`)
    come from ``tasks/granola-integration-plan.md`` §Phase 2c.
    :attr:`GRANOLA_HTTP_ERROR` is added so the "Other 4xx → appropriate
    code" requirement in the Phase 2c handoff prompt has a semantically
    honest bucket (reusing ``PARSE_ERROR`` for request-side rejections
    would muddle that code's response-side meaning).
    """

    GRANOLA_AUTH_FAILED = "granola_auth_failed"
    GRANOLA_FOLDER_NOT_FOUND = "granola_folder_not_found"
    GRANOLA_RATE_LIMITED = "granola_429"
    GRANOLA_5XX = "granola_5xx"
    GRANOLA_TIMEOUT = "granola_timeout"
    GRANOLA_PARSE_ERROR = "granola_parse_error"
    GRANOLA_HTTP_ERROR = "granola_http_error"


class GranolaError(Exception):
    """Base exception for all Granola HTTP API client failures.

    Carries a :class:`GranolaErrorCode` so callers can branch on the
    structured code without inspecting the message text. ``http_status``
    is populated when the failure originated from a Granola HTTP response
    (None for client-side failures like timeouts or parse errors).
    """

    def __init__(
        self,
        code: GranolaErrorCode,
        message: str = "",
        *,
        http_status: int | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        if cause is not None:
            self.__cause__ = cause

    def __repr__(self) -> str:
        return (
            f"GranolaError(code={self.code.value!r}, "
            f"http_status={self.http_status!r}, "
            f"message={self.message!r})"
        )
