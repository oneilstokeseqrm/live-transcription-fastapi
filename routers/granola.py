"""Granola integration admin endpoints (Phase 2f).

The JWT-authed admin surface that lets a user CONNECT a Granola account —
the piece that turns the dormant Phase 2e scheduler into live ingestion.
Per ``tasks/granola-integration-plan.md`` §Phase 2f + LOCKED-30/31/34.

Endpoints (all under prefix ``/integrations/granola``):

* ``POST /validate``  — check a pasted API key + return its folders. Does
  NOT store the key (the two-step wizard validates BEFORE the user picks a
  folder).
* ``POST /connect``   — encrypt + persist the key, then run ONE synchronous
  poll cycle (LOCKED-31 "save & test") so the user gets immediate feedback.
* ``POST /rotate``    — replace the stored key (e.g. after a Granola-side
  key rotation) without losing the credential row's identity.
* ``GET  /status``    — connection health + 7-day activity, WITHOUT
  decrypting the key.
* ``DELETE``          — soft-delete (LOCKED-34): archive the row, preserving
  the audit trail; the scheduler stops dispatching it.

**Auth (LOCKED tenant isolation).** Every endpoint resolves ``tenant_id`` +
``user_id`` from the verified internal JWT via
:func:`utils.context_utils.get_auth_context_polling` (polling-style: no
``X-Account-ID`` anchor — these are per-user integration-management routes,
not account-anchored writes). The vault ``user_credentials.user_id`` column
is a UUID FK to ``users.id``, so we key on the JWT's ``pg_user_id`` (the
Postgres UUID), NOT the Auth0 subject string — the same convention
``routers/queue_actions._effective_user_id`` uses. A JWT without a
UUID-shaped ``pg_user_id`` can't own a credential row, so we reject it with
400 rather than let a non-UUID reach the vault SQL.

**Vault boundary.** All credential storage goes through the audited
:mod:`services.vault` accessors (``routers.granola`` is in the LOCKED-42
ALLOWLIST). The scheduler picks up a freshly-connected credential on its
next 5-min cron tick — no extra wiring here beyond writing the row.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal, Optional
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field, model_validator

from middleware.jwt_auth import extract_bearer_token

from services.asyncpg_pool import get_asyncpg_pool
from services.granola_ingestion.api_client import GranolaAPIClient
from services.granola_ingestion.errors import GranolaError, GranolaErrorCode
from services.granola_ingestion.import_runs import (
    get_or_create_active_import_run,
    latest_import_run,
    read_import_progress,
)
# B3 background-import dispatch + recovery. ``_advisory_lock_key`` is the shared
# per-credential lock convention the reconfigure/reactivate/rotate mutations
# reuse so they never run under an in-flight scheduler cycle.
# ``_IMPORT_RECOVERY_STALE_SECONDS`` mirrors the cron backstop so a
# /status-triggered recovery and a cron-triggered one in the same 5-min window
# dedup to a single re-dispatch. We import only these helpers, NOT the
# @DBOS.step/workflow bodies — calling those outside a launched workflow has
# uncertain production semantics; dispatch goes through ``enqueue_import_workflow``.
from services.granola_ingestion.scheduler import (
    _IMPORT_RECOVERY_STALE_SECONDS,
    _advisory_lock_key,
    enqueue_import_workflow,
    import_recovery_workflow_id,
    import_workflow_id,
)
from services.vault import (
    VaultError,
    VaultErrorCode,
    anchor_credential_watermark,
    archive_credential,
    get_credential_status,
    get_granola_credential_for_user,
    reactivate_credential,
    rotate_credential_key,
    store_credential,
    update_credential_config,
)
from utils.context_utils import get_auth_context_polling

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations/granola", tags=["integrations", "granola"])


# Caller-module identifier passed to the vault ALLOWLIST gate (LOCKED-42).
# MUST match an entry in services.vault.user_credentials.ALLOWLIST exactly —
# the allowlist already contains "routers.granola".
_CALLER_MODULE = "routers.granola"

_PROVIDER = "granola"


# Map Granola client failure codes to the validation ``reason`` the wizard
# surfaces (plan §Phase 2f /validate). Everything not auth/rate-limit
# collapses to "outage" — the actionable message to the user is the same
# ("Granola is unavailable right now, try again"). FOLDER_NOT_FOUND can't
# occur on list_folders; PARSE/HTTP errors are upstream-shape problems the
# user can't fix, so "outage" is the honest user-facing bucket.
_VALIDATE_REASON_BY_CODE = {
    GranolaErrorCode.GRANOLA_AUTH_FAILED: "auth_failed",
    GranolaErrorCode.GRANOLA_RATE_LIMITED: "rate_limited",
    GranolaErrorCode.GRANOLA_5XX: "outage",
    GranolaErrorCode.GRANOLA_TIMEOUT: "outage",
    GranolaErrorCode.GRANOLA_PARSE_ERROR: "outage",
    GranolaErrorCode.GRANOLA_HTTP_ERROR: "outage",
    GranolaErrorCode.GRANOLA_FOLDER_NOT_FOUND: "outage",
}


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class ValidateRequest(BaseModel):
    api_key: str = Field(..., min_length=1, description="Granola personal API key (grn_…)")


class FolderRef(BaseModel):
    """A single watched Granola folder. ``name`` is the display label from
    /validate, stored for the ``granola_folder_name`` envelope extra."""

    id: str = Field(..., min_length=1, description="Granola folder id (fol_…)")
    name: str | None = None


class ConnectRequest(BaseModel):
    """Connect (or reconnect) a Granola account watching a LIST of folders.

    B1 widens the contract from a single folder to an array (decision #5): the
    credential stores ``config.folders = [{id, name}, …]`` and the adapter loops
    over them (the loop itself lands in B2). For one release we ALSO accept the
    legacy singular ``folder_id``/``folder_name`` and mirror ``folders[0]`` back
    into them, so an old client and the not-yet-updated adapter keep working
    through the deploy window (expand-then-contract).

    ``mode`` ("folders" | "all") and ``import_scope`` ("history" | "forward",
    D6) are part of the wire contract from B1, but the behaviour each unlocks
    lands later: ``mode='all'`` (watch everything) is rejected by /connect until
    the B2 loop ships (a synchronous "all" backfill would blow the Railway ~5-min
    request cap); ``import_scope`` is persisted now and acted on in B3.
    """

    api_key: str = Field(..., min_length=1)
    mode: Literal["folders", "all"] = "folders"
    folders: list[FolderRef] = Field(default_factory=list)
    import_scope: Literal["history", "forward"] = "history"  # D6
    # Back-compat (one release): a legacy client may still send the singular
    # folder_id/folder_name; _coalesce_legacy folds them into folders[0].
    folder_id: str | None = None
    folder_name: str | None = None

    @model_validator(mode="after")
    def _coalesce_legacy(self) -> "ConnectRequest":
        if not self.folders and self.folder_id:
            self.folders = [FolderRef(id=self.folder_id, name=self.folder_name)]
        if self.mode == "folders" and not self.folders:
            raise ValueError(
                "folders[] (or a legacy folder_id) is required when mode='folders'"
            )
        return self

    def normalized_folders(self) -> list[dict]:
        """The watched-folder list as plain dicts (``{id[, name]}``)."""
        return [f.model_dump(exclude_none=True) for f in self.folders]

    def config(self) -> dict:
        """The JSONB ``config`` to persist on the credential row.

        Stores the array shape (``mode`` + ``import_scope`` + ``folders``) AND
        mirrors ``folders[0]`` into the legacy singular keys for one release, so
        the adapter's legacy reads + any old client still resolve a folder.
        """
        cfg: dict = {
            "mode": self.mode,
            "import_scope": self.import_scope,
            "folders": self.normalized_folders(),
        }
        if self.folders:  # legacy singular mirror (folders[0]) — one release
            cfg["folder_id"] = self.folders[0].id
            cfg["folder_name"] = self.folders[0].name
        return cfg


class RotateRequest(BaseModel):
    new_api_key: str = Field(..., min_length=1)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _resolve_identity(ctx) -> tuple[UUID, UUID]:
    """Resolve ``(tenant_uuid, user_uuid)`` from the auth context.

    ``tenant_id`` is a validated UUID on the JWT path. ``user_id`` for the
    vault is the Postgres UUID (``pg_user_id``), NOT the Auth0 subject — the
    ``vault.user_credentials.user_id`` column is a UUID FK. A JWT whose
    ``pg_user_id`` is absent or non-UUID could not own a credential row, so
    we reject with 400 rather than feed a non-UUID to the vault SQL (mirrors
    ``routers/queue_actions.ignore_entry``'s guard).
    """
    try:
        tenant_uuid = UUID(ctx.tenant_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=400, detail="tenant_id is not a valid UUID")

    # Bind to the JWT's Postgres UUID (pg_user_id). Do NOT fall back to
    # ctx.user_id (the Auth0 subject): vault.user_credentials.user_id is a FK
    # to users.id, so binding a credential to anything but the real Postgres
    # user id is a tenant-safety hazard (Codex R3 P1). Only the verified-JWT
    # path populates pg_user_id, so REQUIRING it also enforces the Phase 2f
    # "JWT-authed" contract even where ALLOW_LEGACY_HEADER_AUTH is enabled —
    # a legacy-header request carries no pg_user_id and is rejected here with
    # 400 before any credential mutation runs.
    pg_user_id = getattr(ctx, "pg_user_id", None)
    if not pg_user_id:
        raise HTTPException(
            status_code=400,
            detail="this endpoint requires a verified JWT carrying pg_user_id",
        )
    try:
        user_uuid = UUID(pg_user_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=400, detail="pg_user_id is not a valid UUID")
    return tenant_uuid, user_uuid


def _http_from_vault_error(exc: VaultError) -> HTTPException:
    """Map a structured :class:`VaultError` to a clean HTTP response.

    KMS failures → 502 (upstream crypto service); transient DB errors →
    503 (retryable); anything else → 500. We never leak the raw asyncpg /
    boto3 exception text to the client — the structured code is enough for
    the frontend to message + retry.
    """
    code = exc.code
    if code in (
        VaultErrorCode.VAULT_KMS_ENCRYPT_FAILED,
        VaultErrorCode.VAULT_KMS_DECRYPT_FAILED,
        VaultErrorCode.VAULT_KMS_CONTEXT_MISMATCH,
        VaultErrorCode.VAULT_AES_GCM_TAG_MISMATCH,
    ):
        return HTTPException(
            status_code=502, detail="Credential encryption service error; please retry."
        )
    if code in (
        VaultErrorCode.VAULT_DB_QUERY_FAILED,
        # The audit insert / audit-connection acquire failed; the credential
        # write transaction was rolled back, so retrying is the right move —
        # 503, not a permanent-looking 500 (Codex R6 P2).
        VaultErrorCode.VAULT_AUDIT_LOG_WRITE_FAILED,
    ):
        return HTTPException(
            status_code=503, detail="Temporary storage error; please retry."
        )
    return HTTPException(status_code=500, detail=f"Vault error ({code.value}).")


async def _load_status_or_http(*, tenant_id: UUID, user_id: UUID, pool, trace_id):
    """get_credential_status, mapping a structured VaultError to clean HTTP.

    The status accessor can raise on a DB / audit-write failure; without
    this wrapper those would surface as a generic 500 (Codex P2). Mapping
    VAULT_DB_QUERY_FAILED → 503 tells the client the read is retryable.
    """
    try:
        return await get_credential_status(
            tenant_id=tenant_id,
            user_id=user_id,
            caller_module=_CALLER_MODULE,
            pool=pool,
            trace_id=trace_id,
        )
    except VaultError as exc:
        raise _http_from_vault_error(exc)


# Filter on updated_at, NOT created_at (Codex R2 P2): external_integration_runs
# is UPSERTed in place on the composite UNIQUE, so created_at is pinned to
# first-seen while retries + status transitions only bump updated_at. A note
# deferred 10 days ago and ingested yesterday must count as recent ingestion —
# created_at would exclude it from the 7-day window.
_ACTIVITY_COUNTS_7D_SQL = """
SELECT status, COUNT(*)::int AS n
FROM public.external_integration_runs
WHERE tenant_id = $1
  AND user_id = $2
  AND provider = $3
  AND updated_at >= NOW() - INTERVAL '7 days'
GROUP BY status
"""


async def _activity_counts_7d(*, pool, tenant_id: UUID, user_id: UUID) -> dict:
    """Tenant+user-scoped 7-day rollup of external_integration_runs outcomes.

    Reads the public dedup-ledger directly via the asyncpg pool (same
    pattern the adapter uses for these non-encrypted rows). The status
    values are the IngestionOutcome enum strings written by the adapter.

    A transient DB failure here surfaces as a retryable 503 rather than a
    generic 500 (Codex R2 P2) — consistent with the rest of the router's
    storage-error posture.
    """
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(_ACTIVITY_COUNTS_7D_SQL, tenant_id, user_id, _PROVIDER)
    except (asyncpg.PostgresError, OSError) as exc:
        logger.warning("granola /status: 7-day activity rollup failed: %r", exc)
        raise HTTPException(
            status_code=503, detail="Temporary storage error; please retry."
        )
    by_status = {r["status"]: r["n"] for r in rows}
    return {
        "ingested_7d": by_status.get("success", 0),
        "deferred_7d": by_status.get("deferred_pending_account", 0),
        "errors_7d": by_status.get("failed", 0) + by_status.get("failed_permanent", 0),
    }


def _empty_activity() -> dict:
    return {"ingested_7d": 0, "deferred_7d": 0, "errors_7d": 0}


@asynccontextmanager
async def _credential_poll_lock(pool, credential_id: UUID):
    """Acquire the per-credential scheduler advisory lock (non-blocking).

    Yields ``True`` if the lock was acquired (no in-flight scheduler cycle for
    this credential — the caller may proceed) or ``False`` if a cycle holds it
    (the caller should 409). Releases on exit.

    This is the shared serialization primitive for credential MUTATIONS that
    reuse a credential_id (reconnect/reactivate, rotate). A stale scheduler
    cycle still polling the credential writes its terminal state back through
    ``run_one_cycle`` (last_polled_at / status / last_error), which would
    clobber a reactivation reset or a rotation's ``status='active'`` /
    ``last_error=NULL`` (Codex R7/R8 P1). Holding this lock around the mutation
    guarantees no cycle is mid-run; ``pg_try_advisory_lock`` is non-blocking so
    a request never waits a cycle's whole duration — it 409s and the user
    retries once the cycle finishes (≤ one 5-min tick).
    """
    lock_key = _advisory_lock_key(credential_id)
    async with pool.acquire() as conn:
        got = await conn.fetchval("SELECT pg_try_advisory_lock($1)", lock_key)
        try:
            yield got
        finally:
            if got:
                try:
                    await conn.execute("SELECT pg_advisory_unlock($1)", lock_key)
                except Exception:  # noqa: BLE001 — unlock must not mask the result
                    logger.exception(
                        "granola: advisory unlock failed for credential %s",
                        credential_id,
                    )


def _current_cycle_window() -> int:
    """5-min window index (``unix_minute // 5``).

    Mirrors :func:`routers.granola_cron._current_cycle_window` so a
    /status-triggered import recovery and a cron-triggered one in the SAME
    window produce the same recovery workflow id → DBOS dedups to a single
    re-dispatch.
    """
    return int(datetime.now(timezone.utc).timestamp() // 60) // 5


def _import_run_is_stale(created_at: Optional[datetime]) -> bool:
    """True if a ``queued`` import run is old enough to assume its workflow
    never ran / returned lock_busy (A2 strand). A fresh dispatch marks_running
    within ~1-2s, so anything past the staleness window is recoverable."""
    if created_at is None:
        return True
    return (
        datetime.now(timezone.utc) - created_at
    ).total_seconds() >= _IMPORT_RECOVERY_STALE_SECONDS


async def _build_import_block(
    *, credential_id: UUID, tenant_id: UUID, user_id: UUID
) -> Optional[dict]:
    """C18 ``/status`` import block: the latest run + DERIVED progress, or
    ``None`` when the credential never had an import (forward connections +
    pre-B3 legacy rows → the block is OMITTED). Counts come from
    ``external_integration_runs`` (idempotent), never a stored tally.
    """
    latest = await latest_import_run(
        credential_id=credential_id, tenant_id=tenant_id, user_id=user_id
    )
    if latest is None:
        return None
    prog = await read_import_progress(
        import_run_id=latest["id"], tenant_id=tenant_id, user_id=user_id
    )
    if prog is None:
        return None
    started = prog["started_at"]
    finished = prog["finished_at"]
    return {
        "import_run_id": str(latest["id"]),
        "state": prog["state"],  # queued | running | complete | failed | cancelled
        "total": prog["total"],  # null until the first listing → FE indeterminate (C14)
        "done": prog["done"],
        "deferred": prog["deferred"],
        "skipped": prog["skipped"],
        "errors": prog["errors"],
        "started_at": started.isoformat() if started else None,
        "finished_at": finished.isoformat() if finished else None,
    }


async def _finalize_connect_with_scope(
    *,
    credential_id: UUID,
    import_scope: str,
    forward_anchor_at: datetime,
    tenant_id: UUID,
    user_id: UUID,
    pool,
    trace_id,
) -> dict:
    """B3 post-store branch on ``import_scope`` (D6), replacing the retired
    synchronous "save & test" first poll (LOCKED-31).

    * ``forward``: anchor ``last_polled_at`` to the ROUTE-ENTRY timestamp (C4 —
      captured before any awaits) so the first 5-min poll's ``created_after``
      excludes pre-connect history. No backfill, no import run → ``import: null``.
    * ``history`` (default): create the import run (idempotent via the
      partial-unique) + dispatch the background import on the dedicated
      ``GRANOLA_IMPORT_QUEUE`` under the DETERMINISTIC workflow id (C8 — a retry
      DBOS-dedups). Return the ``import`` ACK; the workflow flips it to running.
    """
    if import_scope == "forward":
        try:
            await anchor_credential_watermark(
                pool=pool,
                credential_id=credential_id,
                tenant_id=tenant_id,
                user_id=user_id,
                ts=forward_anchor_at,
                caller_module=_CALLER_MODULE,
                trace_id=trace_id,
            )
        except VaultError as exc:
            raise _http_from_vault_error(exc)
        return {"ok": True, "status": "connected", "import": None}

    run_id, _created = await get_or_create_active_import_run(
        credential_id=credential_id, tenant_id=tenant_id, user_id=user_id
    )
    await enqueue_import_workflow(
        credential_id=credential_id,
        tenant_id=tenant_id,
        user_id=user_id,
        import_run_id=run_id,
        workflow_id=import_workflow_id(credential_id, run_id),
    )
    return {
        "ok": True,
        "status": "connected",
        "import": {
            "import_run_id": str(run_id),
            "state": "queued",
            "total": None,
            "done": 0,
        },
    }


async def _recover_history_import(
    *, status_row, tenant_id: UUID, user_id: UUID
) -> None:
    """Best-effort import recovery on the /status + /connect-retry surfaces.

    Closes the enqueue-atomicity gap (C8) and re-kicks a lock-busy strand (A2)
    for an ACTIVE history credential whose watermark is still NULL (the import
    never set it):

    * no import run (crash before create/dispatch) → create + dispatch with the
      DETERMINISTIC id (idempotent: a live dispatch DBOS-dedups);
    * a STALE ``queued`` run (lock-busy strand / crash before dispatch) →
      re-dispatch with a window-stamped recovery id;
    * running / complete / failed / cancelled → no action (don't auto-retry a
      genuinely failed import — a failed import flips the credential non-active,
      so this guard won't fire for it).

    NEVER raises — a recovery blip must not break the read / connect response.
    """
    cfg = status_row.config or {}
    if (
        status_row.status != "active"
        or cfg.get("import_scope") != "history"
        or status_row.last_polled_at is not None
    ):
        return
    try:
        latest = await latest_import_run(
            credential_id=status_row.id, tenant_id=tenant_id, user_id=user_id
        )
        if latest is None:
            run_id, _ = await get_or_create_active_import_run(
                credential_id=status_row.id, tenant_id=tenant_id, user_id=user_id
            )
            await enqueue_import_workflow(
                credential_id=status_row.id,
                tenant_id=tenant_id,
                user_id=user_id,
                import_run_id=run_id,
                workflow_id=import_workflow_id(status_row.id, run_id),
            )
        elif latest["state"] == "queued" and _import_run_is_stale(latest.get("created_at")):
            await enqueue_import_workflow(
                credential_id=status_row.id,
                tenant_id=tenant_id,
                user_id=user_id,
                import_run_id=latest["id"],
                workflow_id=import_recovery_workflow_id(
                    status_row.id, latest["id"], _current_cycle_window()
                ),
            )
    except Exception:  # noqa: BLE001 — recovery must not break the surface
        logger.exception(
            "granola: history-import recovery (non-fatal) failed for credential %s",
            status_row.id,
        )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/validate")
async def validate_granola_key(body: ValidateRequest, request: Request) -> dict:
    """Validate a pasted API key + return its folders. Does NOT store anything.

    Calls ``GranolaAPIClient.list_folders()`` — a 401 surfaces as
    ``GRANOLA_AUTH_FAILED`` and the folder list is exactly what the wizard's
    step 2 (pick a folder) needs. Returns HTTP 200 with an ``ok`` flag so
    the wizard can render inline messaging without treating a bad key as a
    transport error.
    """
    # Auth: /validate must be reached only with a VERIFIED JWT — it proxies a
    # call to Granola with a caller-supplied key, so it can't be an
    # unauthenticated probe surface. But it is STATELESS (never touches
    # vault.user_credentials), so it must NOT require the optional pg_user_id
    # claim the mutation routes need. We resolve that by requiring a bearer
    # token explicitly: with a token present, get_auth_context_polling
    # verifies the JWT; with none, we 401 here BEFORE the legacy-header
    # fallback can run — so /validate is JWT-only even when
    # ALLOW_LEGACY_HEADER_AUTH=true, without depending on pg_user_id.
    #
    # (Codex trajectory: R4 wanted legacy blocked → added _resolve_identity;
    # R5 flagged that rejects valid pg_user_id-less JWTs; R6 re-flagged the
    # legacy surface. This bearer-token gate resolves all three at once —
    # JWT required, pg_user_id not — rather than freezing on one side.)
    if not extract_bearer_token(request.headers.get("Authorization")):
        raise HTTPException(
            status_code=401, detail="Authorization required: Bearer token expected"
        )
    get_auth_context_polling(request)

    client = GranolaAPIClient(api_key=body.api_key)
    try:
        folders = await client.list_folders()
    except GranolaError as exc:
        reason = _VALIDATE_REASON_BY_CODE.get(exc.code, "outage")
        logger.info("granola /validate failed: code=%s reason=%s", exc.code.value, reason)
        return {"ok": False, "reason": reason}
    finally:
        await client.aclose()

    return {
        "ok": True,
        "folders": [{"id": f.id, "name": f.name} for f in folders],
    }


@router.post("/connect")
async def connect_granola(body: ConnectRequest, request: Request) -> dict:
    """Store the credential, then ACK + dispatch a background import (B3).

    Storage path: try :func:`store_credential` (INSERT). If a row already
    exists for ``(tenant, user, provider)`` the INSERT fails the UNIQUE
    constraint — which covers archived rows too — so we either RECONFIGURE an
    ACTIVE row's folders in place (C5) or :func:`reactivate_credential` an
    ARCHIVED row.

    B3 retires the synchronous "save & test" first poll (LOCKED-31, amended by
    decision #6). After the row is durable we branch on ``import_scope`` (D6):

    * ``history`` (default): create a ``granola_import_runs`` row + dispatch the
      background import on the dedicated ``GRANOLA_IMPORT_QUEUE`` and return an
      ``import`` ACK (``{import_run_id, state:'queued', total:null, done:0}``).
      The 33-83 min backfill never touches this request thread (Railway ~5-min
      cap). The mode="all" guard is LIFTED now that there's no synchronous
      backfill.
    * ``forward``: anchor ``last_polled_at`` to the route-ENTRY timestamp (C4),
      run no backfill, return ``import: null``; the 5-min poll picks up new
      meetings.

    Active-row reconfigure keeps B2 behavior (update ``config.folders`` in
    place; no new import / re-anchor — newly-added-folder backfill is the #21a
    fast-follow). The reconfigure path doubles as a /connect-retry recovery
    surface (C8) via :func:`_recover_history_import`.
    """
    # C4: capture the forward-anchor timestamp at ROUTE ENTRY, BEFORE any awaits,
    # so a meeting created during the connect round-trip isn't skipped by the
    # forward watermark (the store/encrypt round-trip can take a beat).
    forward_anchor_at = datetime.now(timezone.utc)

    ctx = get_auth_context_polling(request)
    tenant_id, user_id = _resolve_identity(ctx)
    pool = await get_asyncpg_pool()

    # Folder-LIST config (decision #5): stores mode + import_scope + folders[]
    # AND mirrors folders[0] into the legacy singular folder_id/folder_name for
    # one release (so the not-yet-updated adapter's legacy reads still resolve).
    config: dict = body.config()

    # --- Store (new) or reactivate (previously archived) -------------------
    # store_credential INSERTs a new row. VAULT_DB_INSERT_FAILED is generic
    # (ANY rejected INSERT), so on that code we must distinguish a real
    # uniqueness collision (a row already exists for this tenant/user/provider)
    # from a different insert rejection — e.g. a stale pg_user_id that no
    # longer satisfies the user_credentials.user_id -> users.id FK (Codex P2).
    # We read the row back: present + archived → reconnect (reactivate);
    # present + active → already connected (409); absent → the INSERT failed
    # for a non-uniqueness reason → surface 502 rather than masquerade as a
    # reconnect and confusingly 500 with vault_db_not_found.
    try:
        credential_id = await store_credential(
            tenant_id=tenant_id,
            user_id=user_id,
            provider=_PROVIDER,
            api_key=body.api_key,
            config=config,
            caller_module=_CALLER_MODULE,
            pool=pool,
            trace_id=ctx.trace_id,
        )
    except VaultError as exc:
        if exc.code is not VaultErrorCode.VAULT_DB_INSERT_FAILED:
            # KMS / generic DB failure — clean HTTP, never a raw 500.
            raise _http_from_vault_error(exc)
        existing = await _load_status_or_http(
            tenant_id=tenant_id, user_id=user_id, pool=pool, trace_id=ctx.trace_id
        )
        if existing is None:
            # INSERT failed but no row exists → not a uniqueness collision
            # (FK violation, constraint, etc). Don't pretend it's a reconnect.
            logger.warning(
                "granola /connect: store INSERT failed with no existing row "
                "(non-uniqueness rejection) for tenant=%s user=%s",
                tenant_id, user_id,
            )
            raise HTTPException(
                status_code=502,
                detail="Could not store the Granola credential; please retry.",
            )
        if existing.archived_at is None:
            if existing.status != "active":
                # A revoked/error row exists (key bad, or credential broken) →
                # 409; the user fixes it via /rotate (new key) or /disconnect.
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Granola is already connected. Use /rotate to change "
                        "the key, or /disconnect first to reconnect a new one."
                    ),
                )
            # C5: an ACTIVE row → folder RECONFIGURE (NOT 409, and NOT
            # reactivate_credential — that only handles ARCHIVED rows). Under the
            # per-credential advisory lock so a concurrent poll cycle can't
            # clobber the config write (and vice versa): load the decrypted key;
            # if the submitted key DIFFERS it's a key change → /rotate's job
            # (reject — don't rotate keys silently through /connect); if it
            # MATCHES, update the watched folders + import_scope in place, then
            # save-and-test over the new folder set. (Backfill of newly-added
            # folders is B3 — this does not move the global watermark.)
            async with _credential_poll_lock(pool, existing.id) as got_lock:
                if not got_lock:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "A Granola sync is currently running for this "
                            "connection; please retry in a moment."
                        ),
                    )
                try:
                    current = await get_granola_credential_for_user(
                        tenant_id=tenant_id,
                        user_id=user_id,
                        caller_module=_CALLER_MODULE,
                        pool=pool,
                        trace_id=ctx.trace_id,
                    )
                except VaultError as exc:
                    raise _http_from_vault_error(exc)
                if current is None:
                    # Flipped (revoked/error) or archived between the status read
                    # and the lock → no active row to reconfigure.
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "Granola is already connected. Use /rotate to change "
                            "the key, or /disconnect first to reconnect a new one."
                        ),
                    )
                if body.api_key != current.api_key:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "That Granola key is different from the connected "
                            "one. Use /rotate to change the key, or /disconnect "
                            "first to reconnect with a new key."
                        ),
                    )
                try:
                    await update_credential_config(
                        tenant_id=tenant_id,
                        user_id=user_id,
                        provider=_PROVIDER,
                        credential_id=existing.id,
                        new_config=config,
                        caller_module=_CALLER_MODULE,
                        pool=pool,
                        trace_id=ctx.trace_id,
                    )
                except VaultError as exc:
                    raise _http_from_vault_error(exc)
            # A4: reconfigure keeps B2 behavior — update config.folders in place,
            # NO new import + NO re-anchor (newly-added-folder backfill is the
            # #21a fast-follow). This path also doubles as the /connect-retry
            # recovery surface (C8): if a prior /connect crashed after creating
            # the credential but before creating/dispatching its import, recover
            # it here. Then report the connection + its current import block.
            await _recover_history_import(
                status_row=existing, tenant_id=tenant_id, user_id=user_id
            )
            import_block = await _build_import_block(
                credential_id=existing.id, tenant_id=tenant_id, user_id=user_id
            )
            resp: dict = {"ok": True, "status": "connected"}
            if import_block is not None:
                resp["import"] = import_block
            return resp
        # Archived row → reconnect: reactivate UPDATEs in place (preserving the
        # credential UUID so EncryptionContext stays consistent), resetting
        # last_polled_at=NULL so the new folder is fully re-scanned. Serialize
        # against any in-flight scheduler cycle (Codex R7 P1): reactivate
        # reuses credential_id, so a stale cycle's terminal write-back would
        # clobber the reset and skip notes in the new folder.
        async with _credential_poll_lock(pool, existing.id) as got_lock:
            if not got_lock:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "A Granola sync is currently running for this "
                        "connection; please retry in a moment."
                    ),
                )
            try:
                credential_id = await reactivate_credential(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    provider=_PROVIDER,
                    new_api_key=body.api_key,
                    new_config=config,
                    caller_module=_CALLER_MODULE,
                    pool=pool,
                    trace_id=ctx.trace_id,
                )
            except VaultError as react_exc:
                if react_exc.code in (
                    VaultErrorCode.VAULT_DB_INSERT_FAILED,
                    VaultErrorCode.VAULT_DB_NOT_FOUND,
                ):
                    # Concurrent reconnect race (double-submit / retry): another
                    # request reactivated the row first → it's active
                    # (INSERT_FAILED='is active') or already-cleared (NOT_FOUND).
                    # Either way the credential is connected now — 409, not 500
                    # (Codex R3 P2).
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "Granola is already connected. Use /rotate to change "
                            "the key, or /disconnect first to reconnect a new one."
                        ),
                    )
                raise _http_from_vault_error(react_exc)

    # B3: the credential is durable + active (new INSERT or reactivated archived
    # row). Branch on import_scope (D6): history → create + dispatch the
    # background import and return the ACK; forward → anchor the route-entry
    # watermark (no backfill) and return import:null.
    return await _finalize_connect_with_scope(
        credential_id=credential_id,
        import_scope=body.import_scope,
        forward_anchor_at=forward_anchor_at,
        tenant_id=tenant_id,
        user_id=user_id,
        pool=pool,
        trace_id=ctx.trace_id,
    )


@router.post("/rotate")
async def rotate_granola_key(body: RotateRequest, request: Request) -> dict:
    """Replace the stored API key on the existing credential row.

    Looks the credential up via :func:`get_credential_status` (no decrypt)
    to find its id, then :func:`rotate_credential_key` mints a fresh DEK +
    nonce (LOCKED-43) and resets status→active. An archived (disconnected)
    credential can't be rotated — that's a reconnect (/connect) — so we 404
    on a missing or archived row.
    """
    ctx = get_auth_context_polling(request)
    tenant_id, user_id = _resolve_identity(ctx)
    pool = await get_asyncpg_pool()

    status_row = await _load_status_or_http(
        tenant_id=tenant_id, user_id=user_id, pool=pool, trace_id=ctx.trace_id
    )
    if status_row is None or status_row.archived_at is not None:
        raise HTTPException(
            status_code=404,
            detail="No connected Granola credential to rotate. Connect first.",
        )

    # Serialize against any in-flight scheduler cycle (Codex R8 P1): rotate
    # resets status='active' + last_error=NULL, but a stale cycle polling with
    # the OLD key would write its terminal status (revoked/error) back
    # afterward — bouncing a just-rotated credential straight back to revoked.
    # Gate on the same advisory lock as reconnect.
    async with _credential_poll_lock(pool, status_row.id) as got_lock:
        if not got_lock:
            raise HTTPException(
                status_code=409,
                detail=(
                    "A Granola sync is currently running for this connection; "
                    "please retry the rotation in a moment."
                ),
            )
        try:
            await rotate_credential_key(
                tenant_id=tenant_id,
                user_id=user_id,
                credential_id=status_row.id,
                new_api_key=body.new_api_key,
                caller_module=_CALLER_MODULE,
                pool=pool,
                trace_id=ctx.trace_id,
            )
        except VaultError as exc:
            if exc.code is VaultErrorCode.VAULT_DB_NOT_FOUND:
                # Raced with a concurrent disconnect between status read + rotate.
                raise HTTPException(
                    status_code=404,
                    detail="Credential was disconnected; reconnect to add a new key.",
                )
            raise _http_from_vault_error(exc)

    return {"ok": True}


@router.get("/status")
async def granola_status(request: Request) -> dict:
    """Connection health + 7-day activity + import progress. Never decrypts the key.

    Returns ``connected=False`` when there's no credential or it's archived
    (the frontend shows the connect wizard); otherwise ``connected=True``
    with the real ``status`` (active / revoked / error) so the UI can render
    the right banner, the watched folders, a 7-day activity rollup, and — for a
    history connection with a background import — an ``import`` block (C18) the
    FE polls (indeterminate until ``total`` is known, then "N of M"; stops on a
    terminal state). The block is OMITTED for forward connections + pre-B3
    legacy credentials (no import run). /status also best-effort recovers a
    stuck import (C8 + A2) while the connect screen is open.
    """
    ctx = get_auth_context_polling(request)
    tenant_id, user_id = _resolve_identity(ctx)
    pool = await get_asyncpg_pool()

    status_row = await _load_status_or_http(
        tenant_id=tenant_id, user_id=user_id, pool=pool, trace_id=ctx.trace_id
    )

    if status_row is None or status_row.archived_at is not None:
        return {
            "connected": False,
            "status": "archived" if status_row is not None else "none",
            "last_polled_at": None,
            "activity": _empty_activity(),
            "mode": None,
            "folders": [],
            "folder": None,  # legacy singular mirror (one release) — null when disconnected
            "last_error": None,
        }

    # C8 + A2: recover a stuck history import (no run after a crash, or a
    # lock-busy strand) while the connect screen is open — the instant half of
    # the founder-approved "both surfaces" recovery (the 5-min cron is the
    # headless backstop). Self-gates + best-effort (never raises).
    await _recover_history_import(
        status_row=status_row, tenant_id=tenant_id, user_id=user_id
    )

    activity = await _activity_counts_7d(pool=pool, tenant_id=tenant_id, user_id=user_id)
    # Array-shaped folders (B1, decision #5). Read the canonical config.folders;
    # fall back to the legacy singular folder_id/folder_name (one release) so a
    # pre-B1 credential still reports a one-element list. Each folder's status
    # defaults to "ok"; per-folder error state (config.folders[].status) is
    # populated in B2 (C6) and surfaces here unchanged.
    cfg = status_row.config or {}
    folders_cfg = cfg.get("folders") or (
        [{"id": cfg["folder_id"], "name": cfg.get("folder_name")}]
        if cfg.get("folder_id")
        else []
    )
    # Legacy singular `folder` (= folders[0]) is mirrored alongside `folders[]`
    # for one release so a pre-B1 /status reader keeps working through the expand
    # window (symmetry with /connect's legacy folder_id/folder_name acceptance).
    # The later "contract" release drops both the request- and response-side
    # legacy keys together.
    legacy_folder = (
        {"id": folders_cfg[0]["id"], "name": folders_cfg[0].get("name")}
        if folders_cfg
        else None
    )
    # C18: the import block (latest run + DERIVED progress). Omitted when the
    # credential never had an import (forward + pre-B3 legacy rows).
    import_block = await _build_import_block(
        credential_id=status_row.id, tenant_id=tenant_id, user_id=user_id
    )
    resp = {
        "connected": True,
        "status": status_row.status,
        "last_polled_at": (
            status_row.last_polled_at.isoformat() if status_row.last_polled_at else None
        ),
        "activity": activity,
        "mode": cfg.get("mode", "folders"),
        "import_scope": cfg.get("import_scope"),  # "history" | "forward" | None (legacy)
        "folders": [
            {"id": f["id"], "name": f.get("name"), "status": f.get("status", "ok")}
            for f in folders_cfg
        ],
        "folder": legacy_folder,
        "last_error": status_row.last_error,
    }
    if import_block is not None:
        resp["import"] = import_block
    return resp


@router.delete("", status_code=status.HTTP_200_OK)
async def disconnect_granola(request: Request) -> dict:
    """Soft-delete the credential (LOCKED-34): archive + preserve audit trail.

    Idempotent: a missing or already-archived credential returns 200
    ``{"status": "disconnected"}`` without mutating. The scheduler's
    ``list_active_credentials`` filters ``archived_at IS NULL``, so the next
    cron tick stops dispatching this credential.
    """
    ctx = get_auth_context_polling(request)
    tenant_id, user_id = _resolve_identity(ctx)
    pool = await get_asyncpg_pool()

    status_row = await _load_status_or_http(
        tenant_id=tenant_id, user_id=user_id, pool=pool, trace_id=ctx.trace_id
    )
    if status_row is None or status_row.archived_at is not None:
        # Nothing connected, or already disconnected — idempotent success.
        return {"ok": True, "status": "disconnected"}

    try:
        await archive_credential(
            tenant_id=tenant_id,
            user_id=user_id,
            credential_id=status_row.id,
            caller_module=_CALLER_MODULE,
            pool=pool,
            trace_id=ctx.trace_id,
        )
    except VaultError as exc:
        # A transient vault/DB failure on the soft-delete → clean retryable
        # HTTP, not a generic 500 (Codex R2 P2).
        raise _http_from_vault_error(exc)
    return {"ok": True, "status": "disconnected"}
