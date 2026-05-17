"""Atomic materialization for queue approval / mapping (Phase 1.5 M3 + Phase-1-email-pipeline M2).

Runs in a single Postgres transaction:
1. Promote any cold-inbound emails from ``pending_interactions`` (M2 §5.2 Step 4):
   archive duplicates, INSERT raw_interactions + emails + interaction_summaries,
   upsert email_threads atomically per pending row, archive the pending rows.
2. INSERT contacts (one per distinct signal email) with the resolved account_id.
3. INSERT interaction_contact_links for every signal that has an interaction_id
   (meeting case — links use the per-signal summary upsert below).
4. Batch INSERT email-summary links across queues (M2 §5.2 Step 5) — covers the
   cross-queue cold-inbound case where signals on OTHER queues reference an
   interaction that this queue's approval just promoted.
5. UPDATE queue entry to status='mapped'.

The promoted ``interaction_ids`` are included in ``MaterializationResult.interaction_ids``
so the existing Step 6 emit (per-interaction EnvelopeV1.email) fans out the
notification downstream. They are ALSO captured separately in
``MaterializationResult.promoted_interaction_ids`` so the new
``emit_email_promoted_events`` step at workflow END can fire one ``EmailPromoted``
EventBridge event per promoted interaction; eq-email-pipeline subscribes and runs
its full local enrichment retroactively (plan §6).

Moved from ``workers/materialization.py`` in M3. Three M3 changes (preserved) +
two M2 additions:

M3:
- ``INSERT_OUTBOX_SQL`` REMOVED. ``account_provisioning_outbox`` dropped post-M3.
- In-memory ``linked_pairs`` REMOVED. Link INSERT uses
  ``ON CONFLICT (interaction_id, contact_id) DO NOTHING``.
- Materialization REQUIRES Lane 2 raw_interactions to already exist.

M2:
- New Step 4 promote pending_interactions logic.
- ``UPSERT_PLACEHOLDER_SUMMARY_SQL`` now uses composite
  ``ON CONFLICT (tenant_id, interaction_id, summary_type)`` instead of the
  single-column ``ON CONFLICT (interaction_id)``. The single-column UNIQUE on
  ``interaction_summaries.interaction_id`` is dropped by the M1 Prisma migration
  (eq-frontend PR for Phase 1 email pipeline); M2 must ship together with M1
  or this query would fail at runtime with "no unique or exclusion constraint
  matching the ON CONFLICT specification."

Caller (the workflow step OR the inline ``/map`` path) is responsible for
opening the transaction and calling session.commit() / session.rollback().
"""

import uuid
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.account_provisioning.types import MaterializationResult


SELECT_SIGNALS_SQL = text("""
    SELECT id, contact_email, contact_display_name, contact_role,
           interaction_id, source_type
    FROM pending_account_mapping_signals
    WHERE queue_id = :queue_id AND archived_at IS NULL
""")


CHECK_RAW_INTERACTION_EXISTS_SQL = text("""
    SELECT 1 FROM raw_interactions
    WHERE interaction_id = :interaction_id
    LIMIT 1
""")
# Codex P1 2026-05-16 (round 5): the prior fix (track placeholder
# interaction_ids in MaterializationResult) wasn't sufficient — a
# subsequent approval for a different queue entry referencing the
# same interaction_id would see the prior placeholder exists and
# NOT mark it as placeholder, then emit a broken envelope.
#
# Architecturally correct fix: don't write placeholders at all. If
# raw_interactions doesn't have a real row for the signal's
# interaction_id, materialization fails loud. The workflow's Step 5
# fails → DBOS retries (up to 3 attempts with backoff). The /map
# path returns 503 → client retries. Either way, the system waits
# until Lane 2 (intelligence_service) has written the real row
# before materializing.
#
# In normal operation Lane 2 writes raw_interactions when the
# transcript is processed — which is the SAME event that creates
# the queue signal. So the race window where the signal exists
# but raw_interactions doesn't is narrow and self-resolving.


