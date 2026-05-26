"""Unit tests for the shared contact_resolution.find_or_create_contact helper.

AsyncMock-based, no DB / no Docker (per feedback_test_pattern_no_docker). The
helper takes a SQLAlchemy AsyncSession as a parameter, so each test passes a
MagicMock session whose execute().mappings().one() returns a canned row.
"""
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.contact_resolution import ResolvedContactRow, find_or_create_contact

TENANT = "11111111-1111-4111-8111-111111111111"
ACCOUNT_A = "22222222-2222-4222-8222-222222222222"
ACCOUNT_B = "33333333-3333-4333-8333-333333333333"


def _session_returning(row: dict) -> MagicMock:
    """A session double whose execute(...).mappings().one() == row."""
    session = MagicMock()
    result = MagicMock()
    result.mappings.return_value.one.return_value = row
    session.execute = AsyncMock(return_value=result)
    return session


@pytest.mark.asyncio
async def test_creates_new_contact_returns_uuid_and_name():
    cid = str(uuid.uuid4())
    session = _session_returning(
        {"contact_id": cid, "first_name": "Matt", "last_name": "Scanlan", "account_id": ACCOUNT_A}
    )
    row = await find_or_create_contact(
        session=session,
        tenant_id=TENANT,
        email="Matt.Scanlan@Palantir.com",
        account_id=ACCOUNT_A,
        display_name="Matt Scanlan",
    )
    assert isinstance(row, ResolvedContactRow)
    assert row.contact_id == cid
    assert row.email == "matt.scanlan@palantir.com"  # normalized lower-case
    assert row.name == "Matt Scanlan"
    assert row.account_id == ACCOUNT_A
    assert row.account_matched is True
    # bound params normalized + typed
    params = session.execute.call_args[0][1]
    assert params["email"] == "matt.scanlan@palantir.com"
    assert params["tenant_id"] == uuid.UUID(TENANT)
    assert params["account_id"] == uuid.UUID(ACCOUNT_A)
    assert params["first_name"] == "Matt"
    assert params["last_name"] == "Scanlan"


@pytest.mark.asyncio
async def test_existing_contact_with_different_account_is_not_reassigned():
    cid = str(uuid.uuid4())
    # DB kept the existing account (COALESCE) — returns ACCOUNT_B though we asked for A
    session = _session_returning(
        {"contact_id": cid, "first_name": "Jane", "last_name": None, "account_id": ACCOUNT_B}
    )
    row = await find_or_create_contact(
        session=session,
        tenant_id=TENANT,
        email="jane@acme.com",
        account_id=ACCOUNT_A,
        display_name=None,
    )
    assert row.contact_id == cid
    assert row.account_id == ACCOUNT_B
    assert row.account_matched is False  # caller logs this for observability
    assert row.name == "Jane"


@pytest.mark.asyncio
async def test_single_word_display_name_splits_to_first_only():
    cid = str(uuid.uuid4())
    session = _session_returning(
        {"contact_id": cid, "first_name": "Cher", "last_name": None, "account_id": ACCOUNT_A}
    )
    row = await find_or_create_contact(
        session=session, tenant_id=TENANT, email="cher@acme.com",
        account_id=ACCOUNT_A, display_name="Cher",
    )
    params = session.execute.call_args[0][1]
    assert params["first_name"] == "Cher"
    assert params["last_name"] is None
    assert row.name == "Cher"


@pytest.mark.asyncio
async def test_missing_account_id_raises():
    with pytest.raises(ValueError):
        await find_or_create_contact(
            session=MagicMock(), tenant_id=TENANT, email="x@y.com", account_id=""
        )


@pytest.mark.asyncio
async def test_blank_email_raises():
    with pytest.raises(ValueError):
        await find_or_create_contact(
            session=MagicMock(), tenant_id=TENANT, email="   ", account_id=ACCOUNT_A
        )
