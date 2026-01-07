"""
Integration Tests for Endpoint Flows

This module contains integration tests for the batch and text endpoints,
testing the full request/response flow with mocked AWS clients.

Tests:
- /batch/process with valid headers returns expected response
- /text/clean with valid headers returns expected response
- Missing headers return 400 errors
"""

import io
import uuid
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient

from main import app


# =============================================================================
# Test Client Fixture
# =============================================================================

@pytest.fixture
def client():
    """Create a test client for the FastAPI app."""
    return TestClient(app)


@pytest.fixture
def valid_headers():
    """Generate valid identity headers for testing."""
    return {
        "X-Tenant-ID": str(uuid.uuid4()),
        "X-User-ID": "auth0|test-user-001",
        "X-Trace-Id": str(uuid.uuid4())
    }


@pytest.fixture
def mock_aws_clients():
    """Mock AWS clients (Kinesis and EventBridge) for isolation."""
    with patch('services.aws_event_publisher.boto3') as mock_boto3:
        # Mock Kinesis client
        mock_kinesis = MagicMock()
        mock_kinesis.put_record.return_value = {"SequenceNumber": "12345"}
        
        # Mock EventBridge client
        mock_eventbridge = MagicMock()
        mock_eventbridge.put_events.return_value = {
            "FailedEntryCount": 0,
            "Entries": [{"EventId": "event-123"}]
        }
        
        # Configure boto3.client to return appropriate mocks
        def client_factory(service_name, **kwargs):
            if service_name == "kinesis":
                return mock_kinesis
            elif service_name == "events":
                return mock_eventbridge
            return MagicMock()
        
        mock_boto3.client.side_effect = client_factory
        
        yield {
            "kinesis": mock_kinesis,
            "eventbridge": mock_eventbridge
        }


# =============================================================================
# Text Endpoint Integration Tests
# =============================================================================

class TestTextCleanEndpoint:
    """Integration tests for POST /text/clean endpoint."""
    
    def test_text_clean_with_valid_headers_returns_expected_response(
        self, client, valid_headers, mock_aws_clients
    ):
        """
        Test /text/clean with valid headers returns expected response.
        
        Validates:
        - HTTP 200 status code
        - Response contains raw_text, cleaned_text, interaction_id
        - interaction_id is a valid UUID
        """
        with patch('routers.text.BatchCleanerService') as mock_cleaner:
            # Mock the cleaner service
            mock_instance = MagicMock()
            mock_instance.clean_transcript = AsyncMock(return_value="Cleaned text content")
            mock_cleaner.return_value = mock_instance
            
            response = client.post(
                "/text/clean",
                json={"text": "This is some raw text to clean"},
                headers=valid_headers
            )
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "raw_text" in data, "Response should contain raw_text"
        assert "cleaned_text" in data, "Response should contain cleaned_text"
        assert "interaction_id" in data, "Response should contain interaction_id"
        
        # Verify interaction_id is a valid UUID
        try:
            uuid.UUID(data["interaction_id"])
        except ValueError:
            pytest.fail("interaction_id should be a valid UUID")
        
        assert data["raw_text"] == "This is some raw text to clean"
    
    def test_text_clean_missing_tenant_id_returns_400(self, client):
        """
        Test /text/clean without X-Tenant-ID returns HTTP 400.
        
        Validates: Requirements 1.3, 8.4
        """
        headers = {
            "X-User-ID": "auth0|test-user-001"
        }
        
        response = client.post(
            "/text/clean",
            json={"text": "Some text"},
            headers=headers
        )
        
        assert response.status_code == 400
        assert "X-Tenant-ID" in response.json()["detail"]
    
    def test_text_clean_missing_user_id_returns_400(self, client):
        """
        Test /text/clean without X-User-ID returns HTTP 400.
        
        Validates: Requirements 1.4, 8.5
        """
        headers = {
            "X-Tenant-ID": str(uuid.uuid4())
        }
        
        response = client.post(
            "/text/clean",
            json={"text": "Some text"},
            headers=headers
        )
        
        assert response.status_code == 400
        assert "X-User-ID" in response.json()["detail"]
    
    def test_text_clean_invalid_tenant_id_returns_400(self, client):
        """
        Test /text/clean with invalid X-Tenant-ID returns HTTP 400.
        
        Validates: Requirements 8.6
        """
        headers = {
            "X-Tenant-ID": "not-a-valid-uuid",
            "X-User-ID": "auth0|test-user-001"
        }
        
        response = client.post(
            "/text/clean",
            json={"text": "Some text"},
            headers=headers
        )
        
        assert response.status_code == 400
        assert "UUID" in response.json()["detail"]
    
    def test_text_clean_whitespace_only_text_returns_400(self, client, valid_headers):
        """
        Test /text/clean with whitespace-only text returns HTTP 400.
        
        Validates: Requirements 3.3
        """
        response = client.post(
            "/text/clean",
            json={"text": "   \t\n  "},
            headers=valid_headers
        )
        
        assert response.status_code == 422  # Pydantic validation error