INSERT_CONTACT_SQL = text("""
    INSERT INTO contacts (id, tenant_id, email, first_name, last_name, account_id,
                          source, validation_status, created_at, updated_at)
    VALUES (gen_random_uuid(), :tenant_id, lower(:email), :first_name, :last_name,
            :account_id, :source, 'verified', NOW(), NOW())
    ON CONFLICT (tenant_id, email) DO UPDATE
        SET first_name = COALESCE(contacts.first_name, EXCLUDED.first_name),
            last_name = COALESCE(contacts.last_name, EXCLUDED.last_name),
            account_id = COALESCE(contacts.account_id, EXCLUDED.account_id),
            updated_at = NOW()
    RETURNING id::text, account_id::text
""")
# validation_status='verified' matches the live ContactValidationStatus enum
# (pending | verified | discarded). Materialization runs only when the queue
# resolves an account; the contact is therefore verified, not pending.
#
# account_id is preserved on conflict via COALESCE; the caller checks the
# RETURNED account_id against the materialization's input account_id. A
# mismatch means the contact already belongs to a different account
# (cross-account reassignment is Phase 3 scope — fail loud).


UPSERT_PLACEHOLDER_SUMMARY_SQL = text("""
    INSERT INTO interaction_summaries (
        summary_id, tenant_id, interaction_id, summary_type, created_at, updated_at
    ) VALUES (
        :summary_id, :tenant_id, :interaction_id, :summary_type, NOW(), NOW()
    )
    ON CONFLICT (tenant_id, interaction_id, summary_type) DO UPDATE
        SET updated_at = interaction_summaries.updated_at
    RETURNING summary_id::text
""")
# M2 (Phase-1-email-pipeline) update: the ON CONFLICT target switched from the
# single-column UNIQUE on ``interaction_summaries.interaction_id`` to the new
# composite UNIQUE on ``(tenant_id, interaction_id, summary_type)``. The
# single-column UNIQUE was dropped by the M1 Prisma migration in eq-frontend so
# the table can hold multiple summary variants per interaction. M2 must deploy
# together with M1 or this query fails at runtime ("no unique or exclusion
# constraint matching the ON CONFLICT specification").
#
# The composite remains race-safe: if the summaries-writer service inserts a
# row for the same (tenant, interaction, summary_type) tuple between our
# intent to insert and our actual write, the conflict path fires a no-op
# UPDATE (preserving the existing updated_at) and RETURNING gives us the
# existing summary_id.


# ---------------------------------------------------------------------------
# Phase-1-email-pipeline M2 — pending_interactions promote (plan §5.2 Step 4)
# ---------------------------------------------------------------------------


ARCHIVE_PENDING_DUPLICATES_SQL = text("""
    UPDATE pending_interactions p
    SET archived_at = NOW(),
        archive_reason = 'duplicate_already_in_emails'
    WHERE p.queue_id = CAST(:queue_id AS uuid)
      AND p.archived_at IS NULL
      AND p.internet_message_id IS NOT NULL
      AND EXISTS (
        SELECT 1 FROM emails e
        WHERE e.tenant_id = p.tenant_id
          AND e.internet_message_id = p.internet_message_id
      )
""")
# Step 4-pre (plan §5.2): a rare race — the orchestrator's dedup vs an in-flight
# pending row that a sibling workflow promoted first. Archive the duplicate
# without promoting; EXCLUDED from 4a/4b/4c/4d below by the
# ``archived_at IS NULL`` filter so we don't INSERT raw_interactions without a
# matching emails row.


SELECT_PENDING_TO_PROMOTE_SQL = text("""
    SELECT
        interaction_id::text AS interaction_id,
        tenant_id::text AS tenant_id,
        connected_user_id::text AS connected_user_id,
        raw_text,
        internet_message_id,
        provider_message_id,
        provider,
        subject,
        from_email,
        from_name,
        to_emails,
        cc_emails,
        direction,
        has_attachments,
        sent_at,
        thread_key,
        attachment_metadata,
        processing_tier,
        filter_reason,
        response_time_seconds,
        created_at
    FROM pending_interactions
    WHERE queue_id = CAST(:queue_id AS uuid)
      AND archived_at IS NULL
""")
# Reads the surviving pending rows after the 4-pre dedup filter. Used by Step 4c
# (thread upsert loop) and the post-archive promoted_interaction_ids capture.


