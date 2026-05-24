"""Path 2 attendee classification + Scenario A/B/C/D branching helpers.

Path 2 is LOCKED-26: the adapter inspects attendees BEFORE deciding what to
do with each Granola note. The branching tree:

* PERSONAL domain attendee (gmail.com, outlook.com, etc.) → skip (no
  contact, no signal).
* INTERNAL domain attendee (the tenant's own provider-connection domains)
  → skip (Phase 2 wires internal-user contacts; out of scope here).
* BUSINESS domain attendee → resolve via
  :func:`services.account_lookup.lookup_account_by_domain`. If the domain
  maps to a known account, this attendee contributes to a "known account
  candidates" set. If not, the domain contributes to a "pending domain
  signals" set so Scenario C can defer + queue.
* Once all attendees are classified, the scenario falls out of the counts:

  - **Scenario A** — ≥1 known account, ≥0 unknown business attendees.
    Pick an anchor (first-found for MVP per the brainstorm doc), build the
    envelope, ingest. Unknown business attendees in the same meeting
    queue signals via the existing :mod:`services.pending_account_mappings`
    helpers (mirrors what :mod:`services.transcript_enrichment` does today
    for unknown secondary attendees).
  - **Scenario B** — same as A; a known anchor exists, mixed crowd. The
    code path is identical. We keep the name in docs because the
    brainstorm distinguished "1 known + N unknown" from "1 known + 0
    unknown" as a UX consideration; for the adapter they're the same
    decision.
  - **Scenario C** — 0 known accounts but ≥1 business-domain attendee.
    The meeting is deferred — no envelope is emitted to downstream
    consumers, the note metadata is captured into ``granola_note_snapshot``
    per LOCKED-44, and the unknown domains are queued via
    :func:`upsert_queue_entry` + :func:`insert_signal` so the user can
    approve them. Once approved, the next poll cycle re-resolves the
    domain and re-runs Path 2 against the cached snapshot.
  - **Scenario D** — 0 business-domain attendees (all personal/internal).
    Skip; nothing to do.

This module is **pure functions over Granola payload + classification
inputs**. It does NOT touch the DB, the Granola API, the vault, or
:mod:`text_clean_service`. The adapter orchestrates DB + I/O around these
helpers. Keeps each layer testable in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from services.domain_classification import (
    DomainClass,
    classify_domain,
    email_domain,
)
from services.granola_ingestion.models import Attendee


class Scenario(str, Enum):
    """Path 2 scenario classification for a single Granola note.

    String values match LOCKED-26 nomenclature so they're stable in logs +
    metrics + decisioning. The adapter branches on this enum to choose
    the ingestion path; downstream observability filters on it.
    """

    A_KNOWN_ANCHOR = "a_known_anchor"  # ≥1 known account; may include unknowns
    C_DEFER_PENDING_ACCOUNT = "c_defer_pending_account"  # 0 known, ≥1 unknown business
    D_NO_BUSINESS = "d_no_business"  # 0 business attendees (all personal/internal)


@dataclass(frozen=True)
class AttendeeClassification:
    """One attendee's domain classification + resolved account_id (if known).

    ``email`` is normalized lower-case. ``domain`` is the lower-case email
    domain extracted from email. ``account_id`` is the looked-up
    ``account_domains.account_id`` (UUID string) when the attendee's
    domain is BUSINESS and matches a known account; ``None`` otherwise
    (including for PERSONAL/INTERNAL attendees — they never look up).

    ``name`` is propagated from Granola's ``attendees[].name`` when
    present; ``None`` when Granola only had email. Used by front-matter
    composition + the adapter's Scenario C signal proposal.
    """

    email: str
    name: Optional[str]
    domain: str
    klass: DomainClass
    account_id: Optional[str]


@dataclass(frozen=True)
class PathTwoDecision:
    """The complete output of Path 2 classification for one note.

    ``scenario`` drives the adapter's branch (Scenario A/C/D — Scenario B
    folds into A operationally). ``anchor_account_id`` is the chosen
    anchor for Scenario A (first-found among knowns; MVP heuristic per
    brainstorm Q1). ``known_account_attendees`` and
    ``unknown_business_attendees`` are surfaced so the adapter can:
    (a) emit Scenario A signal-queueing for unknowns in the same meeting,
    (b) build Scenario C's pending-domain signal set.

    ``personal_attendees`` and ``internal_attendees`` are surfaced for
    logging only — the adapter does not act on them.
    """

    scenario: Scenario
    anchor_account_id: Optional[str]
    known_account_attendees: list[AttendeeClassification] = field(default_factory=list)
    unknown_business_attendees: list[AttendeeClassification] = field(default_factory=list)
    personal_attendees: list[AttendeeClassification] = field(default_factory=list)
    internal_attendees: list[AttendeeClassification] = field(default_factory=list)


def _attendee_email(att: Attendee) -> Optional[str]:
    """Return lower-case email or None when the attendee carries no email.

    Granola's ``attendees[].email`` is nullable — ad-hoc captures may only
    carry the API key holder's email; calendar-linked meetings may have a
    contact with no email if Granola couldn't resolve one. We can't
    classify a domain without an email, so these attendees are skipped
    silently (they appear in ``personal_attendees=[]`` etc as missing,
    and the adapter doesn't act on them).
    """
    if not att.email:
        return None
    return att.email.strip().lower()


def classify_attendees(
    attendees: list[Attendee],
    *,
    internal_domains: set[str],
    domain_to_account_id: dict[str, str],
) -> list[AttendeeClassification]:
    """Classify each attendee's domain + resolve known business accounts.

    Pure function: takes the raw attendee list + pre-fetched classification
    inputs (the tenant's internal-domain set from
    :func:`get_tenant_internal_domains` and a pre-resolved
    domain → account_id mapping from
    :func:`lookup_account_by_domain` per unique business domain).

    The adapter pre-resolves ``domain_to_account_id`` in a single batch so
    this function stays pure + cheap. Missing entries in the dict mean
    "this domain was checked and isn't known"; absence vs ``None`` value
    is informational only (callers should populate every BUSINESS domain
    they see).

    Attendees without an email are skipped silently (Granola allows
    nullable emails; see :func:`_attendee_email`).
    """
    result: list[AttendeeClassification] = []
    for att in attendees:
        email = _attendee_email(att)
        if not email:
            continue
        domain = email_domain(email)
        if not domain:
            continue
        klass = classify_domain(domain, internal_domains=internal_domains)
        if klass is DomainClass.BUSINESS:
            account_id = domain_to_account_id.get(domain)
        else:
            account_id = None
        result.append(
            AttendeeClassification(
                email=email,
                name=att.name,
                domain=domain,
                klass=klass,
                account_id=account_id,
            )
        )
    return result


def decide_scenario(classifications: list[AttendeeClassification]) -> PathTwoDecision:
    """Reduce the per-attendee classifications to one :class:`PathTwoDecision`.

    Branch:
      - If ≥1 attendee is BUSINESS + has a non-None ``account_id``:
        :attr:`Scenario.A_KNOWN_ANCHOR`. Anchor = first such attendee's
        ``account_id`` (MVP first-found heuristic per brainstorm; future
        sessions may refine — e.g. "the account with the most other
        attendees" or "the account the recording user is associated
        with").
      - Else if ≥1 attendee is BUSINESS with ``account_id`` None:
        :attr:`Scenario.C_DEFER_PENDING_ACCOUNT`. anchor_account_id stays
        None — Scenario C does NOT emit an interaction.
      - Else (all PERSONAL or INTERNAL):
        :attr:`Scenario.D_NO_BUSINESS`. anchor_account_id None.

    Pure function — the adapter is responsible for translating the
    decision into DB writes, envelope construction, and signal queuing.
    """
    known: list[AttendeeClassification] = []
    unknown_business: list[AttendeeClassification] = []
    personal: list[AttendeeClassification] = []
    internal: list[AttendeeClassification] = []

    for att in classifications:
        if att.klass is DomainClass.BUSINESS:
            if att.account_id is not None:
                known.append(att)
            else:
                unknown_business.append(att)
        elif att.klass is DomainClass.PERSONAL:
            personal.append(att)
        elif att.klass is DomainClass.INTERNAL:
            internal.append(att)

    if known:
        anchor = _pick_anchor(known)
        return PathTwoDecision(
            scenario=Scenario.A_KNOWN_ANCHOR,
            anchor_account_id=anchor,
            known_account_attendees=known,
            unknown_business_attendees=unknown_business,
            personal_attendees=personal,
            internal_attendees=internal,
        )
    if unknown_business:
        return PathTwoDecision(
            scenario=Scenario.C_DEFER_PENDING_ACCOUNT,
            anchor_account_id=None,
            known_account_attendees=[],
            unknown_business_attendees=unknown_business,
            personal_attendees=personal,
            internal_attendees=internal,
        )
    return PathTwoDecision(
        scenario=Scenario.D_NO_BUSINESS,
        anchor_account_id=None,
        known_account_attendees=[],
        unknown_business_attendees=[],
        personal_attendees=personal,
        internal_attendees=internal,
    )


def _pick_anchor(known: list[AttendeeClassification]) -> str:
    """First-found heuristic for Scenario A anchor account selection.

    Per the brainstorm Q1 outcome: MVP picks the first known-account
    attendee. Future heuristics (account-with-most-attendees, account
    that includes the recording user, etc.) can refine without changing
    the call site — the adapter only needs ``anchor_account_id``.

    Pre-condition: ``known`` is non-empty (guaranteed by
    :func:`decide_scenario`'s branch).
    """
    return str(known[0].account_id)


def unique_unknown_business_domains(decision: PathTwoDecision) -> list[str]:
    """Deduplicated unknown-business domain list from a decision.

    Used by both Scenario A (to queue signals for unknowns in the same
    meeting) and Scenario C (to queue signals for the unknown business
    set that prevents ingestion). Order-preserving so logs + signal-row
    insertion order are deterministic in tests.
    """
    seen: set[str] = set()
    out: list[str] = []
    for att in decision.unknown_business_attendees:
        if att.domain in seen:
            continue
        seen.add(att.domain)
        out.append(att.domain)
    return out
