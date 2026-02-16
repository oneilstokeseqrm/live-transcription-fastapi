"""Tests for JWT authentication middleware.

Tests the JWT verification module and unified auth context function.
"""
import os
import time
import uuid
import pytest
import jwt as pyjwt
from fastapi.testclient import TestClient
from unittest.mock import patch

# Set test environment before importing app
os.environ["INTERNAL_JWT_SECRET"] = "test-secret-that-is-at-least-32-characters-long"
os.environ["INTERNAL_JWT_ISSUER"] = "eq-frontend"
os.environ["INTERNAL_JWT_AUDIENCE"] = "eq-backend"


def generate_test_jwt(
    tenant_id: str = None,
    user_id: str = None,
    pg_user_id: str = None,
    issuer: str = None,
    audience: str = None,
    exp_offset: int = 300,
    secret: str = None,
) -> str:
    """Generate a test JWT with configurable claims."""
    now = int(time.time())
    payload = {
        "tenant_id": tenant_id or str(uuid.uuid4()),
        "user_id": user_id or "auth0|test-user-123",
        "iss": issuer or "eq-frontend",
        "aud": audience or "eq-backend",
        "iat": now,
        "exp": now + exp_offset,
    }
    if pg_user_id is not None:
        payload["pg_user_id"] = pg_user_id
    secret = secret or os.environ["INTERNAL_JWT_SECRET"]
    return pyjwt.encode(payload, secret, algorithm="HS256")


class TestJWTVerification:
    """Tests for the JWT verification module."""

    def test_valid_jwt_extracts_claims(self):
        """Valid JWT should extract tenant_id and user_id."""
        from middleware.jwt_auth import verify_internal_jwt

        tenant_id = str(uuid.uuid4())
        user_id = "auth0|user-xyz"
        token = generate_test_jwt(tenant_id=tenant_id, user_id=user_id)

        claims = verify_internal_jwt(token)

        assert claims.tenant_id == tenant_id
        assert claims.user_id == user_id

    def test_expired_jwt_raises_error(self):
        """Expired JWT should raise JWTVerificationError."""
        from middleware.jwt_auth import verify_internal_jwt, JWTVerificationError

        token = generate_test_jwt(exp_offset=-60)  # Expired 60 seconds ago

        with pytest.raises(JWTVerificationError) as exc_info:
            verify_internal_jwt(token)

        assert exc_info.value.code == "JWT_EXPIRED"

    def test_wrong_issuer_raises_error(self):
        """JWT with wrong issuer should raise JWTVerificationError."""
        from middleware.jwt_auth import verify_internal_jwt, JWTVerificationError

        token = generate_test_jwt(issuer="wrong-issuer")

        with pytest.raises(JWTVerificationError) as exc_info:
            verify_internal_jwt(token)

        assert exc_info.value.code == "JWT_INVALID_ISSUER"

    def test_wrong_audience_raises_error(self):
        """JWT with wrong audience should raise JWTVerificationError."""
        from middleware.jwt_auth import verify_internal_jwt, JWTVerificationError

        token = generate_test_jwt(audience="wrong-audience")

        with pytest.raises(JWTVerificationError) as exc_info:
            verify_internal_jwt(token)

        assert exc_info.value.code == "JWT_INVALID_AUDIENCE"

    def test_wrong_secret_raises_error(self):
        """JWT signed with wrong secret should raise JWTVerificationError."""
        from middleware.jwt_auth import verify_internal_jwt, JWTVerificationError

        token = generate_test_jwt(secret="completely-different-secret-that-is-long-enough")

        with pytest.raises(JWTVerificationError) as exc_info:
            verify_internal_jwt(token)

        assert exc_info.value.code == "JWT_INVALID"

    def test_missing_tenant_id_raises_error(self):
        """JWT without tenant_id should raise JWTVerificationError."""
        from middleware.jwt_auth import verify_internal_jwt, JWTVerificationError

        # Generate JWT without tenant_id
        now = int(time.time())
        payload = {
            "user_id": "auth0|user",
            "iss": "eq-frontend",
            "aud": "eq-backend",
            "iat": now,
            "exp": now + 300,
        }
        token = pyjwt.encode(payload, os.environ["INTERNAL_JWT_SECRET"], algorithm="HS256")

        with pytest.raises(JWTVerificationError) as exc_info:
            verify_internal_jwt(token)

        assert exc_info.value.code == "JWT_MISSING_TENANT"

    def test_missing_user_id_raises_error(self):
        """JWT without user_id should raise JWTVerificationError."""
        from middleware.jwt_auth import verify_internal_jwt, JWTVerificationError

        now = int(time.time())
        payload = {
            "tenant_id": str(uuid.uuid4()),
            "iss": "eq-frontend",
            "aud": "eq-backend",
            "iat": now,
            "exp": now + 300,
        }
        token = pyjwt.encode(payload, os.environ["INTERNAL_JWT_SECRET"], algorithm="HS256")

        with pytest.raises(JWTVerificationError) as exc_info:
            verify_internal_jwt(token)

        assert exc_info.value.code == "JWT_MISSING_USER"

    def test_invalid_tenant_id_format_raises_error(self):
        """JWT with non-UUID tenant_id should raise JWTVerificationError."""
        from middleware.jwt_auth import verify_internal_jwt, JWTVerificationError

        now = int(time.time())
        payload = {
            "tenant_id": "not-a-uuid",
            "user_id": "auth0|user",
            "iss": "eq-frontend",
            "aud": "eq-backend",
            "iat": now,
            "exp": now + 300,
        }
        token = pyjwt.encode(payload, os.environ["INTERNAL_JWT_SECRET"], algorithm="HS256")

        with pytest.raises(JWTVerificationError) as exc_info:
            verify_internal_jwt(token)

        assert exc_info.value.code == "JWT_INVALID_TENANT"

    def test_pg_user_id_extracted_when_present(self):
        """JWT with pg_user_id should populate JWTClaims.pg_user_id."""
        from middleware.jwt_auth import verify_internal_jwt

        tenant_id = str(uuid.uuid4())
        pg_user_id = str(uuid.uuid4())
        token = generate_test_jwt(
            tenant_id=tenant_id,
            user_id="auth0|user-xyz",
            pg_user_id=pg_user_id,
        )

        claims = verify_internal_jwt(token)

        assert claims.pg_user_id == pg_user_id
        assert claims.tenant_id == tenant_id

    def test_pg_user_id_none_when_absent(self):
        """JWT without pg_user_id should have JWTClaims.pg_user_id as None."""
        from middleware.jwt_auth import verify_internal_jwt

        token = generate_test_jwt(tenant_id=str(uuid.uuid4()))

        claims = verify_internal_jwt(token)

        assert claims.pg_user_id is None


