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


# ---------------------------------------------------------------------------
# Static analysis: parse consumer envelope.py via AST (no runtime import)
# ---------------------------------------------------------------------------


@dataclass
class ConsumerEnvelopeShape:
    """Statically-extracted constraints from a consumer's envelope.py."""

    consumer: str
    source_enum: list[str] | None = None  # None = no enum (loose `source: str`)
    interaction_type_enum: list[str] | None = None
    required_fields: set[str] = field(default_factory=set)
    parse_errors: list[str] = field(default_factory=list)


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


def parse_consumer_envelope(consumer: ConsumerRegistration) -> ConsumerEnvelopeShape:
    """Read consumer's envelope.py and extract constraints relevant to us."""
    shape = ConsumerEnvelopeShape(consumer=consumer.name)
    full = consumer.repo_path / consumer.envelope_model_path
    if not full.exists():
        shape.parse_errors.append(f"missing file: {full}")
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
        elif node.name == "EnvelopeV1":
            # Required = AnnAssign with no default. Optional = default present
            # or `Optional[...]`/`X | None` annotation. We use the simpler
            # signal: AnnAssign.value is None means no default → required.
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    if item.value is None:
                        shape.required_fields.add(item.target.id)

    return shape


# ---------------------------------------------------------------------------
# Envelope under test
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@dataclass
class ValidationResult:
    consumer: str
    accepted: bool
    findings: list[str] = field(default_factory=list)


def validate_against_consumer(
    envelope: dict,
    shape: ConsumerEnvelopeShape,
) -> ValidationResult:
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
        all_rules = client.list_rules(EventBusName="default").get("Rules", [])
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
        help="Override envelope.interaction_type",
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

    args = parser.parse_args(argv)

    envelope = build_test_envelope(args)
    print("Validating envelope against known consumers:")
    print(f"  source={envelope.get('source')!r}  "
          f"interaction_type={envelope.get('interaction_type')!r}")
    print()

    results: list[ValidationResult] = []
    for consumer in CONSUMERS:
        shape = parse_consumer_envelope(consumer)
        result = validate_against_consumer(envelope, shape)
        results.append(result)

        marker = "✓" if result.accepted else "✗"
        print(f"  [{marker}] {consumer.name}")
        if consumer.notes:
            print(f"        notes: {consumer.notes}")
        for finding in result.findings:
            print(f"        - {finding}")

    print()

    # AWS rule discovery
    aws_warning_only = False
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

    print()
    rejecters = [r.consumer for r in results if not r.accepted]
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
