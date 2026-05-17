#!/usr/bin/env python3
"""Verify an outgoing EnvelopeV1 emission against all live downstream consumers.

Closes Item 5 of `tasks/downstream/test-discipline-gaps-2026-05-15.md`:
catches the bug class that produced the `action-item-graph` `SourceType`
enum drift (live-transcription-fastapi about to emit a `source` value that
the downstream consumer's Pydantic enum doesn't accept). Static-only —
no AWS round trips required for the validation half. Optional AWS probe
enumerates the live EventBridge rules to discover consumers the plan may
have failed to document (plan §3.4 documented 2; live rules show 3+).

Usage:
    python scripts/verify_consumer_contracts.py
        Default: validate the Phase 1.5 backfill envelope profile against
        all known consumers + probe AWS for rule discovery.

    python scripts/verify_consumer_contracts.py --source zoom
        Test a hypothetical envelope with source="zoom" before adding it
        to the producer. Reports which consumers would reject it.

    python scripts/verify_consumer_contracts.py --interaction-type batch_upload
    python scripts/verify_consumer_contracts.py --detail-type EnvelopeV1.transcript

    python scripts/verify_consumer_contracts.py --no-aws
        Skip the AWS rule probe (works in CI without credentials).

    python scripts/verify_consumer_contracts.py --envelope-file env.json
        Load a full envelope JSON (Detail body) from a file and validate it.

Exit codes:
    0 = All known consumers accept the envelope
    1 = At least one consumer would reject (drift detected)
    2 = AWS probe / config error (only when --aws is required)
    3 = CLI argument error
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final


# ---------------------------------------------------------------------------
# Known consumer registry
# ---------------------------------------------------------------------------
#
# Hardcoded because the consumer envelope.py paths don't change often and
# AST-loading from a sibling repo is more reliable than configuration. Add
# new consumers here when EventBridge rules grow — the AWS probe section
# below reports any rule names not present in this map so this stays current.

_REPO_PARENT: Final = Path(__file__).resolve().parents[2]  # /Users/peteroneil/EQ-CORE


@dataclass(frozen=True)
class ConsumerRegistration:
    """A downstream consumer that subscribes to our EnvelopeV1 events."""

    name: str
    repo_path: Path
    envelope_model_path: str  # relative to repo_path
    rule_name: str  # EventBridge rule that routes our events to this consumer
    notes: str = ""


CONSUMERS: Final = [
    ConsumerRegistration(
        name="action-item-graph",
        repo_path=_REPO_PARENT / "action-item-graph",
        envelope_model_path="src/action_item_graph/models/envelope.py",
        rule_name="action-item-graph-rule",
        notes="strict SourceType + InteractionType enums",
    ),
    ConsumerRegistration(
        name="eq-structured-graph-core",
        repo_path=_REPO_PARENT / "eq-structured-graph-core",
        envelope_model_path="app/models/envelope.py",
        rule_name="eq-structured-graph-rule",
        notes="loose source: str + interaction_type: str (no enum constraint)",
    ),
    ConsumerRegistration(
        name="eq-interaction-threads",
        repo_path=_REPO_PARENT / "eq-interaction-threads",
        envelope_model_path="src/models/envelope.py",
        rule_name="eq-interaction-threads-rule",
        notes="loose model",
    ),
]


# Plan §3.4 documented only 2 consumers; the live AWS probe surfaces 3+.
# Rules we know about but don't map to a known local repo go into this
# allowlist so the script doesn't flag them as drift every run.
KNOWN_UNMAPPED_RULES: Final = frozenset({
    "capture-transcripts-rule",  # destination not yet investigated
})


# Class names consumers use for the nested ``EnvelopeV1.content`` model.
# Round-4 P1 fix (2026-05-17): previously hardcoded to "ContentPayload",
# which silently skipped content validation for 2 of 3 live consumers.
_CONTENT_MODEL_CLASS_NAMES: Final = frozenset({
    "ContentPayload",  # action-item-graph
    "ContentBlock",    # eq-structured-graph-core
    "ContentModel",    # eq-interaction-threads
})


# ---------------------------------------------------------------------------
# Static analysis: parse consumer envelope.py via AST (no runtime import)
# ---------------------------------------------------------------------------


@dataclass
class ConsumerEnvelopeShape:
    """Statically-extracted constraints from a consumer's envelope.py."""

    consumer: str
    source_enum: list[str] | None = None  # None = no enum (loose `source: str`)
    interaction_type_enum: list[str] | None = None
    # `content.format` is an enum on the strict consumer (action-item-graph
    # uses ContentFormat ∈ {plain, markdown, diarized, email}). Loose
    # consumers don't declare one. None = no enum.
    content_format_enum: list[str] | None = None
    # `content` is a nested model on the strict consumer (ContentPayload).
    # required_content_fields is the set of fields that must appear inside
    # the envelope's `content` dict. None = no nested model declared (loose
    # consumers accept any content shape).
    required_content_fields: set[str] | None = None
    required_fields: set[str] = field(default_factory=set)
    parse_errors: list[str] = field(default_factory=list)
    # True when the consumer's local repo isn't on disk (e.g. CI checked out
    # only this repo). The script reports + skips this consumer instead of
    # failing the run, so the gate is still useful in single-repo CI.
    unavailable: bool = False


