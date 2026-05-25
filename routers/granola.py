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
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from middleware.jwt_auth import extract_bearer_token

from services.asyncpg_pool import get_asyncpg_pool
from services.granola_ingestion.adapter import run_one_cycle
from services.granola_ingestion.api_client import GranolaAPIClient
from services.granola_ingestion.errors import GranolaError, GranolaErrorCode
# Shared per-credential advisory-lock key convention (pure UUID->int64). The
# /connect "save & test" poll holds the SAME lock the scheduler's
# run_cycle_step holds, so a synchronous connect poll and a 5-min scheduler
# cycle can never run the same credential concurrently (which would bypass the
# adapter's per-note idempotency anchor and double-publish). We import only the
# key helper, NOT run_cycle_step — that's a @DBOS.step and calling it outside a
# launched workflow has uncertain production semantics.
from services.granola_ingestion.scheduler import _advisory_lock_key
from services.vault import (
    VaultError,
    VaultErrorCode,
    archive_credential,
    get_credential_status,
    get_granola_credential_for_user,
    reactivate_credential,
    rotate_credential_key,
    store_credential,
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


class ConnectRequest(BaseModel):
    api_key: str = Field(..., min_length=1)
    folder_id: str = Field(..., min_length=1, description="Granola folder id (fol_…) to poll")
    folder_name: str | None = Field(
        default=None,
        description="Display name of the chosen folder (from /validate); stored in config for envelope extras",
    )


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


def _first_poll_zero() -> dict:
    return {
        "notes_processed": 0,
        "ingested": 0,
        "deferred": 0,
        "skipped": 0,
        "errors": 0,
    }


async def _run_save_and_test(
    *, credential_id: UUID, tenant_id: UUID, user_id: UUID, pool, trace_id
) -> dict:
    """LOCKED-31 "save & test" first poll. Thin wrapper that converts a
    transient advisory-lock SETUP failure (``pool.acquire`` /
    ``pg_try_advisory_lock`` raising) into the graceful "connected, first poll
    failed" response: the credential is already committed + active, so a
    lock-setup blip must not 500 a retry into a spurious 409 (Codex R5 P2).
    The locked body is :func:`_save_and_test_locked`.
    """
    try:
        return await _save_and_test_locked(
            credential_id=credential_id,
            tenant_id=tenant_id,
            user_id=user_id,
            pool=pool,
            trace_id=trace_id,
        )
    except (asyncpg.PostgresError, OSError):
        logger.exception(
            "granola /connect: advisory-lock setup failed (credential saved + "
            "active; scheduler retries). credential=%s",
            credential_id,
        )
        return {
            "ok": False,
            "status": "connected",
            "first_poll": {**_first_poll_zero(), "errors": 1},
            "error_code": "first_poll_failed",
        }


async def _save_and_test_locked(
    *, credential_id: UUID, tenant_id: UUID, user_id: UUID, pool, trace_id
) -> dict:
    """LOCKED-31 "save & test" first poll, serialized against the scheduler.

    Holds the SAME per-credential advisory lock the scheduler's
    ``run_cycle_step`` holds, keyed on ``credential_id``. If a 5-min
    scheduler cycle is already polling this credential, ``pg_try_advisory_lock``
    fails and we skip the synchronous poll (the credential is saved + active
    and the scheduler is already on it) — never running two concurrent cycles
    for the same credential, which would bypass the adapter's per-note
    idempotency anchor and double-publish (Codex R4 P2 + the LOCKED-39 race
    the scheduler's lock exists to prevent).

    Returns the /connect response body. The credential is durable + active
    before this runs, so EVERY failure path here reports the connection's
    real state rather than 500ing — a failed first poll just means the
    scheduler picks it up on the next tick.
    """
    lock_key = _advisory_lock_key(credential_id)
    async with pool.acquire() as lock_conn:
        got_lock = await lock_conn.fetchval("SELECT pg_try_advisory_lock($1)", lock_key)
        if not got_lock:
            logger.info(
                "granola /connect: scheduler holds the poll lock for credential "
                "%s; deferring the first poll to the scheduler",
                credential_id,
            )
            return {
                "ok": True,
                "status": "connected",
                "first_poll": _first_poll_zero(),
                "error_code": None,
            }
        try:
            # Load (decrypt round-trip). A structured VaultError here (DB /
            # audit / decrypt) is NOT a failed connection — the credential is
            # committed + active — so report it like a failed first poll
            # rather than a 500 that a retry would turn into a spurious 409.
            try:
                credential = await get_granola_credential_for_user(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    caller_module=_CALLER_MODULE,
                    pool=pool,
                    trace_id=trace_id,
                )
            except VaultError:
                logger.exception(
                    "granola /connect: read-back failed (credential active; "
                    "scheduler retries). tenant=%s user=%s",
                    tenant_id, user_id,
                )
                return {
                    "ok": False,
                    "status": "connected",
                    "first_poll": {**_first_poll_zero(), "errors": 1},
                    "error_code": "first_poll_failed",
                }

            if credential is None:
                # Saved, but get_granola_credential_for_user (active-only)
                # returned nothing: a PRIOR scheduler cycle flipped it
                # (revoked/error) or a concurrent /disconnect archived it.
                # Report the real state, not a 500 (Codex R4 P2).
                post = await _load_status_or_http(
                    tenant_id=tenant_id, user_id=user_id, pool=pool, trace_id=trace_id
                )
                if post is None:
                    raise HTTPException(
                        status_code=503,
                        detail="Credential was saved but could not be read back; please retry.",
                    )
                if post.archived_at is not None:
                    return {
                        "ok": False,
                        "status": "disconnected",
                        "first_poll": _first_poll_zero(),
                        "error_code": (post.last_error or {}).get("error_code"),
                    }
                real_status = "connected" if post.status == "active" else post.status
                return {
                    "ok": post.status == "active",
                    "status": real_status,
                    "first_poll": _first_poll_zero(),
                    "error_code": (post.last_error or {}).get("error_code"),
                }

            # NOTE: a brand-new credential has last_polled_at=NULL, so
            # run_one_cycle does a full-folder backfill. For design-partner
            # folders this is fast; a very large folder could approach
            # Railway's ~5-min edge timeout (reference_railway_proxy_timeout).
            # The api_client bounds each request (30s + max_pages); if
            # production shows slow first polls, Phase 2.1 can move the first
            # poll to a dispatched DBOS workflow. LOCKED-31 mandates the
            # synchronous one-shot for the ~2s confirmation UX.
            try:
                cycle = await run_one_cycle(credential=credential, pool=pool)
            except Exception:  # noqa: BLE001 — credential is saved; never 500 the connect
                logger.exception(
                    "granola /connect: first poll raised (credential saved + "
                    "active; scheduler retries). tenant=%s user=%s",
                    tenant_id, user_id,
                )
                return {
                    "ok": False,
                    "status": "connected",
                    "first_poll": {**_first_poll_zero(), "errors": 1},
                    "error_code": "first_poll_failed",
                }

            outcomes = cycle.outcomes or {}
            first_poll = {
                "notes_processed": cycle.notes_processed,
                "ingested": outcomes.get("success", 0),
                "deferred": outcomes.get("deferred_pending_account", 0),
                "skipped": outcomes.get("skipped_no_business_attendees", 0),
                "errors": (
                    outcomes.get("failed", 0)
                    + outcomes.get("failed_permanent", 0)
                    + (1 if cycle.credential_error_code else 0)
                ),
            }
            # Report the credential's REAL post-poll lifecycle by mirroring the
            # adapter's credential-level branch table (Codex P3 + R3 P2): only
            # auth failure ('revoked') and folder-not-found ('error') are
            # terminal; every other credential error (429/5xx/timeout/parse/
            # http) is TRANSIENT — the adapter bumps consecutive_failures but
            # leaves status='active' until the 3-cycle threshold and the
            # scheduler keeps retrying, so report 'connected'.
            err = cycle.credential_error_code
            if err == GranolaErrorCode.GRANOLA_AUTH_FAILED.value:
                connected_status = "revoked"
            elif err == GranolaErrorCode.GRANOLA_FOLDER_NOT_FOUND.value:
                connected_status = "error"
            else:
                connected_status = "connected"
            return {
                "ok": err is None,
                "status": connected_status,
                "first_poll": first_poll,
                "error_code": err,
            }
        finally:
            # Release before the connection returns to the pool — session
            # advisory locks persist across pool checkin (matches
            # run_cycle_step's finally-unlock).
            try:
                await lock_conn.execute("SELECT pg_advisory_unlock($1)", lock_key)
            except Exception:  # noqa: BLE001 — unlock must not mask the poll result
                logger.exception(
                    "granola /connect: pg_advisory_unlock failed for credential %s",
                    credential_id,
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
    """Store the credential, then run one synchronous test poll (LOCKED-31).

    Storage path: try :func:`store_credential` (INSERT). If a row already
    exists for ``(tenant, user, provider)`` the INSERT fails the UNIQUE
    constraint — which covers archived rows too — so we fall through to
    :func:`reactivate_credential` (the reconnect-after-disconnect UPDATE).
    If reactivate reports the row is already ACTIVE, the user is already
    connected → 409 (use /rotate or /disconnect first).

    "Save & test" (LOCKED-31): after the row is durable, we load it back
    (exercising the full encrypt → store → decrypt round-trip) and run ONE
    ``run_one_cycle`` synchronously so the response carries a real first-poll
    result. The credential is active either way; the scheduler will keep
    polling it every 5 min, so a slow or failed first poll is not fatal —
    we surface it and move on.
    """
    ctx = get_auth_context_polling(request)
    tenant_id, user_id = _resolve_identity(ctx)
    pool = await get_asyncpg_pool()

    config: dict = {"folder_id": body.folder_id}
    if body.folder_name is not None:
        config["folder_name"] = body.folder_name

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
            # An active (or revoked/error) row already exists.
            raise HTTPException(
                status_code=409,
                detail=(
                    "Granola is already connected. Use /rotate to change "
                    "the key, or /disconnect first to reconnect a new one."
                ),
            )
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

    # --- Save & test (LOCKED-31): one synchronous poll, serialized against
    # the scheduler via the shared per-credential advisory lock. Every failure
    # path inside reports the connection's real state rather than 500ing —
    # the credential is durable + active before this runs.
    return await _run_save_and_test(
        credential_id=credential_id,
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
    """Connection health + 7-day activity. Never decrypts the key.

    Returns ``connected=False`` when there's no credential or it's archived
    (the frontend shows the connect wizard); otherwise ``connected=True``
    with the real ``status`` (active / revoked / error) so the UI can render
    the right banner, plus the chosen folder and a 7-day activity rollup.
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
            "folder": None,
            "last_error": None,
        }

    activity = await _activity_counts_7d(pool=pool, tenant_id=tenant_id, user_id=user_id)
    return {
        "connected": True,
        "status": status_row.status,
        "last_polled_at": (
            status_row.last_polled_at.isoformat() if status_row.last_polled_at else None
        ),
        "activity": activity,
        "folder": {
            "id": status_row.config.get("folder_id"),
            "name": status_row.config.get("folder_name"),
        },
        "last_error": status_row.last_error,
    }


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