PROMOTE_INSERT_RAW_INTERACTIONS_SQL = text("""
    INSERT INTO raw_interactions (
        interaction_id, tenant_id, account_id, interaction_type, raw_text,
        created_at, updated_at
    )
    SELECT
        interaction_id,
        tenant_id,
        CAST(:account_id AS uuid),
        'email',
        raw_text,
        created_at,
        NOW()
    FROM pending_interactions
    WHERE queue_id = CAST(:queue_id AS uuid)
      AND archived_at IS NULL
    ON CONFLICT (interaction_id) DO NOTHING
""")
# Step 4a (plan §5.2): preserve the pre-allocated interaction_id (identity
# continuity through promotion). ON CONFLICT DO NOTHING makes this idempotent
# under DBOS step retry — a partial-success replay re-runs cleanly.


PROMOTE_INSERT_EMAILS_SQL = text("""
    INSERT INTO emails (
        id, interaction_id, tenant_id, account_id, internet_message_id,
        provider_message_id, provider, subject, from_email, from_name,
        to_emails, cc_emails, direction, has_attachments, sent_at,
        thread_id, thread_key, connected_user_id, processing_tier,
        filter_reason, attachment_metadata, response_time_seconds,
        account_provisioning_queue_id, local_enrichment_completed_at,
        created_at, updated_at
    )
    SELECT
        gen_random_uuid(),
        interaction_id,
        tenant_id,
        CAST(:account_id AS uuid),
        internet_message_id,
        provider_message_id,
        provider,
        subject,
        from_email,
        from_name,
        to_emails,
        cc_emails,
        direction,
        has_attachments,
        sent_at,
        NULL,  -- thread_id set in Step 4c after thread upsert
        thread_key,
        connected_user_id,
        processing_tier,
        filter_reason,
        attachment_metadata,
        response_time_seconds,
        CAST(:queue_id AS uuid),
        NULL,  -- handler sets local_enrichment_completed_at after enrichment
        created_at,
        NOW()
    FROM pending_interactions
    WHERE queue_id = CAST(:queue_id AS uuid)
      AND archived_at IS NULL
    ON CONFLICT (tenant_id, internet_message_id) DO NOTHING
    RETURNING interaction_id::text AS interaction_id
""")
# Step 4b (plan §5.2): mirror the existing emails dedup invariant. ON CONFLICT
# DO NOTHING — replay-safe; an earlier attempt's emails row stays, the
# WHERE archived_at IS NULL filter on the source pending_interactions excludes
# already-promoted rows on retry (Step 4e sets archived_at).
#
# RETURNING interaction_id is the orphan-detection mechanism (Codex M2 review
# round-1 P1): if a concurrent workflow inserts an emails row with the same
# internet_message_id between 4-pre and 4b, this INSERT silently DO NOTHINGs
# for the conflicting row. Without RETURNING the caller wouldn't know which
# pending rows actually became emails — and 4a would have inserted orphan
# raw_interactions rows with no matching email. The caller compares
# RETURNING ids against the pending set and DELETEs the orphan raw_interactions
# rows + archives their pending entries as 'duplicate_race_already_in_emails'
# before 4c/4d run.


DELETE_ORPHAN_RAW_INTERACTIONS_SQL = text("""
    DELETE FROM raw_interactions
    WHERE interaction_id = ANY(CAST(:ids AS uuid[]))
""")
# Orphan cleanup (Codex M2 review round-1 P1): raw_interactions rows that 4a
# inserted but whose corresponding 4b emails INSERT hit ON CONFLICT (a
# concurrent workflow committed an emails row with the same
# internet_message_id between 4-pre and 4b). The rows are brand-new and have
# no dependents yet (Step 4c/4d haven't run for them), so DELETE is safe.


ARCHIVE_RACE_LOSER_PENDING_SQL = text("""
    UPDATE pending_interactions
    SET archived_at = NOW(),
        archive_reason = 'duplicate_race_already_in_emails'
    WHERE queue_id = CAST(:queue_id AS uuid)
      AND archived_at IS NULL
      AND interaction_id = ANY(CAST(:ids AS uuid[]))
""")
# Companion to DELETE_ORPHAN_RAW_INTERACTIONS_SQL. The pending row stays as
# an audit trail — same as Step 4-pre's pre-flight duplicate archive, just
# with a distinct archive_reason so post-hoc analysis can distinguish
# pre-flight duplicates from in-txn race-losers.


