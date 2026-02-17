# User Name Envelope Threading

**Date**: 2026-02-17
**Status**: Implemented
**Related**: eq-frontend JWT threading (Phase 6), action-item-graph owner attribution (Phases 1-5)

## Context

The action-item-graph pipeline extracts action items from meeting transcripts but cannot distinguish items belonging to the recording user from other participants. The `EnvelopeV1.extras` dict is the established extension point for optional metadata flowing to downstream consumers.

The eq-frontend gateway now includes `user_name` in the internal JWT (see `eq-frontend/docs/USER_NAME_JWT_THREADING.md`). This service needs to extract that claim and pass it through to the envelope.

## What Changed

Added `user_name` threading from JWT claims through request context into envelope extras, following the exact same pattern used for `pg_user_id`.

### Files Modified

| File | Change |
|------|--------|
| `middleware/jwt_auth.py` | Added `user_name: str \| None = None` to `JWTClaims` dataclass. Added `payload.get("user_name")` extraction after `pg_user_id`. |
| `models/request_context.py` | Added `user_name: Optional[str] = None` to `RequestContext` dataclass. |
| `utils/context_utils.py` | Added `user_name=claims.user_name` to `RequestContext` construction in `_extract_context_from_jwt()`. |
| `routers/batch.py` | Builds extras dict with optional `user_name` before creating `EnvelopeV1`. |
| `routers/text.py` | Merges `user_name` into request metadata extras before creating `EnvelopeV1`. |

### Data Flow

```
Internal JWT (user_name claim, optional)
  → middleware/jwt_auth.py (extracts as JWTClaims.user_name)
    → utils/context_utils.py (passes to RequestContext.user_name)
      → routers/batch.py | text.py (adds to envelope.extras["user_name"])
        → Kinesis/EventBridge → action-item-graph (reads extras["user_name"])
```

## Approach

- **Optional at every layer**: `str | None = None` — never required, never blocks requests
- **Lenient extraction**: `payload.get("user_name")` — absent JWT claim returns `None` (no error)
- **Conditional inclusion**: `if context.user_name: extras["user_name"] = ...` — absent when `None` (no empty strings in extras)
- **No schema migration**: `EnvelopeV1.extras` is a `dict[str, Any]` — new keys are inherently forwards-compatible
- **Copy-on-write for text router**: `dict(body.metadata)` creates a shallow copy before injecting `user_name` to avoid mutating request body

## Backwards Compatibility

- JWTs without `user_name` claim produce `JWTClaims.user_name = None` → `RequestContext.user_name = None` → no `user_name` key in extras dict
- Existing envelope consumers that don't read `extras["user_name"]` are completely unaffected
- Header-based auth (legacy path) produces `RequestContext.user_name = None` → graceful degradation
- No existing tests are affected (new field is optional with `None` default)

## Verification

1. **JWT with user_name**: `{"tenant_id": "...", "user_id": "...", "user_name": "Peter O'Neil"}` → `JWTClaims.user_name = "Peter O'Neil"` → `extras["user_name"] = "Peter O'Neil"`
2. **JWT without user_name**: `{"tenant_id": "...", "user_id": "..."}` → `JWTClaims.user_name = None` → no `user_name` in extras
3. **Legacy header auth**: No JWT → header fallback → `RequestContext.user_name = None` → no `user_name` in extras
4. **Existing tests**: All pass unchanged (run with `pytest`)

## Upstream Source

The `user_name` claim originates from the Auth0 session (`session.user.name`, standard OIDC claim) and is minted into the internal JWT by `eq-frontend/lib/internal-jwt.ts`. See `eq-frontend/docs/USER_NAME_JWT_THREADING.md` for the upstream implementation.

## Downstream Consumer

The primary consumer is `action-item-graph`, which reads `envelope.extras.get("user_name")` in the extractor and passes it to the LLM extraction prompt. This enables:
1. **User identification**: The LLM knows which speaker is the recording user
2. **is_user_owned tagging**: Action items owned by the user get `is_user_owned: true`
3. **Speaker attribution**: Bare diarization labels (A, B, C) are replaced with names/roles

The pipeline gracefully degrades when `user_name` is absent — the LLM still applies speaker attribution rules, it just can't tag user-owned items.