class TestUnifiedAuthContext:
    """Tests for the unified get_auth_context function."""

    @pytest.fixture
    def client(self):
        """Create test client."""
        from main import app
        return TestClient(app)

    @pytest.fixture
    def mock_services(self):
        """Mock external services for integration tests."""
        with patch("services.batch_cleaner_service.BatchCleanerService.clean_transcript") as mock_clean, \
             patch("services.aws_event_publisher.AWSEventPublisher.publish_envelope") as mock_publish, \
             patch("services.intelligence_service.IntelligenceService.process_transcript") as mock_intel:
            mock_clean.return_value = "Cleaned text"
            mock_publish.return_value = {"kinesis_sequence": "123", "eventbridge_id": "456"}
            mock_intel.return_value = None
            yield

    def test_jwt_auth_works_for_text_clean(self, client, mock_services):
        """Text clean endpoint should accept JWT auth."""
        tenant_id = str(uuid.uuid4())
        user_id = "auth0|test-user"
        token = generate_test_jwt(tenant_id=tenant_id, user_id=user_id)

        response = client.post(
            "/text/clean",
            headers={"Authorization": f"Bearer {token}"},
            json={"text": "Test content for JWT auth"}
        )

        assert response.status_code == 200
        data = response.json()
        assert "interaction_id" in data
        assert data["cleaned_text"] == "Cleaned text"

    def test_no_auth_returns_401_when_legacy_disabled(self, client):
        """Without JWT and legacy disabled, should return 401."""
        with patch.dict(os.environ, {"ALLOW_LEGACY_HEADER_AUTH": "false"}):
            response = client.post(
                "/text/clean",
                headers={"X-Tenant-ID": str(uuid.uuid4()), "X-User-ID": "user"},
                json={"text": "Test"}
            )

            assert response.status_code == 401
            assert "Authorization required" in response.json()["detail"]

    def test_legacy_headers_work_when_enabled(self, client, mock_services):
        """Legacy headers should work when ALLOW_LEGACY_HEADER_AUTH=true."""
        with patch.dict(os.environ, {"ALLOW_LEGACY_HEADER_AUTH": "true"}):
            tenant_id = str(uuid.uuid4())
            response = client.post(
                "/text/clean",
                headers={
                    "X-Tenant-ID": tenant_id,
                    "X-User-ID": "legacy-user"
                },
                json={"text": "Test content"}
            )

            assert response.status_code == 200

    def test_jwt_takes_precedence_over_headers(self, client, mock_services):
        """When both JWT and headers present, JWT should be used."""
        jwt_tenant = str(uuid.uuid4())
        header_tenant = str(uuid.uuid4())
        token = generate_test_jwt(tenant_id=jwt_tenant, user_id="jwt-user")

        with patch.dict(os.environ, {"ALLOW_LEGACY_HEADER_AUTH": "true"}):
            # The JWT tenant should be used, not the header tenant
            response = client.post(
                "/text/clean",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Tenant-ID": header_tenant,  # Should be ignored
                    "X-User-ID": "header-user"  # Should be ignored
                },
                json={"text": "Test content"}
            )

            assert response.status_code == 200

    def test_invalid_jwt_returns_401(self, client):
        """Invalid JWT should return 401."""
        response = client.post(
            "/text/clean",
            headers={"Authorization": "Bearer invalid-token"},
            json={"text": "Test"}
        )

        assert response.status_code == 401


class TestBearerTokenExtraction:
    """Tests for bearer token extraction."""

    def test_extracts_token_from_valid_header(self):
        """Should extract token from valid Authorization header."""
        from middleware.jwt_auth import extract_bearer_token

        token = extract_bearer_token("Bearer abc123xyz")
        assert token == "abc123xyz"

    def test_returns_none_for_missing_header(self):
        """Should return None for missing header."""
        from middleware.jwt_auth import extract_bearer_token

        assert extract_bearer_token(None) is None

    def test_returns_none_for_non_bearer(self):
        """Should return None for non-Bearer auth."""
        from middleware.jwt_auth import extract_bearer_token

        assert extract_bearer_token("Basic abc123") is None
        assert extract_bearer_token("Digest xyz") is None

    def test_returns_none_for_empty_token(self):
        """Should return None for Bearer with no token."""
        from middleware.jwt_auth import extract_bearer_token

        assert extract_bearer_token("Bearer ") is None
        assert extract_bearer_token("Bearer") is None
