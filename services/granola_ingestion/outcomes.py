"""Per-note ingestion outcome enum for the Granola adapter.

Each call to :func:`services.granola_ingestion.adapter.process_note` returns
exactly one :class:`IngestionOutcome` — a tri-state-plus-tri-state shape
(success path, deferred path, skip path, transient failure, permanent failure)
that maps directly to the ``public.external_integration_runs.status`` column
per LOCKED-29 / LOCKED-33 / Q7 of the plan.

The string values are the canonical wire format (they go into the
``status`` column, surface in admin status panels, and are filtered on
in observability dashboards), so they're frozen — any future addition
needs a schema migration coordinated with the Pending Approvals UI and
the consumer-contract verifier.
"""

from __future__ import annotations

from enum import Enum


class IngestionOutcome(str, Enum):
    """Per-note ingestion result for the Granola adapter.

    * :attr:`SUCCESS` — note matched a known business account; envelope was
      built per LOCKED-35/36, passed to :func:`text_clean_service.process`,
      and ``external_integration_runs`` row is recorded with
      ``status='success'`` + ``eq_interaction_id`` set.
    * :attr:`DEFERRED_PENDING_ACCOUNT` — Scenario C: note had business-domain
      attendees but none mapped to a known account. The note metadata is
      captured into ``granola_note_snapshot`` JSONB per LOCKED-44 so the
      next poll cycle (or a future one) can re-process it after the queued
      domain is approved. The note IS NOT ingested as an interaction yet.
    * :attr:`SKIPPED_NO_BUSINESS_ATTENDEES` — Scenario D: all attendees were
      personal-domain (gmail.com, etc.) or internal-domain (the tenant's
      own provider domains). Nothing to enrich; no row is written beyond
      the ``external_integration_runs`` skip marker.
    * :attr:`FAILED` — transient failure (Granola 5xx, timeout, parse error,
      downstream text_clean failure). ``retry_count`` increments; the next
      poll cycle will retry. Once ``retry_count`` exceeds the budget
      (per the plan: 5 attempts), the row transitions to
      :attr:`FAILED_PERMANENT`.
    * :attr:`FAILED_PERMANENT` — retries exhausted. Phase 2e scheduler skips
      this row on subsequent cycles. Operators can manually re-queue via
      the admin endpoint (Phase 2f).
    """

    SUCCESS = "success"
    DEFERRED_PENDING_ACCOUNT = "deferred_pending_account"
    SKIPPED_NO_BUSINESS_ATTENDEES = "skipped_no_business_attendees"
    FAILED = "failed"
    FAILED_PERMANENT = "failed_permanent"