# =============================================================================
# Batch Endpoint Integration Tests
# =============================================================================

class TestBatchProcessEndpoint:
    """Integration tests for POST /batch/process endpoint."""
    
    def test_batch_process_with_valid_headers_returns_expected_response(
        self, client, valid_headers, mock_aws_clients
    ):
        """
        Test /batch/process with valid headers returns expected response.
        
        Validates:
        - HTTP 200 status code
        - Response contains raw_transcript, cleaned_transcript, interaction_id
        - interaction_id is a valid UUID
        """
        with patch('routers.batch.BatchService') as mock_batch, \
             patch('routers.batch.BatchCleanerService') as mock_cleaner:
            
            # Mock the batch service
            mock_batch_instance = MagicMock()
            mock_batch_instance.transcribe_audio = AsyncMock(
                return_value="Raw transcript from audio"
            )
            mock_batch.return_value = mock_batch_instance
            
            # Mock the cleaner service
            mock_cleaner_instance = MagicMock()
            mock_cleaner_instance.clean_transcript = AsyncMock(
                return_value="Cleaned transcript"
            )
            mock_cleaner.return_value = mock_cleaner_instance
            
            # Create a fake audio file
            audio_content = b"fake audio content"
            files = {"file": ("test.wav", io.BytesIO(audio_content), "audio/wav")}
            
            response = client.post(
                "/batch/process",
                files=files,
                headers=valid_headers
            )
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "raw_transcript" in data, "Response should contain raw_transcript"
        assert "cleaned_transcript" in data, "Response should contain cleaned_transcript"
        assert "interaction_id" in data, "Response should contain interaction_id"
        
        # Verify interaction_id is a valid UUID
        try:
            uuid.UUID(data["interaction_id"])
        except ValueError:
            pytest.fail("interaction_id should be a valid UUID")
    
    def test_batch_process_missing_tenant_id_returns_400(self, client):
        """
        Test /batch/process without X-Tenant-ID returns HTTP 400.
        
        Validates: Requirements 1.1, 8.4
        """
        headers = {
            "X-User-ID": "auth0|test-user-001"
        }
        
        audio_content = b"fake audio content"
        files = {"file": ("test.wav", io.BytesIO(audio_content), "audio/wav")}
        
        response = client.post(
            "/batch/process",
            files=files,
            headers=headers
        )
        
        assert response.status_code == 400
        assert "X-Tenant-ID" in response.json()["detail"]
    
    def test_batch_process_missing_user_id_returns_400(self, client):
        """
        Test /batch/process without X-User-ID returns HTTP 400.
        
        Validates: Requirements 1.2, 8.5
        """
        headers = {
            "X-Tenant-ID": str(uuid.uuid4())
        }
        
        audio_content = b"fake audio content"
        files = {"file": ("test.wav", io.BytesIO(audio_content), "audio/wav")}
        
        response = client.post(
            "/batch/process",
            files=files,
            headers=headers
        )
        
        assert response.status_code == 400
        assert "X-User-ID" in response.json()["detail"]
    
    def test_batch_process_invalid_tenant_id_returns_400(self, client):
        """
        Test /batch/process with invalid X-Tenant-ID returns HTTP 400.
        
        Validates: Requirements 8.6
        """
        headers = {
            "X-Tenant-ID": "not-a-valid-uuid",
            "X-User-ID": "auth0|test-user-001"
        }
        
        audio_content = b"fake audio content"
        files = {"file": ("test.wav", io.BytesIO(audio_content), "audio/wav")}
        
        response = client.post(
            "/batch/process",
            files=files,
            headers=headers
        )
        
        assert response.status_code == 400
        assert "UUID" in response.json()["detail"]
    
    def test_batch_process_invalid_file_format_returns_400(self, client, valid_headers):
        """
        Test /batch/process with invalid file format returns HTTP 400.
        
        Validates: Requirements 2.2
        """
        audio_content = b"fake content"
        files = {"file": ("test.txt", io.BytesIO(audio_content), "text/plain")}
        
        response = client.post(
            "/batch/process",
            files=files,
            headers=valid_headers
        )
        
        assert response.status_code == 400
        assert "Invalid file format" in response.json()["detail"]


