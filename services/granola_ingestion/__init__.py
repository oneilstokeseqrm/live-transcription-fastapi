"""Granola.ai transcript ingestion module.

Phase 2c (this PR) ships the HTTP API client, error codes, and Pydantic
models — the inert primitives Phase 2d's adapter will compose into a
DBOS-scheduled poll cycle. Nothing in this package is wired into a code
path yet; the module deploys to production as dead weight until Phase 2d
lands.

See ``tasks/granola-integration-plan.md`` §Phase 2c for the spec and
``docs/superpowers/specs/2026-05-22-granola-integration-brainstorm-decisions.md``
§"Empirical Granola API findings" for the response-shape ground truth.

Public API exports are kept minimal (no aliases, no rebinding) so
Phase 2d can import via either form without semantic drift::

    from services.granola_ingestion import GranolaAPIClient, GranolaError
    # OR
    from services.granola_ingestion.api_client import GranolaAPIClient
    from services.granola_ingestion.errors import GranolaError
"""

from __future__ import annotations

from .api_client import GranolaAPIClient
from .errors import GranolaError, GranolaErrorCode
from .models import (
    Attendee,
    CalendarEvent,
    FolderMembership,
    GranolaFolder,
    GranolaNoteDetail,
    GranolaNoteSummary,
    TranscriptTurn,
)

__all__ = [
    "Attendee",
    "CalendarEvent",
    "FolderMembership",
    "GranolaAPIClient",
    "GranolaError",
    "GranolaErrorCode",
    "GranolaFolder",
    "GranolaNoteDetail",
    "GranolaNoteSummary",
    "TranscriptTurn",
]
