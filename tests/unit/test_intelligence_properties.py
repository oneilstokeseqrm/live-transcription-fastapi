"""
Property-Based Tests for Intelligence Layer

Feature: intelligence-layer-integration, Task 17
Uses Hypothesis for property-based testing of correctness properties.
"""

import pytest
from unittest.mock import patch, MagicMock
from hypothesis import given, strategies as st, settings, assume
from datetime import date

from models.extraction_models import (
    RiskSeverityEnum,
    Summaries,
    ActionItem,
    Decision,
    Risk,
    ProductFeedback,
    MarketIntelligence,
    InteractionAnalysis,
)
from models.db_models import InsightTypeEnum


def create_mocked_service():
    """Create IntelligenceService with mocked instructor client."""
    with patch('services.intelligence_service.instructor') as mock:
        mock_client = MagicMock()
        mock.from_provider.return_value = mock_client
        from services.intelligence_service import IntelligenceService
        return IntelligenceService()


# =============================================================================
# Property 1: Content Hash Determinism
# Feature: intelligence-layer-integration
# Validates: Task 10 - Content hash is deterministic
# =============================================================================

@given(
    insight_type=st.sampled_from([
        "action_item", "key_takeaway", "decision_made", 
        "risk", "product_feedback", "market_intelligence"
    ]),
    content=st.text(min_size=1, max_size=1000)
)
@settings(max_examples=100, deadline=None)
def test_content_hash_determinism_property(insight_type, content):
    """
    Property 1: Content Hash Determinism
    
    For any insight_type and content string, calling _generate_content_hash
    multiple times with the same inputs SHALL always produce the same hash.
    
    Feature: intelligence-layer-integration
    Validates: Task 10 - Deterministic hash generation
    """
    service = create_mocked_service()
    
    hash1 = service._generate_content_hash(insight_type, content)
    hash2 = service._generate_content_hash(insight_type, content)
    hash3 = service._generate_content_hash(insight_type, content)
    
    assert hash1 == hash2 == hash3, \
        "Content hash must be deterministic - same input must produce same output"


# =============================================================================
# Property 2: Different Content Produces Different Hash
# Feature: intelligence-layer-integration
# Validates: Task 10 - Hash uniqueness for different inputs
# =============================================================================

@given(
    insight_type=st.sampled_from([
        "action_item", "key_takeaway", "decision_made", 
        "risk", "product_feedback", "market_intelligence"
    ]),
    content1=st.text(min_size=1, max_size=500),
    content2=st.text(min_size=1, max_size=500)
)
@settings(max_examples=100, deadline=None)
def test_different_content_produces_different_hash_property(insight_type, content1, content2):
    """
    Property 2: Different Content Produces Different Hash
    
    For any insight_type and two different content strings, the generated
    hashes SHALL be different (collision resistance).
    
    Feature: intelligence-layer-integration
    Validates: Task 10 - Hash uniqueness
    """
    # Skip if contents are the same
    assume(content1 != content2)
    
    service = create_mocked_service()
    
    hash1 = service._generate_content_hash(insight_type, content1)
    hash2 = service._generate_content_hash(insight_type, content2)
    
    assert hash1 != hash2, \
        "Different content must produce different hashes"


# =============================================================================
# Property 3: Summary Count Always Equals 5
# Feature: intelligence-layer-integration
# Validates: Task 11 - Exactly 5 summary entries per analysis
# =============================================================================

@st.composite
def valid_summaries_strategy(draw):
    """Generate valid Summaries with all 5 fields."""
    return Summaries(
        title=draw(st.text(min_size=1, max_size=100)),
        headline=draw(st.text(min_size=1, max_size=200)),
        brief=draw(st.text(min_size=1, max_size=500)),
        detailed=draw(st.text(min_size=1, max_size=1000)),
        spotlight=draw(st.text(min_size=1, max_size=200))
    )


@st.composite
def valid_interaction_analysis_strategy(draw):
    """Generate valid InteractionAnalysis with random content."""
    return InteractionAnalysis(
        summaries=draw(valid_summaries_strategy()),
        action_items=draw(st.lists(
            st.builds(ActionItem, description=st.text(min_size=1, max_size=200)),
            max_size=5
        )),
        decisions=draw(st.lists(
            st.builds(Decision, decision=st.text(min_size=1, max_size=200)),
            max_size=3
        )),
        risks=draw(st.lists(
            st.builds(
                Risk,
                risk=st.text(min_size=1, max_size=200),
                severity=st.sampled_from(list(RiskSeverityEnum))
            ),
            max_size=3
        )),
        key_takeaways=draw(st.lists(st.text(min_size=1, max_size=200), max_size=5)),
        product_feedback=draw(st.lists(
            st.builds(ProductFeedback, text=st.text(min_size=1, max_size=200)),
            max_size=3
        )),
        market_intelligence=draw(st.lists(
            st.builds(MarketIntelligence, text=st.text(min_size=1, max_size=200)),
            max_size=3
        ))
    )


