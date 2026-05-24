"""Unit tests for :mod:`services.granola_ingestion.path2`.

Pure-function tests of the Path 2 attendee classification + Scenario
A/C/D decision tree. The adapter wraps these with DB resolution; these
tests cover the logic boundary.
"""
from __future__ import annotations

from typing import Optional

import pytest

from services.domain_classification import DomainClass
from services.granola_ingestion.models import Attendee
from services.granola_ingestion.path2 import (
    AttendeeClassification,
    PathTwoDecision,
    Scenario,
    classify_attendees,
    decide_scenario,
    unique_unknown_business_domains,
)


def _att(email: Optional[str], name: Optional[str] = None) -> Attendee:
    return Attendee(email=email, name=name)


# ---------------------------------------------------------------------------
# classify_attendees
# ---------------------------------------------------------------------------


def test_classify_attendees_skips_no_email():
    """Granola allows nullable email; classifier silently drops those entries."""
    result = classify_attendees(
        [Attendee(email=None, name="Anon"), _att("alice@acme.com")],
        internal_domains=set(),
        domain_to_account_id={},
    )
    assert len(result) == 1
    assert result[0].email == "alice@acme.com"


def test_classify_attendees_lowercases_email_and_domain():
    """Both email and domain are lower-cased."""
    result = classify_attendees(
        [_att("ALICE@Acme.COM")], internal_domains=set(), domain_to_account_id={}
    )
    assert result[0].email == "alice@acme.com"
    assert result[0].domain == "acme.com"


def test_classify_attendees_personal_domain():
    """gmail.com et al → PERSONAL; account_id stays None."""
    result = classify_attendees(
        [_att("alice@gmail.com")], internal_domains=set(), domain_to_account_id={}
    )
    assert result[0].klass is DomainClass.PERSONAL
    assert result[0].account_id is None


def test_classify_attendees_internal_domain():
    """Tenant's connected provider domain → INTERNAL; account_id stays None.

    Even if the internal domain ALSO appears in domain_to_account_id (a
    Granola attendee that matches an internal mailbox), classify still
    routes it to INTERNAL — internal supersedes any business-account
    lookup. (This matches transcript_enrichment's policy: internal users
    are Phase 2 territory and don't act as business attendees.)
    """
    result = classify_attendees(
        [_att("ceo@my-company.com")],
        internal_domains={"my-company.com"},
        domain_to_account_id={"my-company.com": "account-uuid"},
    )
    assert result[0].klass is DomainClass.INTERNAL
    assert result[0].account_id is None


def test_classify_attendees_business_known_account():
    """BUSINESS domain that appears in domain_to_account_id → account_id set."""
    result = classify_attendees(
        [_att("bob@bigco.com")],
        internal_domains=set(),
        domain_to_account_id={"bigco.com": "acc-001"},
    )
    assert result[0].klass is DomainClass.BUSINESS
    assert result[0].account_id == "acc-001"


def test_classify_attendees_business_unknown_account():
    """BUSINESS domain NOT in domain_to_account_id → account_id None."""
    result = classify_attendees(
        [_att("bob@unknown-co.com")],
        internal_domains=set(),
        domain_to_account_id={},
    )
    assert result[0].klass is DomainClass.BUSINESS
    assert result[0].account_id is None


# ---------------------------------------------------------------------------
# decide_scenario
# ---------------------------------------------------------------------------


def _make_classification(
    *, klass: DomainClass, account_id: Optional[str], email: str = "x@y.com"
) -> AttendeeClassification:
    return AttendeeClassification(
        email=email,
        name=None,
        domain=email.split("@", 1)[1],
        klass=klass,
        account_id=account_id,
    )


def test_decide_scenario_a_one_known_account():
    """Scenario A: at least one BUSINESS attendee with an account_id."""
    classifications = [
        _make_classification(klass=DomainClass.BUSINESS, account_id="acc-1"),
    ]
    decision = decide_scenario(classifications)
    assert decision.scenario is Scenario.A_KNOWN_ANCHOR
    assert decision.anchor_account_id == "acc-1"
    assert len(decision.known_account_attendees) == 1


def test_decide_scenario_a_picks_first_known_as_anchor():
    """First-found heuristic: anchor = first known-account attendee."""
    classifications = [
        _make_classification(klass=DomainClass.BUSINESS, account_id=None, email="u@u.com"),
        _make_classification(klass=DomainClass.BUSINESS, account_id="acc-2", email="a@a.com"),
        _make_classification(klass=DomainClass.BUSINESS, account_id="acc-3", email="b@b.com"),
    ]
    decision = decide_scenario(classifications)
    assert decision.scenario is Scenario.A_KNOWN_ANCHOR
    assert decision.anchor_account_id == "acc-2"