UPSERT_EMAIL_THREAD_SQL = text("""
    INSERT INTO email_threads (
        id, tenant_id, thread_key, account_id, subject,
        participant_emails, first_message_at, last_message_at,
        message_count, created_at, updated_at
    ) VALUES (
        gen_random_uuid(),
        CAST(:tenant_id AS uuid),
        :thread_key,
        CAST(:account_id AS uuid),
        :subject,
        ARRAY[:from_email]::TEXT[],
        :sent_at,
        :sent_at,
        1,
        NOW(),
        NOW()
    )
    ON CONFLICT (tenant_id, thread_key) DO UPDATE SET
        message_count = email_threads.message_count + 1,
        last_message_at = GREATEST(email_threads.last_message_at, EXCLUDED.last_message_at),
        first_message_at = LEAST(email_threads.first_message_at, EXCLUDED.first_message_at),
        participant_emails = (
            SELECT ARRAY(SELECT DISTINCT unnest(email_threads.participant_emails || EXCLUDED.participant_emails))
        ),
        account_id = COALESCE(email_threads.account_id, EXCLUDED.account_id),
        subject = COALESCE(email_threads.subject, EXCLUDED.subject),
        updated_at = NOW()
    RETURNING id::text
""")
# Step 4c (plan §5.2 + §6.3): atomic upsert on the existing
# (tenant_id, thread_key) UNIQUE index. Called ONCE PER PENDING ROW so
# message_count increments correctly when multiple promoted emails share a
# thread (plan-writing Codex round 2 P0 catch). The single-statement form
# closes the pre-existing SELECT-then-UPSERT race window (Codex round 2 P1).
# Note: this is the EQUIVALENT of eq-email-pipeline's upsert_thread helper
# (M4 rewrites that helper to the same atomic form for the known-account
# path); inlined here as SQL because materialization runs in
# live-transcription-fastapi, not eq-email-pipeline.


UPDATE_EMAIL_THREAD_ID_SQL = text("""
    UPDATE emails
    SET thread_id = CAST(:thread_id AS uuid),
        updated_at = NOW()
    WHERE interaction_id = CAST(:interaction_id AS uuid)
""")
# Step 4c (plan §5.2): fill in the thread_id that Step 4b inserted as NULL.
# Idempotent under retry (UPDATE with same thread_id is a no-op write).


PROMOTE_INSERT_INTERACTION_SUMMARIES_SQL = text("""
    INSERT INTO interaction_summaries (
        summary_id, tenant_id, interaction_id, summary_type,
        ai_workflow_trigger, source, created_at, updated_at
    )
    SELECT
        gen_random_uuid(),
        tenant_id,
        interaction_id,
        'email',
        false,
        provider,
        NOW(),
        NOW()
    FROM pending_interactions
    WHERE queue_id = CAST(:queue_id AS uuid)
      AND archived_at IS NULL
    ON CONFLICT (tenant_id, interaction_id, summary_type) DO NOTHING
""")
# Step 4d (plan §5.2): required by Step 5's link table inserts. Composite
# ON CONFLICT preserves the multi-variant summary model — exactly one
# 'email' summary per (tenant, interaction); other summary_types
# (headline / brief / detailed / persona-specific) can coexist for the
# same interaction. M1 added the composite UNIQUE that this clause targets.


ARCHIVE_PROMOTED_PENDING_SQL = text("""
    UPDATE pending_interactions
    SET archived_at = NOW(),
        archive_reason = 'promoted'
    WHERE queue_id = CAST(:queue_id AS uuid)
      AND archived_at IS NULL
""")
# Step 4e (plan §5.2): archive (NOT delete) the rows we just promoted. The
# Step 4 SELECTs use ``archived_at IS NULL``, so a DBOS step retry that re-runs
# the whole materialization will skip already-promoted rows (idempotency).


# ---------------------------------------------------------------------------
# Phase-1-email-pipeline M2 — cross-queue email-summary link batch (plan §5.2 Step 5)
# ---------------------------------------------------------------------------


