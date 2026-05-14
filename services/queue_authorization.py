"""Owner-only V1 authorization for queue actions, with tenant_review escalation.

This module is the single source of truth for "can this user act on this
pending_account_mappings row?" in Phase 1.5. Keeping the policy in one
function (rather than inlined in every route handler) means future tier-based
or role-based extensions land in one place — see design Section 8.7
(encapsulated policy discipline).

Callers:
- `routers/queue_actions.py` — Approve / Map / Ignore HTTP routes.

V1 policy:
- The owner_user_id on the queue entry is the only user who can act in
  the common case (status='pending').
- Tenant admins (is_admin=True) can additionally act on entries that have
  been escalated to `status='tenant_review'`. The plan reserves admin
  escalation for that explicit state; admins do NOT get blanket access to
  every queue entry.
- Admin support is not yet wired in this repo: the JWT does not carry an
  admin claim. Callers should pass is_admin=False today. The parameter is
  exposed so that when admin resolution lands, only the caller-side wiring
  changes — the policy here stays put.
"""
from __future__ import annotations

from typing import Mapping


def can_act_on_queue_entry(
    user_id: str,
    queue_entry: Mapping,
    is_admin: bool = False,
) -> bool:
    """Return True if `user_id` may act on `queue_entry`.

    Args:
        user_id: The authenticated user's id (string; matches `owner_user_id`).
        queue_entry: A mapping-like row from `pending_account_mappings`.
            Must expose `owner_user_id` and `status`. Extra keys are ignored.
        is_admin: Whether the caller is a tenant admin. Today every caller
            passes False (admin escalation not yet implemented in the repo);
            see module docstring.

    Returns:
        True when the caller is the owner, OR when the caller is an admin
        and the entry's status is `tenant_review`. False otherwise.
    """
    if user_id == queue_entry.get("owner_user_id"):
        return True
    if queue_entry.get("status") == "tenant_review" and is_admin:
        return True
    return False