def test_decide_scenario_a_collects_unknowns_alongside_anchor():
    """Scenario A with mixed crowd: unknown business attendees still surface
    so the adapter can queue signals for them."""
    classifications = [
        _make_classification(klass=DomainClass.BUSINESS, account_id="acc-1", email="known@k.com"),
        _make_classification(klass=DomainClass.BUSINESS, account_id=None, email="u1@u1.com"),
        _make_classification(klass=DomainClass.BUSINESS, account_id=None, email="u2@u2.com"),
        _make_classification(klass=DomainClass.PERSONAL, account_id=None, email="p@gmail.com"),
    ]
    decision = decide_scenario(classifications)
    assert decision.scenario is Scenario.A_KNOWN_ANCHOR
    assert len(decision.known_account_attendees) == 1
    assert len(decision.unknown_business_attendees) == 2
    assert len(decision.personal_attendees) == 1


def test_decide_scenario_c_only_unknown_business():
    """0 known accounts but >= 1 unknown business → Scenario C."""
    classifications = [
        _make_classification(klass=DomainClass.BUSINESS, account_id=None),
        _make_classification(klass=DomainClass.PERSONAL, account_id=None),
    ]
    decision = decide_scenario(classifications)
    assert decision.scenario is Scenario.C_DEFER_PENDING_ACCOUNT
    assert decision.anchor_account_id is None
    assert len(decision.unknown_business_attendees) == 1
    assert len(decision.personal_attendees) == 1


def test_decide_scenario_d_only_personal():
    """0 business attendees → Scenario D."""
    classifications = [
        _make_classification(klass=DomainClass.PERSONAL, account_id=None),
        _make_classification(klass=DomainClass.PERSONAL, account_id=None),
    ]
    decision = decide_scenario(classifications)
    assert decision.scenario is Scenario.D_NO_BUSINESS
    assert decision.anchor_account_id is None


def test_decide_scenario_d_only_internal():
    """0 business attendees, only internal → Scenario D."""
    classifications = [
        _make_classification(klass=DomainClass.INTERNAL, account_id=None),
    ]
    decision = decide_scenario(classifications)
    assert decision.scenario is Scenario.D_NO_BUSINESS


def test_decide_scenario_d_empty():
    """No attendees at all → Scenario D (degenerate but safe)."""
    decision = decide_scenario([])
    assert decision.scenario is Scenario.D_NO_BUSINESS
    assert decision.anchor_account_id is None


def test_decide_scenario_d_mixed_personal_internal():
    """Mix of personal + internal (no business) → Scenario D."""
    classifications = [
        _make_classification(klass=DomainClass.PERSONAL, account_id=None),
        _make_classification(klass=DomainClass.INTERNAL, account_id=None),
    ]
    decision = decide_scenario(classifications)
    assert decision.scenario is Scenario.D_NO_BUSINESS


# ---------------------------------------------------------------------------
# unique_unknown_business_domains
# ---------------------------------------------------------------------------


def test_unique_unknown_business_domains_dedups():
    """Multiple attendees from the same unknown domain → one entry."""
    classifications = [
        _make_classification(klass=DomainClass.BUSINESS, account_id=None, email="a@unk.com"),
        _make_classification(klass=DomainClass.BUSINESS, account_id=None, email="b@unk.com"),
        _make_classification(klass=DomainClass.BUSINESS, account_id=None, email="c@other.com"),
    ]
    decision = decide_scenario(classifications)
    domains = unique_unknown_business_domains(decision)
    assert domains == ["unk.com", "other.com"]


def test_unique_unknown_business_domains_preserves_order():
    """First occurrence wins; order matches attendee order (deterministic logs)."""
    classifications = [
        _make_classification(klass=DomainClass.BUSINESS, account_id=None, email="a@d3.com"),
        _make_classification(klass=DomainClass.BUSINESS, account_id=None, email="b@d1.com"),
        _make_classification(klass=DomainClass.BUSINESS, account_id=None, email="c@d2.com"),
    ]
    decision = decide_scenario(classifications)
    assert unique_unknown_business_domains(decision) == ["d3.com", "d1.com", "d2.com"]


def test_unique_unknown_business_domains_empty():
    """No unknowns → empty list."""
    classifications = [
        _make_classification(klass=DomainClass.BUSINESS, account_id="acc-1"),
    ]
    decision = decide_scenario(classifications)
    assert unique_unknown_business_domains(decision) == []