BATCH_LINK_EMAIL_SUMMARIES_SQL = text("""
    INSERT INTO interaction_contact_links (link_id, interaction_id, contact_id)
    SELECT
        gen_random_uuid(),
        s.summary_id,
        c.id
    FROM pending_account_mapping_signals sig
    JOIN interaction_summaries s
        ON s.interaction_id = sig.interaction_id
        AND s.tenant_id = sig.tenant_id
        AND s.summary_type = 'email'
    JOIN contacts c
        ON c.email = lower(sig.contact_email)
        AND c.tenant_id = sig.tenant_id
    WHERE
        sig.archived_at IS NULL
        AND (
            sig.queue_id = CAST(:queue_id AS uuid)
            OR sig.interaction_id IN (
                SELECT interaction_id FROM pending_interactions
                WHERE queue_id = CAST(:queue_id AS uuid)
                  AND archive_reason = 'promoted'
            )
        )
    ON CONFLICT (interaction_id, contact_id) DO NOTHING
""")
# Step 5 (plan §5.2): handles the cross-queue cold-inbound case (§8.6) where
# an email's anchor queue is approved AFTER another participant's queue. The
# OR clause picks up signals on OTHER queues whose interaction_id was just
# promoted by THIS approval, so contacts materialized on other queues get
# linked to the now-promoted interaction.
#
# Filter ``summary_type = 'email'`` keeps this batch focused on the email-
# pipeline path and prevents fan-out across multiple summary variants that
# might exist for the same interaction (plan-writing Codex round 3 P1 catch).
# Meeting-summary links are still created inline by the per-signal loop in
# the legacy path below.
#
# Note: the column literally named ``interaction_id`` on
# interaction_contact_links stores ``summary_id`` — Prisma naming artifact,
# documented in tasks/lessons.md.


SELECT_PROMOTED_INTERACTION_IDS_SQL = text("""
    SELECT interaction_id::text AS interaction_id
    FROM pending_interactions
    WHERE queue_id = CAST(:queue_id AS uuid)
      AND archive_reason = 'promoted'
""")
# Post-Step-4e capture of the just-promoted interaction_ids for the workflow's
# emit step (both the existing EnvelopeV1.email fan-out and the new
# EmailPromoted event fan-out).


INSERT_LINK_SQL = text("""
    INSERT INTO interaction_contact_links (link_id, interaction_id, contact_id)
    VALUES (gen_random_uuid(), :summary_id, :contact_id)
    ON CONFLICT (interaction_id, contact_id) DO NOTHING
""")
# Phase 1.5 M2 added UNIQUE INDEX (interaction_id, contact_id), making this
# INSERT replay-safe at the SQL layer. Previously the in-memory linked_pairs
# set provided dedup within a single call; that was replay-broken across
# DBOS step retries (each retry starts with a fresh set). ON CONFLICT
# DO NOTHING is the canonical idempotency mechanism.
#
# Note: the column literally named ``interaction_id`` on this table stores
# ``summary_id`` — Prisma naming artifact, documented in tasks/lessons.md.
# We bind ``:summary_id`` to it for that reason.


UPDATE_QUEUE_SQL = text("""
    UPDATE pending_account_mappings
    SET status = 'mapped',
        resolved_account_id = :account_id,
        mapped_at = NOW(),
        updated_at = NOW()
    WHERE id = :queue_id
""")


def _split_name(display_name: str | None) -> tuple[str | None, str | None]:
    if not display_name:
        return (None, None)
    parts = display_name.strip().split()
    if not parts:
        return (None, None)
    if len(parts) == 1:
        return (parts[0], None)
    return (parts[0], " ".join(parts[1:]))