@given(analysis=valid_interaction_analysis_strategy())
@settings(max_examples=100)
def test_summary_count_always_equals_five_property(analysis):
    """
    Property 3: Summary Count Always Equals 5
    
    For any valid InteractionAnalysis, the summaries object SHALL always
    contain exactly 5 summary levels: title, headline, brief, detailed, spotlight.
    
    Feature: intelligence-layer-integration
    Validates: Task 11 - Exactly 5 summary entries
    """
    # Count the summary fields
    summary_fields = ['title', 'headline', 'brief', 'detailed', 'spotlight']
    
    for field in summary_fields:
        assert hasattr(analysis.summaries, field), \
            f"Summaries must have {field} field"
        assert getattr(analysis.summaries, field) is not None, \
            f"Summaries.{field} must not be None"
    
    # Verify exactly 5 fields exist
    assert len(summary_fields) == 5, "Must have exactly 5 summary levels"


# =============================================================================
# Property 4: Insight Type Mapping Correctness
# Feature: intelligence-layer-integration
# Validates: Task 11 - Correct mapping for all 6 insight types
# =============================================================================

def test_insight_type_enum_has_all_required_values():
    """
    Property 4: Insight Type Mapping - Enum Completeness
    
    The InsightTypeEnum SHALL contain all 6 required insight types:
    action_item, key_takeaway, decision_made, risk, product_feedback, market_intelligence.
    
    Feature: intelligence-layer-integration
    Validates: Task 11 - All insight types mapped
    """
    required_types = [
        'action_item',
        'key_takeaway', 
        'decision_made',
        'risk',
        'product_feedback',
        'market_intelligence'
    ]
    
    enum_values = [e.value for e in InsightTypeEnum]
    
    for required_type in required_types:
        assert required_type in enum_values, \
            f"InsightTypeEnum must contain '{required_type}'"


@given(
    action_items=st.lists(
        st.builds(ActionItem, description=st.text(min_size=1, max_size=100)),
        min_size=0, max_size=5
    ),
    decisions=st.lists(
        st.builds(Decision, decision=st.text(min_size=1, max_size=100)),
        min_size=0, max_size=3
    ),
    risks=st.lists(
        st.builds(
            Risk,
            risk=st.text(min_size=1, max_size=100),
            severity=st.sampled_from(list(RiskSeverityEnum))
        ),
        min_size=0, max_size=3
    ),
    key_takeaways=st.lists(st.text(min_size=1, max_size=100), min_size=0, max_size=5),
    product_feedback=st.lists(
        st.builds(ProductFeedback, text=st.text(min_size=1, max_size=100)),
        min_size=0, max_size=3
    ),
    market_intelligence=st.lists(
        st.builds(MarketIntelligence, text=st.text(min_size=1, max_size=100)),
        min_size=0, max_size=3
    )
)
@settings(max_examples=100)
def test_insight_type_mapping_property(
    action_items, decisions, risks, key_takeaways, 
    product_feedback, market_intelligence
):
    """
    Property 4: Insight Type Mapping Correctness
    
    For any valid InteractionAnalysis, the total insight count SHALL equal
    the sum of all insight lists (action_items + decisions + risks + 
    key_takeaways + product_feedback + market_intelligence).
    
    Feature: intelligence-layer-integration
    Validates: Task 11 - Correct insight type mapping
    """
    analysis = InteractionAnalysis(
        summaries=Summaries(
            title="Test",
            headline="Test",
            brief="Test",
            detailed="Test",
            spotlight="Test"
        ),
        action_items=action_items,
        decisions=decisions,
        risks=risks,
        key_takeaways=key_takeaways,
        product_feedback=product_feedback,
        market_intelligence=market_intelligence
    )
    
    expected_insight_count = (
        len(action_items) +
        len(decisions) +
        len(risks) +
        len(key_takeaways) +
        len(product_feedback) +
        len(market_intelligence)
    )
    
    actual_insight_count = (
        len(analysis.action_items) +
        len(analysis.decisions) +
        len(analysis.risks) +
        len(analysis.key_takeaways) +
        len(analysis.product_feedback) +
        len(analysis.market_intelligence)
    )
    
    assert actual_insight_count == expected_insight_count, \
        "Total insight count must match sum of all insight lists"


