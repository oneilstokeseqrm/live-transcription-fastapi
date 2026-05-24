"""Granola.ai transcript ingestion module.

Phase 2c shipped the HTTP API client, error codes, and Pydantic models —
the inert primitives Phase 2d's adapter composes into a per-credential
poll cycle. Phase 2d adds the adapter, Path 2 attendee classification,
and the IngestionOutcome enum.

After Phase 2d the module is still **inert at module-import time**: no
scheduler invokes ``run_one_cycle`` yet. Phase 2e wires the Railway-cron
→ DBOS-queue → ``run_one_cycle`` invocation.

See:
* ``tasks/granola-integration-plan.md`` §Phase 2c + §Phase 2d for the
  spec.
* ``docs/superpowers/specs/2026-05-22-granola-integration-brainstorm-decisions.md``
  §"Empirical Granola API findings" for the response-shape ground truth.

Public API exports are kept minimal (no aliases, no rebinding) so
callers can import via either form without semantic drift::

    from services.granola_ingestion import GranolaAPIClient, run_one_cycle
    # OR
    from services.granola_ingestion.api_client import GranolaAPIClient
    from services.granola_ingestion.adapter import run_one_cycle
"""

from __future__ import annotations

from .adapter import CycleResult, process_note, run_one_cycle
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
from .outcomes import IngestionOutcome
from .path2 import (
    AttendeeClassification,
    PathTwoDecision,
    Scenario,
    classify_attendees,
    decide_scenario,
    unique_unknown_business_domains,
)

__all__ = [
    "Attendee",
    "AttendeeClassification",
    "CalendarEvent",
    "CycleResult",
    "FolderMembership",
    "GranolaAPIClient",
    "GranolaError",
    "GranolaErrorCode",
    "GranolaFolder",
    "GranolaNoteDetail",
    "GranolaNoteSummary",
    "IngestionOutcome",
    "PathTwoDecision",
    "Scenario",
    "TranscriptTurn",
    "classify_attendees",
    "decide_scenario",
    "process_note",
    "run_one_cycle",
    "unique_unknown_business_domains",
]