def _extract_str_enum_values(class_node: ast.ClassDef) -> list[str]:
    """Pull string literal values from an Enum-subclass class body."""
    values: list[str] = []
    for item in class_node.body:
        if isinstance(item, ast.Assign):
            # `MEMBER = 'literal'`
            if (
                isinstance(item.value, ast.Constant)
                and isinstance(item.value.value, str)
            ):
                values.append(item.value.value)
        elif isinstance(item, ast.AnnAssign):
            # `MEMBER: str = 'literal'` — supported by Python typed enums.
            if (
                isinstance(item.value, ast.Constant)
                and isinstance(item.value.value, str)
            ):
                values.append(item.value.value)
    return values


def _ann_assign_is_required(item: ast.AnnAssign) -> bool:
    """Decide whether a Pydantic-style annotated assignment marks a required field.

    Required cases:
      - No default at all:                  ``tenant_id: UUID``
      - Pydantic ``Field(...)`` with ``...`` as first positional arg:
        ``tenant_id: UUID = Field(..., description='REQUIRED')``

    Optional cases (NOT required):
      - Any literal default:                ``schema_version: str = 'v1'``
      - ``Field(default=...)`` / ``Field(default_factory=...)`` —
        Pydantic-supplied default, even with no positional first arg
      - Bare ``Field()`` with no args — Pydantic treats this as optional
    """
    if item.value is None:
        return True
    call = item.value
    if not isinstance(call, ast.Call):
        return False  # any concrete literal default → optional
    func_name = call.func.attr if isinstance(call.func, ast.Attribute) else (
        call.func.id if isinstance(call.func, ast.Name) else None
    )
    if func_name != "Field":
        return False
    # First positional arg `...` (Ellipsis) → required.
    if call.args:
        first = call.args[0]
        if isinstance(first, ast.Constant) and first.value is Ellipsis:
            return True
        # Any other positional first arg is treated as a default value → optional.
        return False
    # No positional args: optional UNLESS Pydantic's `default=...` keyword
    # explicitly carries Ellipsis (rare but valid Pydantic v2).
    for kw in call.keywords:
        if kw.arg == "default":
            if isinstance(kw.value, ast.Constant) and kw.value.value is Ellipsis:
                return True
            return False
        if kw.arg == "default_factory":
            return False
    # `Field()` with no args at all: Pydantic v2 treats this as optional.
    return False


