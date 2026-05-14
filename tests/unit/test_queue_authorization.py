"""Unit tests for `can_act_on_queue_entry` (Task 1.5.10).

Owner-only authorization in V1, with admin escalation for `tenant_review`
status. Future tier-based extensions plug in via the `is_admin` parameter
(or a future `is_tier_leader` parameter) — one place to widen the policy,
per the encapsulated-policy discipline in design Section 8.7.
"""
from __future__ import annotations

from services.queue_authorization import can_act_on_queue_entry


OWNER_USER_ID = "auth0|owner-user-1"
OTHER_USER_ID = "auth0|other-user-2"


def _entry(*, owner_user_id: str = OWNER_USER_ID, status: str = "pending") -> dict:
    """Build a minimal pending_account_mappings row dict for the helper."""
    return {
        "owner_user_id": owner_user_id,
        "status": status,
    }


def test_owner_can_act():
    """Owner of the queue entry can always act regardless of status."""
    assert can_act_on_queue_entry(
        user_id=OWNER_USER_ID,
        queue_entry=_entry(status="pending"),
        is_admin=False,
    ) is True


def test_non_owner_cannot_act_in_v1():
    """A different user with no admin privilege cannot act."""
    assert can_act_on_queue_entry(
        user_id=OTHER_USER_ID,
        queue_entry=_entry(status="pending"),
        is_admin=False,
    ) is False


def test_admin_can_act_on_tenant_review():
    """When status == 'tenant_review', tenant admins can also act."""
    assert can_act_on_queue_entry(
        user_id=OTHER_USER_ID,
        queue_entry=_entry(status="tenant_review"),
        is_admin=True,
    ) is True


def test_non_admin_cannot_act_on_tenant_review():
    """Non-admin non-owner cannot act even on tenant_review entries."""
    assert can_act_on_queue_entry(
        user_id=OTHER_USER_ID,
        queue_entry=_entry(status="tenant_review"),
        is_admin=False,
    ) is False


def test_admin_does_not_get_blanket_access_in_pending():
    """Admin status only unlocks tenant_review entries, not pending ones.

    Prevents the policy from quietly broadening to "admins can act on any
    entry" — V1 keeps admin escalation narrow.
    """
    assert can_act_on_queue_entry(
        user_id=OTHER_USER_ID,
        queue_entry=_entry(status="pending"),
        is_admin=True,
    ) is False
