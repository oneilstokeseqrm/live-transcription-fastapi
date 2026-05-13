"""3-tier name resolution: display_name -> email heuristic -> Tavily."""

import pytest
from services.name_resolution import (
    resolve_name,
    heuristic_name_from_email,
    NameResolution,
)


def test_tier1_display_name_wins():
    result = resolve_name(email="x@y.com", display_name="Jane Smith", tavily_client=None)
    assert result is not None
    assert result.first_name == "Jane"
    assert result.last_name == "Smith"
    assert result.source == "display_name"


def test_tier2_heuristic_from_email():
    result = heuristic_name_from_email("jane.smith@acme.com")
    assert result == ("Jane", "Smith")


def test_tier2_heuristic_dash():
    assert heuristic_name_from_email("jane-smith@acme.com") == ("Jane", "Smith")


def test_tier2_heuristic_underscore():
    assert heuristic_name_from_email("jane_smith@acme.com") == ("Jane", "Smith")


def test_tier2_heuristic_unconfident_initials():
    # j.smith@acme.com -> ambiguous; heuristic returns None to escalate to Tavily
    assert heuristic_name_from_email("j.smith@acme.com") is None


def test_tier3_no_tavily_client_returns_none():
    result = resolve_name(email="ambiguous@acme.com", display_name=None, tavily_client=None)
    assert result is None