def parse_consumer_envelope(consumer: ConsumerRegistration) -> ConsumerEnvelopeShape:
    """Read consumer's envelope.py and extract constraints relevant to us."""
    shape = ConsumerEnvelopeShape(consumer=consumer.name)
    full = consumer.repo_path / consumer.envelope_model_path
    if not full.exists():
        # Missing sibling repo isn't a parse error — it's "this consumer's
        # source is not in the current checkout." The script reports +
        # skips so the gate is still useful in single-repo CI.
        shape.unavailable = True
        return shape

    try:
        tree = ast.parse(full.read_text())
    except SyntaxError as exc:
        shape.parse_errors.append(f"syntax error parsing {full}: {exc}")
        return shape

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if node.name == "SourceType":
            shape.source_enum = _extract_str_enum_values(node)
        elif node.name == "InteractionType":
            shape.interaction_type_enum = _extract_str_enum_values(node)
        elif node.name == "ContentFormat":
            # str-enum on action-item-graph: PLAIN/MARKDOWN/DIARIZED/EMAIL.
            shape.content_format_enum = _extract_str_enum_values(node)
        elif node.name in _CONTENT_MODEL_CLASS_NAMES:
            # Nested model for the envelope's ``content`` field. Consumers
            # name this differently:
            #   action-item-graph        → ContentPayload
            #   eq-structured-graph-core → ContentBlock
            #   eq-interaction-threads   → ContentModel
            # Round-4 P1 (2026-05-17): without this set, content validation
            # silently ran on 1 of 3 consumers — defeating the gate.
            # Catches producer changes like content.text=None or a bogus
            # content.format that would pass the top-level required-field
            # check but fail Pydantic at parse time.
            shape.required_content_fields = set()
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    if _ann_assign_is_required(item):
                        shape.required_content_fields.add(item.target.id)
        elif node.name == "EnvelopeV1":
            # Required if there's no default OR the default is `Field(...)`
            # (Pydantic's "required field with metadata" idiom). Detection
            # logic is centralized in ``_ann_assign_is_required`` so future
            # Pydantic patterns can be added in one place.
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    if _ann_assign_is_required(item):
                        shape.required_fields.add(item.target.id)

    return shape


# ---------------------------------------------------------------------------
# Envelope under test
# ---------------------------------------------------------------------------


# Closed map from envelope.interaction_type → EventBridge DetailType.
# **DUPLICATED from services/account_provisioning/eventbridge_emit.py.**
# A unit test (tests/scripts/test_verify_consumer_contracts.py) imports
# the producer's lookup and asserts equality, so drift fails CI.
# Importing the producer module directly here would drag in boto3 +
# sqlalchemy + DBOS, which defeats this script's purpose as a
# lightweight pre-merge gate runnable in single-repo CI.
#
# CRITICAL semantics: ``batch_upload`` maps to ``EnvelopeV1.transcript``,
# NOT ``EnvelopeV1.batch_upload``. Per the producer's comment:
# batch_upload is what routers/batch.py + routers/upload.py write to
# raw_interactions.interaction_type for transcript-content paths
# (file upload + batch processing); downstream consumers should treat
# them as transcripts.
INTERACTION_TYPE_TO_DETAIL_TYPE: Final = {
    "transcript": "EnvelopeV1.transcript",
    "meeting": "EnvelopeV1.meeting",
    "note": "EnvelopeV1.note",
    "email": "EnvelopeV1.email",
    "batch_upload": "EnvelopeV1.transcript",
}


class UnmappedInteractionTypeError(ValueError):
    """Raised when ``interaction_type`` is not in the closed lookup.

    Mirrors the producer's ``services.account_provisioning.types``
    exception. The producer fails loud (no synthetic default) so
    this verifier must do the same — otherwise a developer could
    use --interaction-type to vet a new value and get a false green
    while Step 6 in production would raise.
    """


# Mirrors the Phase 1.5 backfill emit step's envelope (services/account_provisioning/eventbridge_emit.py).
DEFAULT_ENVELOPE: Final = {
    "schema_version": "v1",
    "tenant_id": "11111111-1111-4111-8111-111111111111",
    "user_id": "b0000000-0000-4000-8000-000000000002",
    "interaction_type": "transcript",
    "content": {"text": "", "format": "plain"},
    "timestamp": "2026-05-17T12:00:00+00:00",
    "source": "api",
    "interaction_id": "00000000-0000-4000-8000-000000000001",
    "account_id": "11111111-1111-4111-8111-111111111111",
    "extras": {
        "contact_ids": [],
        "contacts": [],
        "account_provisioning_queue_id": "00000000-0000-4000-8000-000000000002",
    },
}


