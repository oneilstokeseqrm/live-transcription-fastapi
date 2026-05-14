"""Domain classification: personal | internal | business."""

import pytest
from services.domain_classification import (
    is_personal_domain,
    classify_domain,
    DomainClass,
)


def test_personal_gmail():
    assert is_personal_domain("gmail.com") is True


def test_personal_outlook():
    assert is_personal_domain("outlook.com") is True


def test_business_domain():
    assert is_personal_domain("acme.com") is False


def test_classify_personal():
    assert classify_domain("gmail.com", internal_domains=set()) == DomainClass.PERSONAL


def test_classify_internal():
    result = classify_domain("mycompany.com", internal_domains={"mycompany.com"})
    assert result == DomainClass.INTERNAL


def test_classify_business():
    result = classify_domain("acme.com", internal_domains={"mycompany.com"})
    assert result == DomainClass.BUSINESS


def test_classify_case_insensitive():
    assert classify_domain("ACME.com", internal_domains=set()) == DomainClass.BUSINESS
