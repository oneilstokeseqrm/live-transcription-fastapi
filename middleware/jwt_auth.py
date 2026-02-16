"""
Internal JWT Authentication Module

This module implements verification of internal JWTs minted by the Next.js gateway.
It follows the contract defined in eq-frontend/docs/backend-internal-jwt-contract.md.

JWT Claims Contract:
- tenant_id: UUID v4 (required) - Internal Postgres tenant identifier
- user_id: string (required) - Auth0 subject (sub)
- pg_user_id: string (optional) - Postgres User UUID from identity bridge
- iss: string (required) - Issuer, must match INTERNAL_JWT_ISSUER
- aud: string (required) - Audience, must match INTERNAL_JWT_AUDIENCE
- iat: number (required) - Issued-at timestamp
- exp: number (required) - Expiration timestamp

Security:
- Uses HMAC-SHA256 (HS256) symmetric signing
- Short TTL (~5 minutes) to limit replay window
- Never logs full JWT tokens
"""

import os
import logging
from typing import Optional
from dataclasses import dataclass

import jwt
from jwt.exceptions import (
    InvalidTokenError,
    ExpiredSignatureError,
    InvalidIssuerError,
    InvalidAudienceError,
)

logger = logging.getLogger(__name__)

# Clock skew tolerance in seconds (for exp validation)
CLOCK_SKEW_LEEWAY = 30


@dataclass
class JWTClaims:
    """
    Validated claims extracted from an internal JWT.

    Attributes:
        tenant_id: UUID v4 string identifying the tenant/organization
        user_id: Auth0 subject string (e.g., 'auth0|507f1f77bcf86cd799439011')
        pg_user_id: Optional Postgres User UUID from identity bridge
        issued_at: Unix timestamp when the token was issued
        expires_at: Unix timestamp when the token expires
    """
    tenant_id: str
    user_id: str
    issued_at: int
    expires_at: int
    pg_user_id: str | None = None


class JWTVerificationError(Exception):
    """
    Raised when JWT verification fails.

    Attributes:
        message: Human-readable error description
        code: Machine-readable error code for logging/metrics
    """
    def __init__(self, message: str, code: str = "JWT_INVALID"):
        self.message = message
        self.code = code
        super().__init__(message)


def get_jwt_config() -> tuple[str, str, str]:
    """
    Get JWT configuration from environment variables.

    Returns:
        Tuple of (secret, issuer, audience)

    Raises:
        JWTVerificationError: If required env vars are missing
    """
    secret = os.getenv("INTERNAL_JWT_SECRET")
    issuer = os.getenv("INTERNAL_JWT_ISSUER", "eq-frontend")
    audience = os.getenv("INTERNAL_JWT_AUDIENCE", "eq-backend")

    if not secret:
        logger.error("INTERNAL_JWT_SECRET not configured")
        raise JWTVerificationError(
            "JWT verification not configured",
            code="JWT_NOT_CONFIGURED"
        )

    if len(secret) < 32:
        logger.error("INTERNAL_JWT_SECRET is too short (min 32 chars)")
        raise JWTVerificationError(
            "JWT verification misconfigured",
            code="JWT_MISCONFIGURED"
        )

    return secret, issuer, audience


def verify_internal_jwt(token: str) -> JWTClaims:
    """
    Verify an internal JWT and extract claims.

    This function performs all required validations per the backend contract:
    1. Signature verification using INTERNAL_JWT_SECRET
    2. Issuer validation against INTERNAL_JWT_ISSUER
    3. Audience validation against INTERNAL_JWT_AUDIENCE
    4. Expiration check with clock skew tolerance
    5. Required claims presence (tenant_id, user_id)

    Args:
        token: The JWT string (without 'Bearer ' prefix)

    Returns:
        JWTClaims with validated tenant_id and user_id

    Raises:
        JWTVerificationError: On any validation failure
    """
    secret, issuer, audience = get_jwt_config()

    # Log only that verification is being attempted (never log the token)
    logger.debug(f"Verifying JWT (first 8 chars): {token[:8]}...")

    try:
        # Decode and verify the JWT
        payload = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            issuer=issuer,
            audience=audience,
            leeway=CLOCK_SKEW_LEEWAY,
            options={
                "require": ["exp", "iat", "iss", "aud"],
            }
        )

        # Extract required custom claims
        tenant_id = payload.get("tenant_id")
        user_id = payload.get("user_id")

        if not tenant_id:
            logger.warning("JWT missing tenant_id claim")
            raise JWTVerificationError(
                "Missing required claim: tenant_id",
                code="JWT_MISSING_TENANT"
            )

        if not user_id:
            logger.warning("JWT missing user_id claim")
            raise JWTVerificationError(
                "Missing required claim: user_id",
                code="JWT_MISSING_USER"
            )

        # Validate tenant_id is a valid UUID format
        import uuid
        try:
            uuid.UUID(tenant_id)
        except ValueError:
            logger.warning(f"JWT tenant_id is not a valid UUID")
            raise JWTVerificationError(
                "Invalid tenant_id format: must be UUID",
                code="JWT_INVALID_TENANT"
            )

        # Extract optional identity bridge claim (no error if absent)
        pg_user_id = payload.get("pg_user_id")

        logger.info(f"JWT verified successfully for tenant={tenant_id[:8]}...")

        return JWTClaims(
            tenant_id=tenant_id,
            user_id=user_id,
            pg_user_id=pg_user_id,
            issued_at=payload.get("iat", 0),
            expires_at=payload.get("exp", 0),
        )

    except ExpiredSignatureError:
        logger.warning("JWT has expired")
        raise JWTVerificationError("Token has expired", code="JWT_EXPIRED")

    except InvalidIssuerError:
        logger.warning(f"JWT has invalid issuer (expected: {issuer})")
        raise JWTVerificationError("Invalid token issuer", code="JWT_INVALID_ISSUER")

    except InvalidAudienceError:
        logger.warning(f"JWT has invalid audience (expected: {audience})")
        raise JWTVerificationError("Invalid token audience", code="JWT_INVALID_AUDIENCE")

    except InvalidTokenError as e:
        logger.warning(f"JWT verification failed: {type(e).__name__}")
        raise JWTVerificationError("Invalid token", code="JWT_INVALID")


def extract_bearer_token(authorization_header: Optional[str]) -> Optional[str]:
    """
    Extract the token from an Authorization header.

    Args:
        authorization_header: The full Authorization header value

    Returns:
        The token string, or None if header is missing/malformed
    """
    if not authorization_header:
        return None

    if not authorization_header.startswith("Bearer "):
        return None

    token = authorization_header[7:]  # Remove "Bearer " prefix

    if not token or not token.strip():
        return None

    return token.strip()


def is_jwt_auth_configured() -> bool:
    """
    Check if JWT authentication is properly configured.

    Returns:
        True if INTERNAL_JWT_SECRET is set and valid
    """
    secret = os.getenv("INTERNAL_JWT_SECRET")
    return secret is not None and len(secret) >= 32