async def materialize_account_approval(
    session: AsyncSession,
    tenant_id: str,
    queue_id: str,
    account_id: str,
    event_type: str,  # "account_created" | "account_mapped"
) -> MaterializationResult:
    """Materialize all signals for a queue entry.

    IMPORTANT: Caller MUST open a transaction before calling this function and
    call session.commit() or session.rollback() after. This function does NOT
    commit. If called outside a transaction, each statement autocommits and the
    atomic guarantee is lost.

    The DBOS workflow step (M3) and the inline ``/map`` route both call this
    function; both wrap it in ``async with session.begin():``. The workflow's
    Step 6 (emit) consumes the returned ``MaterializationResult`` to fan out
    per-interaction EnvelopeV1 events; ``/map`` ignores the return.

    Raises ValueError if no active signals exist for the queue_id — a queue
    entry being materialized with zero signals is architecturally wrong (signals
    are what materialize into contacts) and would produce an empty result. Fail
    loud so the caller logs + the workflow surfaces.
    """
    # event_type is retained for signature parity with the prior worker call
    # site; with the outbox removed, the workflow records lifecycle in
    # dbos.workflow_status instead, and /map's local audit lives in the row's
    # mapped_at + resolved_account_id stamps.
    del event_type

    # ---------------------------------------------------------------------
    # Step 4 (M2): promote pending_interactions for the cold-inbound emails
    # attached to this queue. Runs BEFORE the signal loop so the promoted
    # interactions exist by the time Step 5's email-summary link batch runs.
    # Skipped (and the whole block is cheap) when no cold-inbound emails are
    # attached — the legacy meeting-only path is unaffected.
    # ---------------------------------------------------------------------

    # Step 4-pre: archive any pending rows whose internet_message_id already
    # exists in emails. Prevents the inconsistent state where 4a inserts
    # raw_interactions but 4b's ON CONFLICT skips the emails INSERT.
    await session.execute(ARCHIVE_PENDING_DUPLICATES_SQL, {"queue_id": queue_id})

    # Read the surviving pending rows. Step 4c iterates these for the
    # per-row thread upsert; the final promoted_interaction_ids capture
    # re-reads after Step 4e archives them so the list reflects only
    # rows that successfully traversed all four sub-steps.
    pending_rows = (
        await session.execute(SELECT_PENDING_TO_PROMOTE_SQL, {"queue_id": queue_id})
    ).all()

    promoted_interaction_ids: list[str] = []

    if pending_rows:
        # Step 4a: INSERT raw_interactions (preserved interaction_id).
        await session.execute(
            PROMOTE_INSERT_RAW_INTERACTIONS_SQL,
            {"queue_id": queue_id, "account_id": account_id},
        )

        # Step 4b: INSERT emails (thread_id=NULL, filled in by Step 4c).
        # RETURNING tells us which interaction_ids actually got an emails row
        # — i.e., did NOT hit ON CONFLICT (tenant_id, internet_message_id).
        # Codex M2 round-1 P1: if a concurrent workflow commits a duplicate
        # emails row between 4-pre and 4b, 4a's raw_interactions row becomes
        # an orphan (no matching email). Detect and clean up.
        emails_inserted_result = await session.execute(
            PROMOTE_INSERT_EMAILS_SQL,
            {"queue_id": queue_id, "account_id": account_id},
        )
        inserted_email_interaction_ids = {
            row.interaction_id for row in emails_inserted_result.all()
        }

        # Identify race-losers: pending rows whose 4a raw_interactions was
        # inserted but whose 4b emails INSERT skipped due to conflict.
        all_pending_ids = {str(row.interaction_id) for row in pending_rows}
        race_loser_ids = all_pending_ids - inserted_email_interaction_ids

        if race_loser_ids:
            race_loser_list = list(race_loser_ids)
            # Delete the orphan raw_interactions rows (safe: brand-new,
            # no dependents, IDs known only to this transaction).
            await session.execute(
                DELETE_ORPHAN_RAW_INTERACTIONS_SQL,
                {"ids": race_loser_list},
            )
            # Archive the race-loser pending rows so the surviving 4d/4c
            # WHERE archived_at IS NULL filters exclude them, and Step 4e
            # doesn't re-touch them.
            await session.execute(
                ARCHIVE_RACE_LOSER_PENDING_SQL,
                {"queue_id": queue_id, "ids": race_loser_list},
            )
            # Keep only the actually-promoted rows for the per-row 4c loop.
            pending_rows = [
                row for row in pending_rows
                if str(row.interaction_id) in inserted_email_interaction_ids
            ]

        # If every row was a race-loser, there is nothing left to promote.
        if not pending_rows:
            # Skip 4d/4c/4e for the empty set. promoted_interaction_ids
            # stays []; the workflow's emit step is a no-op.
            pass
        else:
            # Step 4d: INSERT interaction_summaries (summary_type='email').
            # Done BEFORE Step 4c so Step 5's link batch — which JOINs on
            # interaction_summaries — sees a row even if Step 4c's per-row
            # loop is partway through under retry. The WHERE archived_at IS NULL
            # filter excludes race-losers archived above.
            await session.execute(
                PROMOTE_INSERT_INTERACTION_SUMMARIES_SQL,
                {"queue_id": queue_id},
            )

        # Step 4c: upsert email_threads ONCE PER PENDING ROW (so
        # message_count increments correctly when multiple promoted
        # emails share a thread) + UPDATE emails.thread_id.
        for row in pending_rows:
            thread_id = (
                await session.execute(
                    UPSERT_EMAIL_THREAD_SQL,
                    {
                        "tenant_id": tenant_id,
                        "thread_key": row.thread_key,
                        "subject": row.subject,
                        "from_email": row.from_email,
                        "sent_at": row.sent_at,
                        "account_id": account_id,
                    },
                )
            ).scalar_one()
            await session.execute(
                UPDATE_EMAIL_THREAD_ID_SQL,
                {"thread_id": thread_id, "interaction_id": row.interaction_id},
            )
            promoted_interaction_ids.append(str(row.interaction_id))

        # Step 4e: archive the rows we just promoted.
        await session.execute(
            ARCHIVE_PROMOTED_PENDING_SQL,
            {"queue_id": queue_id},
        )

    # ---------------------------------------------------------------------
    # Signal-driven contact materialization + per-signal meeting-summary
    # links (legacy meeting path; M3 behavior preserved).
    # ---------------------------------------------------------------------

    signals = (await session.execute(SELECT_SIGNALS_SQL, {"queue_id": queue_id})).all()

    # Per plan §4.2: the orchestrator's pending path ALWAYS flushes at least
    # the sender's signal alongside the pending_interactions row, in the same
    # transaction. A queue with promoted pending rows but zero active signals
    # is therefore an upstream data-integrity bug — either the orchestrator
    # short-circuited the signal flush or signals were archived out from
    # under us. Either way, materialization without contacts would leave the
    # promoted email with no link to its sender's contact, breaking the
    # downstream extras.contacts contract that action-item-graph and
    # eq-structured-graph-core depend on (Codex M2 round-2 P1). Fail loud
    # so the upstream bug surfaces instead of writing broken envelopes.
    if not signals:
        raise ValueError(
            f"materialize_account_approval called with no active signals "
            f"for queue_id={queue_id!r} (pending_interactions promoted="
            f"{len(promoted_interaction_ids)}). Each cold-inbound email "
            f"should produce at least the sender's signal — investigate "
            f"the orchestrator path."
        )

    contact_ids: list[str] = []
    interaction_ids: list[str] = []
    # Lazy cache: at most one placeholder interaction_summaries row per
    # raw_interaction_id, created on first reference. All contacts for that
    # interaction link to the same summary_id; the link INSERT uses
    # ON CONFLICT DO NOTHING against the (interaction_id, contact_id) unique
    # index, so re-execution under DBOS replay is a no-op.
    summary_id_by_raw_id: dict[str, str] = {}

    for s in signals:
        first, last = _split_name(s.contact_display_name)
        result = await session.execute(
            INSERT_CONTACT_SQL,
            {
                "tenant_id": tenant_id,
                "email": s.contact_email,
                "first_name": first,
                "last_name": last,
                "account_id": account_id,
                "source": s.source_type,
            },
        )
        row = result.one()
        contact_id = row.id
        returned_account_id = row.account_id
        if returned_account_id != account_id:
            raise ValueError(
                f"Contact {s.contact_email!r} already belongs to account "
                f"{returned_account_id!r}; cannot materialize against account "
                f"{account_id!r} for queue_id={queue_id!r}. Cross-account "
                f"contact reassignment is Phase 3 scope; materialization "
                f"fails loud so the operator can investigate."
            )
        contact_ids.append(contact_id)

        if s.interaction_id is not None:
            raw_id = str(s.interaction_id)
            summary_id = summary_id_by_raw_id.get(raw_id)
            if summary_id is None:
                # Codex P1 2026-05-16 (round 5): require Lane 2 to
                # have written the real raw_interactions row before
                # materializing. The previous placeholder pattern
                # (write a synthetic 'meeting' row + track placeholder
                # ids to filter emission) couldn't distinguish "my
                # placeholder" from "previous approval's placeholder
                # for same interaction," so a second approval would
                # emit a corrupted envelope. Failing loud instead is
                # the architecturally correct behavior — DBOS retries
                # the step (up to 3 attempts), /map returns 503, and
                # the operator retries.
                existing_check = (
                    await session.execute(
                        CHECK_RAW_INTERACTION_EXISTS_SQL,
                        {"interaction_id": s.interaction_id},
                    )
                ).first()
                if existing_check is None:
                    raise ValueError(
                        f"raw_interactions row for interaction_id="
                        f"{raw_id!r} does not exist yet. Lane 2 "
                        f"(intelligence_service.process_transcript) "
                        f"may still be writing; the materialization "
                        f"will retry. If this persists across "
                        f"retries, investigate why the queue signal "
                        f"references an interaction_id that Lane 2 "
                        f"never wrote (data-integrity bug upstream)."
                    )
                # UPSERT the placeholder summary atomically. The ON CONFLICT
                # (interaction_id) branch handles the race where the
                # summaries-writer service inserts a row concurrently — we
                # get the existing summary_id back without aborting our txn.
                proposed_summary_id = str(uuid.uuid4())
                result = await session.execute(
                    UPSERT_PLACEHOLDER_SUMMARY_SQL,
                    {
                        "summary_id": proposed_summary_id,
                        "tenant_id": tenant_id,
                        "interaction_id": s.interaction_id,
                        "summary_type": "meeting",
                    },
                )
                summary_id = result.scalar_one()
                summary_id_by_raw_id[raw_id] = summary_id

            await session.execute(
                INSERT_LINK_SQL,
                {"summary_id": summary_id, "contact_id": contact_id},
            )
            interaction_ids.append(raw_id)

    # ---------------------------------------------------------------------
    # Step 5 (M2): batch link insert for email summaries (cross-queue safe).
    # Picks up signals on THIS queue whose interaction was just promoted
    # AND signals on OTHER queues whose interaction was just promoted
    # (the §8.6 cross-queue cold-inbound case). Meeting-summary links are
    # handled by the per-signal loop above. The SQL is a no-op when no
    # email summaries exist for any signal, so it's safe to run
    # unconditionally — but skipping it when no emails were promoted
    # AND no email summaries pre-exist is a measurable hot-path win
    # for the legacy meeting-only case.
    # ---------------------------------------------------------------------

    if promoted_interaction_ids:
        await session.execute(
            BATCH_LINK_EMAIL_SUMMARIES_SQL,
            {"queue_id": queue_id, "tenant_id": tenant_id},
        )

    await session.execute(
        UPDATE_QUEUE_SQL,
        {"queue_id": queue_id, "account_id": account_id},
    )

    # Include the promoted interaction_ids in the main interaction_ids list
    # so Step 6 (existing emit_eventbridge_events) fans out one EnvelopeV1.email
    # per promoted interaction — downstream consumers receive the email exactly
    # as if it had been ingested through the known-account path. The separate
    # promoted_interaction_ids field feeds the new emit_email_promoted_events
    # step at workflow END (plan §5.4) for eq-email-pipeline's local enrichment.
    interaction_ids.extend(promoted_interaction_ids)

    # Dedupe via dict.fromkeys (preserves order; one contact per email,
    # one interaction per raw_id, even if multiple signals share them).
    return MaterializationResult(
        queue_id=queue_id,
        tenant_id=tenant_id,
        account_id=account_id,
        contact_ids=list(dict.fromkeys(contact_ids)),
        interaction_ids=list(dict.fromkeys(interaction_ids)),
        promoted_interaction_ids=list(dict.fromkeys(promoted_interaction_ids)),
    )