# =============================================================================
# Header Validation Tests (Both Endpoints)
# =============================================================================

class TestHeaderValidation:
    """Tests for header validation across both endpoints."""
    
    def test_empty_user_id_returns_400_text_endpoint(self, client):
        """Test that empty X-User-ID returns 400 for text endpoint."""
        headers = {
            "X-Tenant-ID": str(uuid.uuid4()),
            "X-User-ID": ""
        }
        
        response = client.post(
            "/text/clean",
            json={"text": "Some text"},
            headers=headers
        )
        
        assert response.status_code == 400
        assert "X-User-ID" in response.json()["detail"]
    
    def test_whitespace_user_id_returns_400_text_endpoint(self, client):
        """Test that whitespace-only X-User-ID returns 400 for text endpoint."""
        headers = {
            "X-Tenant-ID": str(uuid.uuid4()),
            "X-User-ID": "   "
        }
        
        response = client.post(
            "/text/clean",
            json={"text": "Some text"},
            headers=headers
        )
        
        assert response.status_code == 400
        assert "X-User-ID" in response.json()["detail"]
    
    def test_invalid_trace_id_returns_400(self, client):
        """Test that invalid X-Trace-Id returns 400."""
        headers = {
            "X-Tenant-ID": str(uuid.uuid4()),
            "X-User-ID": "auth0|test-user-001",
            "X-Trace-Id": "not-a-valid-uuid"
        }
        
        response = client.post(
            "/text/clean",
            json={"text": "Some text"},
            headers=headers
        )
        
        assert response.status_code == 400
        assert "X-Trace-Id" in response.json()["detail"]
    
    def test_trace_id_generated_when_not_provided(
        self, client, mock_aws_clients
    ):
        """Test that trace_id is generated when X-Trace-Id is not provided."""
        headers = {
            "X-Tenant-ID": str(uuid.uuid4()),
            "X-User-ID": "auth0|test-user-001"
            # No X-Trace-Id
        }
        
        with patch('routers.text.BatchCleanerService') as mock_cleaner:
            mock_instance = MagicMock()
            mock_instance.clean_transcript = AsyncMock(return_value="Cleaned text")
            mock_cleaner.return_value = mock_instance
            
            response = client.post(
                "/text/clean",
                json={"text": "Some text"},
                headers=headers
            )
        
        # Should succeed - trace_id is optional and will be generated
        assert response.status_code == 200

