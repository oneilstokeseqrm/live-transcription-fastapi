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
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from services.asyncpg_pool import get_asyncpg_pool
from services.granola_ingestion.adapter import run_one_cycle
from services.granola_ingestion.api_client import GranolaAPIClient
from services.granola_ingestion.errors import GranolaError, GranolaErrorCode
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

    candidate = ctx.pg_user_id or ctx.user_id
    try:
        user_uuid = UUID(candidate)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(
            status_code=400,
            detail="this endpoint requires a UUID-shaped user identifier (pg_user_id)",
        )
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
    if code is VaultErrorCode.VAULT_DB_QUERY_FAILED:
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
    # Auth only — validate doesn't touch the DB, so tenant/user aren't used,
    # but the route is still gated (401 without a valid JWT).
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
        await store_credential(
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
        # Archived row → reconnect: reactivate UPDATEs in place (preserving
        # the credential UUID so EncryptionContext stays consistent).
        try:
            await reactivate_credential(
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
            raise _http_from_vault_error(react_exc)

    # --- Save & test: load (decrypt round-trip) + one synchronous cycle ----
    # The load can raise a structured VaultError (DB / audit / decrypt). The
    # credential is already committed + active at this point, so a load
    # failure is NOT a failed connection — treat it like a failed first poll
    # (the scheduler retries every 5 min) rather than a 500 that would make
    # a retry hit the 409 path (Codex P2).
    try:
        credential = await get_granola_credential_for_user(
            tenant_id=tenant_id,
            user_id=user_id,
            caller_module=_CALLER_MODULE,
            pool=pool,
            trace_id=ctx.trace_id,
        )
    except VaultError:
        logger.exception(
            "granola /connect: credential saved but read-back failed "
            "(credential active; next cron tick retries). tenant=%s user=%s",
            tenant_id, user_id,
        )
        return {
            "ok": False,
            "status": "connected",
            "first_poll": {**_first_poll_zero(), "errors": 1},
            "error_code": "first_poll_failed",
        }
    if credential is None:
        # We just stored an active row; its absence here is a real
        # invariant break (concurrent disconnect, or a store/read DB split).
        logger.error(
            "granola /connect: credential missing immediately after store "
            "(tenant=%s user=%s)",
            tenant_id, user_id,
        )
        raise HTTPException(
            status_code=500,
            detail="Credential was saved but could not be read back; please retry.",
        )

    # NOTE: a brand-new credential has last_polled_at=NULL, so run_one_cycle
    # does a full-folder backfill. For design-partner folders this is fast;
    # a very large folder could approach Railway's ~5-min edge timeout
    # (reference_railway_proxy_timeout). The api_client bounds each request
    # (30s + max_pages); if production data shows slow first polls, Phase 2.1
    # can move the first poll to a dispatched DBOS workflow. LOCKED-31
    # mandates the synchronous one-shot for the ~2s confirmation UX.
    try:
        cycle = await run_one_cycle(credential=credential, pool=pool)
    except Exception as exc:  # noqa: BLE001 — the credential is saved; don't 500 the connect
        logger.exception(
            "granola /connect: first poll raised (credential saved + active; "
            "next cron tick retries). tenant=%s user=%s",
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
    # A credential-level error in the first poll (bad key slipped past
    # validate, deleted folder, sustained outage) flips the credential
    # inside run_one_cycle. Mirror the adapter's lifecycle mapping so the
    # response reports the REAL state (auth failure → 'revoked', everything
    # else → 'error') rather than collapsing all errors to 'error' and
    # driving the wrong reconnect banner (Codex P3).
    if cycle.credential_error_code == GranolaErrorCode.GRANOLA_AUTH_FAILED.value:
        connected_status = "revoked"
    elif cycle.credential_error_code:
        connected_status = "error"
    else:
        connected_status = "connected"
    return {
        "ok": cycle.credential_error_code is None,
        "status": connected_status,
        "first_poll": first_poll,
        "error_code": cycle.credential_error_code,
    }


def _first_poll_zero() -> dict:
    return {
        "notes_processed": 0,
        "ingested": 0,
        "deferred": 0,
        "skipped": 0,
        "errors": 0,
    }


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
