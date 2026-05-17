"""Unit tests for scripts/verify_consumer_contracts.py.

Tests the pure functions in isolation. Live AWS probe + real
consumer-repo file reads are covered by the smoke runs in the PR
description (and the script's exit code is deterministic given
inputs).
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make `scripts/` importable.
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from scripts.verify_consumer_contracts import (  # noqa: E402
    INTERACTION_TYPE_TO_DETAIL_TYPE,
    ConsumerEnvelopeShape,
    ConsumerRegistration,
    check_detail_type_against_rules,
    parse_consumer_envelope,
    resolve_detail_type,
    validate_against_consumer,
)


# ---------------------------------------------------------------------------
# AST parsing
# ---------------------------------------------------------------------------


class TestParseConsumerEnvelope:
    """Parsing real consumer envelope.py files."""

    def test_parses_strict_consumer_enums(self, tmp_path: Path) -> None:
        # Simulate the action-item-graph shape: strict SourceType +
        # InteractionType enums, plus an EnvelopeV1 with required fields.
        model = tmp_path / "envelope.py"
        model.write_text(
            "from enum import Enum\n"
            "from pydantic import BaseModel\n"
            "class SourceType(str, Enum):\n"
            "    WEB_MIC = 'web-mic'\n"
            "    API = 'api'\n"
            "    ZOOM = 'zoom'\n"
            "class InteractionType(str, Enum):\n"
            "    TRANSCRIPT = 'transcript'\n"
            "    EMAIL = 'email'\n"
            "class EnvelopeV1(BaseModel):\n"
            "    schema_version: str\n"
            "    tenant_id: str\n"
            "    optional_field: str = 'default'\n"
        )
        reg = ConsumerRegistration(
            name="fake",
            repo_path=tmp_path,
            envelope_model_path="envelope.py",
            rule_name="fake-rule",
        )
        shape = parse_consumer_envelope(reg)
        assert shape.source_enum is not None
        assert set(shape.source_enum) == {"web-mic", "api", "zoom"}
        assert set(shape.interaction_type_enum or []) == {"transcript", "email"}
        assert shape.required_fields == {"schema_version", "tenant_id"}
        assert "optional_field" not in shape.required_fields
        assert shape.parse_errors == []

    def test_parses_loose_consumer_no_enums(self, tmp_path: Path) -> None:
        # eq-structured-graph-core shape: no enum, just `source: str`.
        model = tmp_path / "envelope.py"
        model.write_text(
            "from pydantic import BaseModel\n"
            "class EnvelopeV1(BaseModel):\n"
            "    tenant_id: str\n"
            "    source: str\n"
        )
        reg = ConsumerRegistration(
            name="loose",
            repo_path=tmp_path,
            envelope_model_path="envelope.py",
            rule_name="loose-rule",
        )
        shape = parse_consumer_envelope(reg)
        assert shape.source_enum is None
        assert shape.interaction_type_enum is None
        assert "tenant_id" in shape.required_fields
        assert "source" in shape.required_fields

    def test_marks_missing_file_as_unavailable_not_parse_error(self, tmp_path: Path) -> None:
        # Codex round-1 P1: in single-repo checkouts (typical CI, fresh
        # clones), the sibling repo paths don't exist. The script must
        # NOT treat that as a hard failure — instead, mark the consumer
        # as `unavailable` so the gate remains useful for the consumers
        # that ARE in the checkout.
        reg = ConsumerRegistration(
            name="missing",
            repo_path=tmp_path,
            envelope_model_path="does_not_exist.py",
            rule_name="x",
        )
        shape = parse_consumer_envelope(reg)
        assert shape.unavailable is True
        assert shape.parse_errors == []
        assert shape.required_fields == set()

    def test_detects_pydantic_field_ellipsis_as_required(self, tmp_path: Path) -> None:
        # Codex round-1 P1: action-item-graph declares required fields as
        # `Field(..., description=...)`. The old AST logic only saw bare
        # annotations as required → strict consumer's required_fields was
        # empty, and producer drops of tenant_id/content/timestamp would
        # silently pass the script. This regression is the bug we fix.
        model = tmp_path / "envelope.py"
        model.write_text(
            "from pydantic import BaseModel, Field\n"
            "from uuid import UUID\n"
            "class EnvelopeV1(BaseModel):\n"
            "    schema_version: str = Field(default='v1')\n"
            "    tenant_id: UUID = Field(..., description='REQUIRED')\n"
            "    user_id: str = Field(..., description='REQUIRED')\n"
            "    optional_field: str | None = Field(default=None)\n"
            "    optional_with_factory: list = Field(default_factory=list)\n"
            "    bare_field: str\n"  # required, no default
            "    plain_literal: str = 'default'\n"  # optional
        )
        reg = ConsumerRegistration(
            name="pydantic-style",
            repo_path=tmp_path,
            envelope_model_path="envelope.py",
            rule_name="r",
        )
        shape = parse_consumer_envelope(reg)
        assert shape.required_fields == {"tenant_id", "user_id", "bare_field"}
        assert "schema_version" not in shape.required_fields
        assert "optional_field" not in shape.required_fields
        assert "optional_with_factory" not in shape.required_fields
        assert "plain_literal" not in shape.required_fields


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _strict_shape() -> ConsumerEnvelopeShape:
    return ConsumerEnvelopeShape(
        consumer="strict",
        source_enum=["api", "web-mic", "gmail"],
        interaction_type_enum=["transcript", "email"],
        required_fields={"tenant_id"},
    )


def _loose_shape() -> ConsumerEnvelopeShape:
    return ConsumerEnvelopeShape(
        consumer="loose",
        source_enum=None,
        interaction_type_enum=None,
        required_fields={"tenant_id"},
    )


class TestValidation:
    def test_strict_consumer_accepts_in_enum_values(self) -> None:
        envelope = {"tenant_id": "x", "source": "api", "interaction_type": "transcript"}
        result = validate_against_consumer(envelope, _strict_shape())
        assert result.accepted
        assert result.findings == []

    def test_strict_consumer_rejects_unknown_source(self) -> None:
        # This is the action-item-graph SourceType-drift bug class.
        envelope = {"tenant_id": "x", "source": "zoom", "interaction_type": "transcript"}
        result = validate_against_consumer(envelope, _strict_shape())
        assert not result.accepted
        # Finding mentions the offending field + value
        assert any("source=" in f and "zoom" in f for f in result.findings)

    def test_strict_consumer_rejects_unknown_interaction_type(self) -> None:
        # This is the batch_upload-vs-InteractionType-enum drift we just caught.
        envelope = {
            "tenant_id": "x",
            "source": "api",
            "interaction_type": "batch_upload",
        }
        result = validate_against_consumer(envelope, _strict_shape())
        assert not result.accepted
        assert any("interaction_type=" in f for f in result.findings)

    def test_loose_consumer_accepts_any_string_value(self) -> None:
        # eq-structured-graph-core's loose model accepts any source / itype.
        envelope = {
            "tenant_id": "x",
            "source": "unrecognized-value",
            "interaction_type": "some-future-type",
        }
        result = validate_against_consumer(envelope, _loose_shape())
        assert result.accepted

    def test_missing_required_field_is_reported(self) -> None:
        envelope = {"source": "api", "interaction_type": "transcript"}  # no tenant_id
        result = validate_against_consumer(envelope, _strict_shape())
        assert not result.accepted
        assert any("missing required" in f for f in result.findings)

    def test_parse_errors_short_circuit_validation(self) -> None:
        # A consumer whose envelope.py is unreadable should be flagged
        # without false-positive validation against its (empty) shape.
        shape = ConsumerEnvelopeShape(
            consumer="broken",
            parse_errors=["syntax error: nope"],
        )
        result = validate_against_consumer({"source": "api"}, shape)
        assert not result.accepted
        assert any("PARSE:" in f for f in result.findings)

    def test_unavailable_consumer_is_skipped_not_rejected(self) -> None:
        # Codex round-1 P1: a consumer whose repo isn't present should
        # produce a skipped result, not a rejection. Validates the
        # single-repo CI use case.
        shape = ConsumerEnvelopeShape(consumer="absent", unavailable=True)
        result = validate_against_consumer({"source": "api"}, shape)
        assert result.skipped is True
        # Skipped == accepted by default (the script's exit code logic).
        # Use --strict to flip this; tested in main()-level smoke runs.
        assert result.accepted is True


class TestDetailTypeLookup:
    """Codex round-1 P2: the --detail-type / rule-filter cross-check."""

    def test_resolves_from_interaction_type_when_no_override(self) -> None:
        envelope = {"interaction_type": "transcript"}
        assert resolve_detail_type(envelope, None) == "EnvelopeV1.transcript"

    def test_override_wins(self) -> None:
        envelope = {"interaction_type": "transcript"}
        assert (
            resolve_detail_type(envelope, "EnvelopeV1.custom")
            == "EnvelopeV1.custom"
        )

    def test_unknown_interaction_type_returns_none(self) -> None:
        envelope = {"interaction_type": "something_new"}
        assert resolve_detail_type(envelope, None) is None

    def test_closed_map_covers_all_documented_types(self) -> None:
        # If the producer adds a new interaction_type, this map must grow
        # to match. The test pins the current set.
        assert set(INTERACTION_TYPE_TO_DETAIL_TYPE.keys()) == {
            "transcript", "note", "meeting", "email", "batch_upload",
        }


class TestRuleFilterCrossCheck:
    """check_detail_type_against_rules: warns when a rule would drop the event."""

    def _rule(self, name: str, detail_types: list[str] | None) -> dict:
        pattern: dict = {"source": ["com.yourapp.transcription"]}
        if detail_types is not None:
            pattern["detail-type"] = detail_types
        return {"name": name, "pattern": pattern, "state": "ENABLED"}

    def test_warns_when_detail_type_not_in_filter(self) -> None:
        rules = [self._rule("aig", ["EnvelopeV1.transcript", "EnvelopeV1.email"])]
        warnings = check_detail_type_against_rules("EnvelopeV1.batch_upload", rules)
        assert warnings
        assert "DROPPED" in warnings[0]
        assert "aig" in warnings[0]

    def test_no_warning_when_detail_type_in_filter(self) -> None:
        rules = [self._rule("aig", ["EnvelopeV1.transcript"])]
        warnings = check_detail_type_against_rules("EnvelopeV1.transcript", rules)
        assert warnings == []

    def test_no_warning_when_rule_has_no_detail_type_filter(self) -> None:
        # An empty / missing detail-type filter matches everything.
        rules = [self._rule("open", None)]
        warnings = check_detail_type_against_rules("EnvelopeV1.anything", rules)
        assert warnings == []