def build_test_envelope(args: argparse.Namespace) -> dict:
    """Compose the envelope to validate from CLI overrides + defaults."""
    if args.envelope_file is not None:
        try:
            return json.loads(args.envelope_file.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            print(f"ENVELOPE FILE ERROR: {exc}", file=sys.stderr)
            sys.exit(3)

    env = dict(DEFAULT_ENVELOPE)
    if args.source is not None:
        env["source"] = args.source
    if args.interaction_type is not None:
        env["interaction_type"] = args.interaction_type
    return env


def resolve_detail_type(envelope: dict, override: str | None) -> str:
    """Pick the DetailType the producer would emit for this envelope.

    If the CLI supplied ``--detail-type`` explicitly, use it. Otherwise,
    derive from ``envelope.interaction_type`` via the closed lookup —
    mirroring what production's
    ``services.account_provisioning.eventbridge_emit.resolve_detail_type``
    does. Raises ``UnmappedInteractionTypeError`` on unknown types so
    that --interaction-type with a not-yet-mapped value fails the gate
    instead of returning a misleading green.
    """
    if override is not None:
        return override
    itype = envelope.get("interaction_type")
    if not isinstance(itype, str):
        raise UnmappedInteractionTypeError(
            f"envelope.interaction_type is missing or not a string: {itype!r}"
        )
    try:
        return INTERACTION_TYPE_TO_DETAIL_TYPE[itype]
    except KeyError as exc:
        raise UnmappedInteractionTypeError(
            f"interaction_type={itype!r} is not in the closed "
            f"INTERACTION_TYPE_TO_DETAIL_TYPE lookup. Either extend the "
            f"lookup (this script + the producer) AND the live "
            f"EventBridge rule patterns, or fix the upstream type "
            f"assignment."
        ) from exc


def check_detail_type_against_rules(
    detail_type: str,
    rules: list[dict],
) -> list[str]:
    """Return a list of WARNING strings for rules that would drop the event.

    A rule "drops" the event when:
      - The rule's ``detail-type`` filter is a closed list AND
        ``detail_type`` is not in it.
    An empty detail-type filter (rare in our setup) matches everything.
    """
    warnings: list[str] = []
    for rule in rules:
        filter_types = rule["pattern"].get("detail-type")
        if not filter_types:
            continue  # no filter or open-ended → matches anything
        if detail_type not in filter_types:
            warnings.append(
                f"rule {rule['name']!r} filters detail-types "
                f"{filter_types!r}; DetailType {detail_type!r} would be DROPPED"
            )
    return warnings


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@dataclass
class ValidationResult:
    consumer: str
    accepted: bool
    findings: list[str] = field(default_factory=list)
    skipped: bool = False  # consumer repo unavailable → not a rejection


def validate_against_consumer(
    envelope: dict,
    shape: ConsumerEnvelopeShape,
) -> ValidationResult:
    if shape.unavailable:
        # Consumer's source isn't in this checkout (e.g., single-repo CI).
        # Don't treat as rejection — just report skipped so the gate still
        # works for the consumers we CAN see.
        return ValidationResult(
            consumer=shape.consumer,
            accepted=True,
            skipped=True,
            findings=["repo not present in this checkout (skipped)"],
        )

    result = ValidationResult(consumer=shape.consumer, accepted=True)

    if shape.parse_errors:
        result.findings.extend(f"PARSE: {e}" for e in shape.parse_errors)
        result.accepted = False
        return result

    if shape.source_enum is not None:
        source_value = envelope.get("source")
        if source_value not in shape.source_enum:
            result.accepted = False
            result.findings.append(
                f"source={source_value!r} NOT in SourceType enum "
                f"{sorted(shape.source_enum)}"
            )

    if shape.interaction_type_enum is not None:
        itype = envelope.get("interaction_type")
        if itype not in shape.interaction_type_enum:
            result.accepted = False
            result.findings.append(
                f"interaction_type={itype!r} NOT in InteractionType enum "
                f"{sorted(shape.interaction_type_enum)}"
            )

    # Required-field check: only flag fields the envelope flat-out doesn't
    # set. The envelope schema is forgiving on optional fields, so don't
    # over-claim.
    missing_required = shape.required_fields - set(envelope.keys())
    if missing_required:
        result.accepted = False
        result.findings.append(
            f"missing required fields: {sorted(missing_required)}"
        )
    # Round-4 P2: required top-level fields must also be non-null.
    # The Pydantic models declare these as non-Optional types (e.g.
    # ``tenant_id: UUID = Field(...)``), so envelope[k] = None would
    # be rejected at parse time. Same logic we already apply to the
    # nested content fields.
    null_required = {
        k for k in shape.required_fields
        if k in envelope and envelope[k] is None
    }
    if null_required:
        result.accepted = False
        result.findings.append(
            f"required fields are null: {sorted(null_required)}"
        )

    # Nested content validation: only when the consumer declares a
    # ContentPayload-style model (loose consumers skip this check).
    if shape.required_content_fields is not None:
        content = envelope.get("content")
        if content is None:
            # `content` is itself a required EnvelopeV1 field on strict
            # consumers; the missing_required check above catches that.
            # Nothing further to check here.
            pass
        elif not isinstance(content, dict):
            result.accepted = False
            result.findings.append(
                f"content must be an object, got {type(content).__name__}"
            )
        else:
            missing_content = shape.required_content_fields - set(content.keys())
            if missing_content:
                result.accepted = False
                result.findings.append(
                    f"missing required content fields: {sorted(missing_content)}"
                )
            # Round-3 P2: required content fields can't be None either —
            # the consumer's Pydantic model declares them as `str`, not
            # `Optional[str]`. Catches the false-green for
            # ``content={"text": null, "format": "plain"}``.
            null_content = {
                k for k in shape.required_content_fields
                if k in content and content[k] is None
            }
            if null_content:
                result.accepted = False
                result.findings.append(
                    f"required content fields are null: {sorted(null_content)}"
                )
            if shape.content_format_enum is not None and "format" in content:
                fmt = content.get("format")
                if fmt not in shape.content_format_enum:
                    result.accepted = False
                    result.findings.append(
                        f"content.format={fmt!r} NOT in ContentFormat enum "
                        f"{sorted(shape.content_format_enum)}"
                    )

    return result


# ---------------------------------------------------------------------------
# Optional: AWS EventBridge rule probe
# ---------------------------------------------------------------------------


def probe_eventbridge_rules() -> tuple[list[dict], str | None]:
    """Return (matching_rules, error_message). Empty list + None on no rules."""
    try:
        import boto3  # local import — only loaded when this fn is called
    except ImportError as exc:
        return [], f"boto3 not installed: {exc}"

    try:
        client = boto3.client(
            "events",
            region_name=os.getenv("AWS_REGION", "us-east-1"),
        )
        # Round-4 P3 fix: ListRules is paginated. We page through all
        # results so a later rule (or detail-type filter) on page 2+ can't
        # silently be missed. Our default bus today has ~12 rules, well
        # within a single page, but pagination future-proofs the gate
        # without meaningful cost.
        all_rules: list[dict] = []
        paginator = client.get_paginator("list_rules")
        for page in paginator.paginate(EventBusName="default"):
            all_rules.extend(page.get("Rules", []))
    except Exception as exc:  # noqa: BLE001 — surface any AWS error verbatim
        return [], f"AWS probe failed: {type(exc).__name__}: {exc}"

    matching: list[dict] = []
    for rule in all_rules:
        pattern = rule.get("EventPattern", "")
        if not pattern:
            continue
        try:
            parsed = json.loads(pattern)
        except json.JSONDecodeError:
            continue
        sources = parsed.get("source") or []
        if "com.yourapp.transcription" in sources:
            matching.append({
                "name": rule.get("Name"),
                "pattern": parsed,
                "state": rule.get("State"),
            })
    return matching, None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="verify_consumer_contracts.py",
        description=(
            "Validate an EnvelopeV1 emission against all known downstream "
            "consumers' Pydantic constraints. Exits 0 if all accept, 1 if "
            "any reject."
        ),
    )
    parser.add_argument(
        "--source",
        help="Override envelope.source (e.g. 'zoom' to test if a new value works)",
    )
    parser.add_argument(
        "--interaction-type",
        help="Override envelope.interaction_type (auto-maps to DetailType "
             "via INTERACTION_TYPE_TO_DETAIL_TYPE)",
    )
    parser.add_argument(
        "--detail-type",
        help="Override the EventBridge DetailType the producer would emit. "
             "Defaults to the closed-lookup mapping from interaction_type. "
             "Used to cross-check against live EventBridge rule filters.",
    )
    parser.add_argument(
        "--envelope-file",
        type=Path,
        help="JSON file containing the envelope Detail body to validate",
    )
    parser.add_argument(
        "--no-aws",
        action="store_true",
        help="Skip the AWS EventBridge rule probe (useful in CI / no-creds)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat skipped consumers (repo not in checkout) as failures. "
             "Default: skip + report so the gate is useful in single-repo CI.",
    )

    args = parser.parse_args(argv)

    envelope = build_test_envelope(args)
    try:
        detail_type = resolve_detail_type(envelope, args.detail_type)
    except UnmappedInteractionTypeError as exc:
        # Mirror the production producer: unmapped types are a hard fail,
        # never a silent default. Avoids the "false green" Codex round-2
        # P2 flagged when --interaction-type vets a new value the producer
        # would refuse to emit.
        print(f"UNMAPPED INTERACTION TYPE: {exc}", file=sys.stderr)
        return 1
    print("Validating envelope against known consumers:")
    print(f"  source={envelope.get('source')!r}  "
          f"interaction_type={envelope.get('interaction_type')!r}  "
          f"DetailType={detail_type!r}")
    print()

    results: list[ValidationResult] = []
    for consumer in CONSUMERS:
        shape = parse_consumer_envelope(consumer)
        result = validate_against_consumer(envelope, shape)
        results.append(result)

        if result.skipped:
            marker = "—"
        elif result.accepted:
            marker = "✓"
        else:
            marker = "✗"
        print(f"  [{marker}] {consumer.name}")
        if consumer.notes:
            print(f"        notes: {consumer.notes}")
        for finding in result.findings:
            print(f"        - {finding}")

    print()

    # AWS rule discovery + DetailType cross-check.
    aws_warning_only = False
    rule_drop_warnings: list[str] = []
    if not args.no_aws:
        rules, err = probe_eventbridge_rules()
        if err is not None:
            print(f"AWS probe skipped: {err}", file=sys.stderr)
            aws_warning_only = True
        else:
            print(f"Live EventBridge rules filtering for our source ({len(rules)} found):")
            known_rule_names = {c.rule_name for c in CONSUMERS} | KNOWN_UNMAPPED_RULES
            unmapped: list[str] = []
            for rule in rules:
                name = rule["name"]
                detail_types = rule["pattern"].get("detail-type", [])
                marker = "·" if name in known_rule_names else "?"
                print(f"  [{marker}] {name}  state={rule['state']}")
                print(f"        detail-types: {detail_types}")
                if name not in known_rule_names:
                    unmapped.append(name)
            if unmapped:
                print()
                print(
                    "WARNING: rules not registered in CONSUMERS or "
                    "KNOWN_UNMAPPED_RULES — investigate + update this script:",
                    file=sys.stderr,
                )
                for u in unmapped:
                    print(f"  - {u}", file=sys.stderr)
            if detail_type is not None:
                rule_drop_warnings = check_detail_type_against_rules(
                    detail_type, rules
                )
                if rule_drop_warnings:
                    print()
                    print(
                        f"DETAIL-TYPE WARNING: emit step would set "
                        f"DetailType={detail_type!r}, but some rules would drop it:",
                        file=sys.stderr,
                    )
                    for w in rule_drop_warnings:
                        print(f"  - {w}", file=sys.stderr)

    print()
    skipped = [r.consumer for r in results if r.skipped]
    # Round-3 P2: in --strict, treat skipped consumers as rejections so the
    # gate actually fires in single-repo CI. Skipped results are constructed
    # with accepted=True for the default code path; --strict overrides that.
    if args.strict:
        rejecters = [r.consumer for r in results if not r.accepted or r.skipped]
    else:
        rejecters = [r.consumer for r in results if not r.accepted]
        if skipped:
            print(
                f"SKIPPED {len(skipped)} consumer(s) "
                f"(repo not in checkout): {skipped}",
                file=sys.stderr,
            )

    # Round-2 P1: if AWS surfaced rule-filter drops for the computed
    # DetailType, fail. Otherwise CI could green-light a PR that
    # introduces a new detail type the live rules won't forward.
    if rule_drop_warnings:
        print(
            f"DETAIL-TYPE DROP: {len(rule_drop_warnings)} live rule(s) "
            f"would filter out DetailType={detail_type!r}",
            file=sys.stderr,
        )
        return 1
    if rejecters:
        print(f"DRIFT: {len(rejecters)} consumer(s) would reject the envelope: "
              f"{rejecters}", file=sys.stderr)
        return 1

    print("OK: all known consumers accept the envelope")
    if aws_warning_only:
        return 0  # AWS probe was a soft check; validation passed
    return 0


if __name__ == "__main__":
    sys.exit(main())
