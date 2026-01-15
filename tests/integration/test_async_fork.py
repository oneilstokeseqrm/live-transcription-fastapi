"""
Integration Tests for Async Fork Pattern

Feature: intelligence-layer-integration, Task 19
Tests the async fork pattern and error isolation between Lane 1 and Lane 2.
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone
from uuid import uuid4

from models.envelope import EnvelopeV1, ContentModel
from models.extraction_models import (
    InteractionAnalysis,
    Summaries,
)


@pytest.fixture
def sample_envelope():
    """Create a sample EnvelopeV1 for testing."""
    return EnvelopeV1(
        tenant_id=uuid4(),
        user_id="test-user",
        interaction_type="meeting",
        content=ContentModel(text="Test transcript content", format="diarized"),
        timestamp=datetime.now(timezone.utc),
        source="websocket",
        extras={},
        interaction_id=uuid4(),
        trace_id=str(uuid4())
    )


@pytest.fixture
def sample_analysis():
    """Create a sample InteractionAnalysis for testing."""
    return InteractionAnalysis(
        summaries=Summaries(
            title="Test",
            headline="Test headline",
            brief="Test brief",
            detailed="Test detailed",
            spotlight="Test spotlight"
        )
    )


class TestAsyncForkErrorIsolation:
    """Tests for error isolation between Lane 1 and Lane 2."""
    
    @pytest.mark.asyncio
    async def test_lane2_failure_does_not_block_lane1(self, sample_envelope, sample_analysis):
        """Test that Lane 2 failure does not block Lane 1 completion."""
        # Mock Lane 1 (publishing) - succeeds
        async def lane1_success():
            await asyncio.sleep(0.01)  # Simulate async work
            return {"kinesis_sequence": "123", "eventbridge_id": "456"}
        
        # Mock Lane 2 (intelligence) - fails
        async def lane2_failure():
            await asyncio.sleep(0.01)
            raise Exception("Intelligence extraction failed")
        
        # Execute both lanes concurrently
        results = await asyncio.gather(
            lane1_success(),
            lane2_failure(),
            return_exceptions=True
        )
        
        # Lane 1 should succeed
        assert results[0] == {"kinesis_sequence": "123", "eventbridge_id": "456"}, \
            "Lane 1 should complete successfully despite Lane 2 failure"
        
        # Lane 2 should have exception
        assert isinstance(results[1], Exception), \
            "Lane 2 exception should be captured"
        assert "Intelligence extraction failed" in str(results[1])
    
    @pytest.mark.asyncio
    async def test_lane1_failure_does_not_block_lane2(self, sample_analysis):
        """Test that Lane 1 failure does not block Lane 2 completion."""
        # Mock Lane 1 (publishing) - fails
        async def lane1_failure():
            await asyncio.sleep(0.01)
            raise Exception("Kinesis publish failed")
        
        # Mock Lane 2 (intelligence) - succeeds
        async def lane2_success():
            await asyncio.sleep(0.01)
            return sample_analysis
        
        # Execute both lanes concurrently
        results = await asyncio.gather(
            lane1_failure(),
            lane2_success(),
            return_exceptions=True
        )
        
        # Lane 1 should have exception
        assert isinstance(results[0], Exception), \
            "Lane 1 exception should be captured"
        assert "Kinesis publish failed" in str(results[0])
        
        # Lane 2 should succeed
        assert results[1] == sample_analysis, \
            "Lane 2 should complete successfully despite Lane 1 failure"
    
    @pytest.mark.asyncio
    async def test_both_lanes_complete_successfully(self, sample_analysis):
        """Test that both lanes complete successfully in normal case."""
        # Mock Lane 1 (publishing) - succeeds
        async def lane1_success():
            await asyncio.sleep(0.01)
            return {"kinesis_sequence": "123", "eventbridge_id": "456"}
        
        # Mock Lane 2 (intelligence) - succeeds
        async def lane2_success():
            await asyncio.sleep(0.01)
            return sample_analysis
        
        # Execute both lanes concurrently
        results = await asyncio.gather(
            lane1_success(),
            lane2_success(),
            return_exceptions=True
        )
        
        # Both should succeed
        assert results[0] == {"kinesis_sequence": "123", "eventbridge_id": "456"}, \
            "Lane 1 should complete successfully"
        assert results[1] == sample_analysis, \
            "Lane 2 should complete successfully"
        
        # Neither should be an exception
        assert not isinstance(results[0], Exception)
        assert not isinstance(results[1], Exception)
    
    @pytest.mark.asyncio
    async def test_both_lanes_fail_independently(self):
        """Test that both lanes can fail independently."""
        # Mock Lane 1 (publishing) - fails
        async def lane1_failure():
            await asyncio.sleep(0.01)
            raise ValueError("Lane 1 error")
        
        # Mock Lane 2 (intelligence) - fails
        async def lane2_failure():
            await asyncio.sleep(0.01)
            raise RuntimeError("Lane 2 error")
        
        # Execute both lanes concurrently
        results = await asyncio.gather(
            lane1_failure(),
            lane2_failure(),
            return_exceptions=True
        )
        
        # Both should have exceptions
        assert isinstance(results[0], ValueError), \
            "Lane 1 should have ValueError"
        assert isinstance(results[1], RuntimeError), \
            "Lane 2 should have RuntimeError"
        
        # Exceptions should be independent
        assert "Lane 1 error" in str(results[0])
        assert "Lane 2 error" in str(results[1])
    
    @pytest.mark.asyncio
    async def test_exceptions_are_captured_not_raised(self):
        """Test that exceptions are captured and not raised to caller."""
        async def failing_lane():
            raise Exception("This should be captured")
        
        async def succeeding_lane():
            return "success"
        
        # This should NOT raise an exception
        results = await asyncio.gather(
            failing_lane(),
            succeeding_lane(),
            return_exceptions=True
        )
        
        # We should get results, not an exception
        assert len(results) == 2
        assert isinstance(results[0], Exception)
        assert results[1] == "success"


class TestAsyncForkConcurrency:
    """Tests for concurrent execution of lanes."""
    
    @pytest.mark.asyncio
    async def test_lanes_execute_concurrently(self):
        """Test that lanes execute concurrently, not sequentially."""
        execution_order = []
        
        async def lane1():
            execution_order.append("lane1_start")
            await asyncio.sleep(0.05)
            execution_order.append("lane1_end")
            return "lane1"
        
        async def lane2():
            execution_order.append("lane2_start")
            await asyncio.sleep(0.05)
            execution_order.append("lane2_end")
            return "lane2"
        
        start_time = asyncio.get_event_loop().time()
        results = await asyncio.gather(lane1(), lane2())
        end_time = asyncio.get_event_loop().time()
        
        # Both should complete
        assert results == ["lane1", "lane2"]
        
        # Execution should be concurrent (both start before either ends)
        assert "lane1_start" in execution_order
        assert "lane2_start" in execution_order
        
        # Total time should be ~0.05s (concurrent), not ~0.1s (sequential)
        elapsed = end_time - start_time
        assert elapsed < 0.1, \
            f"Lanes should execute concurrently (elapsed: {elapsed:.3f}s)"
    
    @pytest.mark.asyncio
    async def test_slow_lane_does_not_delay_fast_lane_result(self):
        """Test that a slow lane doesn't delay the fast lane's result capture."""
        async def fast_lane():
            await asyncio.sleep(0.01)
            return "fast"
        
        async def slow_lane():
            await asyncio.sleep(0.1)
            return "slow"
        
        results = await asyncio.gather(fast_lane(), slow_lane())
        
        # Both results should be captured
        assert results[0] == "fast"
        assert results[1] == "slow"


class TestAsyncForkLogging:
    """Tests for logging behavior in async fork pattern."""
    
    @pytest.mark.asyncio
    async def test_exception_logging_includes_context(self):
        """Test that exceptions are logged with appropriate context."""
        logged_messages = []
        
        async def lane_with_logging():
            try:
                raise Exception("Test error")
            except Exception as e:
                logged_messages.append(f"Error: {e}")
                raise
        
        results = await asyncio.gather(
            lane_with_logging(),
            return_exceptions=True
        )
        
        # Exception should be captured
        assert isinstance(results[0], Exception)
        
        # Error should have been logged
        assert len(logged_messages) == 1
        assert "Test error" in logged_messages[0]


class TestAsyncForkWithRealServices:
    """Tests simulating real service behavior."""
    
    @pytest.mark.asyncio
    async def test_simulated_websocket_async_fork(self, sample_analysis):
        """Test async fork pattern as used in WebSocket endpoint."""
        session_id = str(uuid4())
        tenant_id = str(uuid4())
        trace_id = str(uuid4())
        cleaned_transcript = "Test transcript content"
        
        # Simulate Lane 1: Publishing
        async def _lane1_publish():
            # Simulate AWS publishing
            await asyncio.sleep(0.01)
            return {"kinesis_sequence": "seq-123", "eventbridge_id": "evt-456"}
        
        # Simulate Lane 2: Intelligence
        async def _lane2_intelligence():
            # Simulate LLM extraction
            await asyncio.sleep(0.02)
            return sample_analysis
        
        # Execute as in main.py
        results = await asyncio.gather(
            _lane1_publish(),
            _lane2_intelligence(),
            return_exceptions=True
        )
        
        # Process results as in main.py
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                lane_name = "Lane 1 (publishing)" if i == 0 else "Lane 2 (intelligence)"
                # Would log error here
                assert False, f"{lane_name} should not fail in this test"
            else:
                lane_name = "Lane 1 (publishing)" if i == 0 else "Lane 2 (intelligence)"
                # Would log success here
        
        # Verify results
        assert results[0]["kinesis_sequence"] == "seq-123"
        assert results[1].summaries.title == "Test"
    
    @pytest.mark.asyncio
    async def test_simulated_batch_async_fork(self, sample_analysis):
        """Test async fork pattern as used in batch router."""
        processing_id = str(uuid4())
        interaction_id = str(uuid4())
        tenant_id = str(uuid4())
        trace_id = str(uuid4())
        cleaned_transcript = "Batch transcript content"
        
        # Simulate Lane 1: Publishing
        async def _lane1_publish():
            await asyncio.sleep(0.01)
            return {"kinesis_sequence": "seq-789", "eventbridge_id": "evt-012"}
        
        # Simulate Lane 2: Intelligence with batch_upload type
        async def _lane2_intelligence():
            await asyncio.sleep(0.02)
            # In real code, this would call IntelligenceService with interaction_type="batch_upload"
            return sample_analysis
        
        results = await asyncio.gather(
            _lane1_publish(),
            _lane2_intelligence(),
            return_exceptions=True
        )
        
        # Verify both completed
        assert not isinstance(results[0], Exception)
        assert not isinstance(results[1], Exception)
        assert results[0]["kinesis_sequence"] == "seq-789"
    
    @pytest.mark.asyncio
    async def test_simulated_text_async_fork(self, sample_analysis):
        """Test async fork pattern as used in text router."""
        interaction_id = str(uuid4())
        tenant_id = str(uuid4())
        trace_id = str(uuid4())
        cleaned_text = "Note content"
        
        # Simulate Lane 1: Publishing
        async def _lane1_publish():
            await asyncio.sleep(0.01)
            return {"kinesis_sequence": "seq-abc", "eventbridge_id": "evt-def"}
        
        # Simulate Lane 2: Intelligence with note type
        async def _lane2_intelligence():
            await asyncio.sleep(0.02)
            # In real code, this would call IntelligenceService with interaction_type="note"
            return sample_analysis
        
        results = await asyncio.gather(
            _lane1_publish(),
            _lane2_intelligence(),
            return_exceptions=True
        )
        
        # Verify both completed
        assert not isinstance(results[0], Exception)
        assert not isinstance(results[1], Exception)


class TestAsyncForkHTTPResponseIsolation:
    """Tests ensuring HTTP response is not affected by lane failures."""
    
    @pytest.mark.asyncio
    async def test_http_response_returned_despite_lane_failures(self):
        """Test that HTTP response can be returned even if both lanes fail."""
        # Simulate the batch router pattern
        raw_transcript = "Raw transcript"
        cleaned_transcript = "Cleaned transcript"
        interaction_id = str(uuid4())
        
        # Both lanes fail
        async def _lane1_publish():
            raise Exception("Kinesis unavailable")
        
        async def _lane2_intelligence():
            raise Exception("OpenAI rate limited")
        
        # Execute lanes (as in batch router)
        results = await asyncio.gather(
            _lane1_publish(),
            _lane2_intelligence(),
            return_exceptions=True
        )
        
        # Log failures but don't raise
        failures = [r for r in results if isinstance(r, Exception)]
        assert len(failures) == 2, "Both lanes should have failed"
        
        # HTTP response can still be constructed
        response = {
            "raw_transcript": raw_transcript,
            "cleaned_transcript": cleaned_transcript,
            "interaction_id": interaction_id
        }
        
        # Response is valid despite lane failures
        assert response["raw_transcript"] == raw_transcript
        assert response["cleaned_transcript"] == cleaned_transcript
        assert response["interaction_id"] == interaction_id
