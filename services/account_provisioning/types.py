"""Pydantic models for the account-provisioning workflow.

Defined as a single module so workflow, steps, and tests share one
source of truth for the contract that flows between steps. Two of these
models (``AccountProfile``, ``AgentEnrichRun``) double as the local
declaration of the eq-agent-action-core ``/api/enrich`` response shape;
the contract-pinning test in ``tests/contract/test_agent_enrich_response_shape.py``
asserts the live response satisfies them.

Plan reference: §5.4, §6 (step boundaries), §3.2 (agent contract).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Workflow input / output
# ---------------------------------------------------------------------------


class WorkflowInput(BaseModel):
    """Input passed to ``account_provisioning_workflow``.

    Carries everything Step 1 needs to re-validate state; subsequent
    steps derive the rest from DB state to keep replays deterministic.
    """

    queue_id: str
    tenant_id: str
    approval_attempt_id: str
    re_open_count: int = 0
    effort: str = "medium"  # "low" | "medium" | "high"


class AccountProvisioningResult(BaseModel):
    """Terminal workflow result.

    Steps 5 + 6 produce the data; the workflow returns it for callers
    that want to inspect (tests, an eventual SSE endpoint).
    """

    queue_id: str
    account_id: str
    domain: str
    contact_ids: list[str] = Field(default_factory=list)
    interaction_ids: list[str] = Field(default_factory=list)
    emissions: list["EmissionRecord"] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Step-level types
# ---------------------------------------------------------------------------


class QueueState(BaseModel):
    """Snapshot of a queue row after Step 1's revalidation."""

    queue_id: str
    tenant_id: str
    domain: str
    status: str
    approval_attempt_id: Optional[str]
    re_open_count: int


class AccountProfile(BaseModel):
    """Agent enrichment payload — v2 schema (in production since 2026-03-04).

    The agent's ``/api/enrich`` response wraps the enrichment payload
    under a top-level ``result`` envelope: ``{run_id, status, result: {
    company_name, website_domain, ...}, metadata, account_id}``. The
    client's ``_parse_profile`` extracts ``result`` before validating
    against this model. Field aliases here map the agent's v2 field names
    to our local semantics so the workflow keeps reading
    ``profile.name``, ``profile.region``, etc.

    The contract-pinning test at ``tests/contract/test_agent_enrich_response_shape.py``
    is the load-bearing live-drift guard (today @needs_internal_jwt-marked;
    deferred Phase-2 follow-up to make it actually run in CI). Unit-test
    regression coverage lives in
    ``tests/unit/account_provisioning/test_agent_client.py``.

    Plan §3.2. M5.3 (2026-05-19): added aliases for v2 envelope.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    name: str = Field(..., alias="company_name")
    domain: Optional[str] = Field(default=None, alias="website_domain")
    industry: Optional[str] = None
    company_size: Optional[str] = Field(default=None, alias="employee_count_range")
    region: Optional[str] = Field(default=None, alias="headquarters")
    website: Optional[str] = Field(default=None, alias="website_domain")
    description: Optional[str] = Field(default=None, alias="one_line_description")
    company_type: Optional[str] = None


class AgentEnrichRun(BaseModel):
    """Cached identifier from a completed ``/api/enrich`` call.

    Step 3 caches ``run_id`` via ``DBOS.set_event`` so a retry after
    crash can call ``GET /api/enrich/{run_id}`` instead of paying for a
    second 30-90s enrich. ``profile`` is the AccountProfile returned by
    the initial call; the GET response should match (verified at
    execution time per plan §15 item 3).
    """

    run_id: str
    profile: AccountProfile


class MaterializationResult(BaseModel):
    """Step 5 output.

    Captures what Step 6 needs to fan out per-interaction EnvelopeV1
    emissions without re-reading from the database.

    By design (Codex P1 2026-05-16 round 5): materialization REQUIRES
    a real ``raw_interactions`` row to exist for every signal's
    interaction_id. If absent, ``materialize_account_approval`` raises
    rather than writing a placeholder. So ``interaction_ids`` is
    always emit-safe — no placeholder filtering needed downstream.

    ``promoted_interaction_ids`` is the subset of ``interaction_ids``
    that were promoted from ``pending_interactions`` during this
    approval (cold-inbound emails awaiting an account anchor). The
    M2 step ``emit_email_promoted_events`` fan-outs one EventBridge
    ``EmailPromoted`` event per promoted interaction; eq-email-pipeline
    subscribes and runs its full local enrichment (Neo4j flesh +
    Pinecone embed + LLM summary) retroactively. Empty for queues
    with no cold-inbound emails attached (legacy meeting-only case).
    """

    queue_id: str
    tenant_id: str
    account_id: str
    contact_ids: list[str] = Field(default_factory=list)
    interaction_ids: list[str] = Field(default_factory=list)
    promoted_interaction_ids: list[str] = Field(default_factory=list)


class EmittedContact(BaseModel):
    """Contact metadata included in EnvelopeV1.extras.contacts.

    Downstream consumers (action-item-graph, eq-structured-graph-core)
    read this shape — see ``tasks/downstream/action-item-graph.md`` and
    ``tasks/downstream/eq-structured-graph-core.md``.
    """

    contact_id: str
    email: str
    name: Optional[str] = None
    role: Optional[str] = None


class InteractionForEmit(BaseModel):
    """Per-interaction context Step 6 reads before constructing envelopes."""

    interaction_id: str
    interaction_type: str
    raw_text: Optional[str]
    user_id: Optional[str]
    created_at: datetime
    contacts: list[EmittedContact] = Field(default_factory=list)


class EmissionRecord(BaseModel):
    """One EnvelopeV1 emission record for the workflow result."""

    interaction_id: str
    detail_type: str
    event_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Error types (narrow per Item 3 of test-discipline-gaps)
# ---------------------------------------------------------------------------


class AgentEnrichTransientError(Exception):
    """Retry-eligible failure during ``/api/enrich`` — DBOS retries the step."""


class AgentEnrichTerminalError(Exception):
    """Fail-loud failure during ``/api/enrich`` — workflow surfaces error."""


class EventBridgeEmissionError(Exception):
    """Non-zero ``FailedEntryCount`` from ``put_events`` — DBOS retries."""


class UnmappedInteractionTypeError(Exception):
    """An interaction_type fell outside ``INTERACTION_TYPE_TO_DETAIL_TYPE``.

    Plan §3.3: the lookup is a CLOSED table; unknown types fail loud so
    the operator extends the table or fixes the upstream type assignment.
    """


# Forward-reference resolution.
AccountProvisioningResult.model_rebuild()