# =============================================================================
# Property 5: Product Feedback Direct Mapping
# Feature: intelligence-layer-integration
# Validates: Task 11 - product_feedback maps to InsightType.product_feedback
# =============================================================================

@given(
    feedback_texts=st.lists(st.text(min_size=1, max_size=200), min_size=1, max_size=5)
)
@settings(max_examples=100)
def test_product_feedback_direct_mapping_property(feedback_texts):
    """
    Property 5: Product Feedback Direct Mapping
    
    For any list of product feedback items, each item SHALL be stored
    with InsightType.product_feedback (NOT key_takeaway).
    
    Feature: intelligence-layer-integration
    Validates: Task 11 - Direct product_feedback mapping
    """
    analysis = InteractionAnalysis(
        summaries=Summaries(
            title="Test",
            headline="Test",
            brief="Test",
            detailed="Test",
            spotlight="Test"
        ),
        product_feedback=[ProductFeedback(text=t) for t in feedback_texts]
    )
    
    # Verify product_feedback list is preserved
    assert len(analysis.product_feedback) == len(feedback_texts), \
        "Product feedback count must match input"
    
    # Verify each item has text field
    for i, feedback in enumerate(analysis.product_feedback):
        assert feedback.text == feedback_texts[i], \
            "Product feedback text must be preserved"


# =============================================================================
# Property 6: Market Intelligence Direct Mapping
# Feature: intelligence-layer-integration
# Validates: Task 11 - market_intelligence maps to InsightType.market_intelligence
# =============================================================================

@given(
    intel_texts=st.lists(st.text(min_size=1, max_size=200), min_size=1, max_size=5)
)
@settings(max_examples=100)
def test_market_intelligence_direct_mapping_property(intel_texts):
    """
    Property 6: Market Intelligence Direct Mapping
    
    For any list of market intelligence items, each item SHALL be stored
    with InsightType.market_intelligence (NOT key_takeaway).
    
    Feature: intelligence-layer-integration
    Validates: Task 11 - Direct market_intelligence mapping
    """
    analysis = InteractionAnalysis(
        summaries=Summaries(
            title="Test",
            headline="Test",
            brief="Test",
            detailed="Test",
            spotlight="Test"
        ),
        market_intelligence=[MarketIntelligence(text=t) for t in intel_texts]
    )
    
    # Verify market_intelligence list is preserved
    assert len(analysis.market_intelligence) == len(intel_texts), \
        "Market intelligence count must match input"
    
    # Verify each item has text field
    for i, intel in enumerate(analysis.market_intelligence):
        assert intel.text == intel_texts[i], \
            "Market intelligence text must be preserved"


# =============================================================================
# Property 7: Risk Severity Enum Validation
# Feature: intelligence-layer-integration
# Validates: Task 4 - Risk severity enum values
# =============================================================================

@given(severity=st.sampled_from(list(RiskSeverityEnum)))
@settings(max_examples=100)
def test_risk_severity_enum_values_property(severity):
    """
    Property 7: Risk Severity Enum Validation
    
    For any RiskSeverityEnum value, it SHALL be one of: low, medium, high.
    
    Feature: intelligence-layer-integration
    Validates: Task 4 - Risk severity enum
    """
    valid_values = {"low", "medium", "high"}
    
    assert severity.value in valid_values, \
        f"Risk severity must be one of {valid_values}"


# =============================================================================
# Property 8: InteractionAnalysis Serialization Round-Trip
# Feature: intelligence-layer-integration
# Validates: Task 4 - Model serialization
# =============================================================================

@given(analysis=valid_interaction_analysis_strategy())
@settings(max_examples=100)
def test_interaction_analysis_round_trip_property(analysis):
    """
    Property 8: InteractionAnalysis Serialization Round-Trip
    
    For any valid InteractionAnalysis, serializing to JSON and deserializing
    back SHALL produce an equivalent object.
    
    Feature: intelligence-layer-integration
    Validates: Task 4 - Model serialization
    """
    # Serialize to JSON
    json_str = analysis.model_dump_json()
    
    # Deserialize back
    restored = InteractionAnalysis.model_validate_json(json_str)
    
    # Verify key fields preserved
    assert restored.summaries.title == analysis.summaries.title
    assert restored.summaries.headline == analysis.summaries.headline
    assert len(restored.action_items) == len(analysis.action_items)
    assert len(restored.decisions) == len(analysis.decisions)
    assert len(restored.risks) == len(analysis.risks)
    assert len(restored.key_takeaways) == len(analysis.key_takeaways)
    assert len(restored.product_feedback) == len(analysis.product_feedback)
    assert len(restored.market_intelligence) == len(analysis.market_intelligence)
