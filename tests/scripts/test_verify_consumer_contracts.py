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
    ConsumerEnvelopeShape,
    ConsumerRegistration,
    parse_consumer_envelope,
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

    def test_reports_missing_file_as_parse_error(self, tmp_path: Path) -> None:
        reg = ConsumerRegistration(
            name="missing",
            repo_path=tmp_path,
            envelope_model_path="does_not_exist.py",
            rule_name="x",
        )
        shape = parse_consumer_envelope(reg)
        assert shape.parse_errors
        assert "missing file" in shape.parse_errors[0]


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
            parse_errors=["missing file: /nope/envelope.py"],
        )
        result = validate_against_consumer({"source": "api"}, shape)
        assert not result.accepted
        assert any("PARSE:" in f for f in result.findings)
