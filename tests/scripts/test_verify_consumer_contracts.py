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
    UnmappedInteractionTypeError,
    check_detail_type_against_rules,
    parse_consumer_envelope,
    resolve_detail_type,
    validate_against_consumer,
)

import pytest  # noqa: E402


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

    def test_extracts_nested_content_block_class_name(self, tmp_path: Path) -> None:
        # Codex round-4 P1: consumers use different class names for the
        # nested content model:
        #   action-item-graph        → ContentPayload
        #   eq-structured-graph-core → ContentBlock
        #   eq-interaction-threads   → ContentModel
        # Without recognizing all three, content validation silently runs
        # on 1 of 3 consumers — defeating the gate. Pin all three.
        for class_name in ("ContentPayload", "ContentBlock", "ContentModel"):
            model = tmp_path / f"{class_name}.py"
            model.write_text(
                "from pydantic import BaseModel, Field\n"
                f"class {class_name}(BaseModel):\n"
                "    text: str = Field(..., description='REQUIRED')\n"
                "class EnvelopeV1(BaseModel):\n"
                "    tenant_id: str\n"
                f"    content: {class_name} = Field(..., description='REQUIRED')\n"
            )
            reg = ConsumerRegistration(
                name=class_name,
                repo_path=tmp_path,
                envelope_model_path=f"{class_name}.py",
                rule_name="r",
            )
            shape = parse_consumer_envelope(reg)
            assert shape.required_content_fields == {"text"}, (
                f"content validation failed for class name {class_name!r}"
            )

    def test_extracts_nested_content_payload_and_format(self, tmp_path: Path) -> None:
        # Codex round-2 P2: action-item-graph constrains content via a
        # nested ContentPayload model + ContentFormat enum. AST scan
        # must pull both so the validator catches content.text=None or
        # content.format="html" drift.
        model = tmp_path / "envelope.py"
        model.write_text(
            "from enum import Enum\n"
            "from pydantic import BaseModel, Field\n"
            "class ContentFormat(str, Enum):\n"
            "    PLAIN = 'plain'\n"
            "    MARKDOWN = 'markdown'\n"
            "class ContentPayload(BaseModel):\n"
            "    text: str = Field(..., description='REQUIRED')\n"
            "    format: ContentFormat = Field(default=ContentFormat.PLAIN)\n"
            "class EnvelopeV1(BaseModel):\n"
            "    tenant_id: str\n"
            "    content: ContentPayload = Field(..., description='REQUIRED')\n"
        )
        reg = ConsumerRegistration(
            name="nested",
            repo_path=tmp_path,
            envelope_model_path="envelope.py",
            rule_name="r",
        )
        shape = parse_consumer_envelope(reg)
        assert shape.content_format_enum is not None
        assert set(shape.content_format_enum) == {"plain", "markdown"}
        assert shape.required_content_fields == {"text"}
        assert "content" in shape.required_fields
        assert "tenant_id" in shape.required_fields

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

    def test_missing_content_text_rejected_on_strict_consumer(self) -> None:
        # Codex round-2 P2: strict consumer declares ContentPayload.text
        # as required. Producer change like `content={}` would silently
        # pass before; must fail now.
        shape = ConsumerEnvelopeShape(
            consumer="strict",
            required_fields={"tenant_id", "content"},
            required_content_fields={"text"},
            content_format_enum=["plain", "markdown"],
        )
        envelope = {"tenant_id": "x", "content": {"format": "plain"}}  # no text
        result = validate_against_consumer(envelope, shape)
        assert not result.accepted
        assert any("missing required content fields" in f for f in result.findings)
        assert any("text" in f for f in result.findings)

    def test_unknown_content_format_rejected(self) -> None:
        shape = ConsumerEnvelopeShape(
            consumer="strict",
            required_fields={"tenant_id", "content"},
            required_content_fields={"text"},
            content_format_enum=["plain", "markdown"],
        )
        envelope = {"tenant_id": "x", "content": {"text": "x", "format": "html"}}
        result = validate_against_consumer(envelope, shape)
        assert not result.accepted
        assert any("content.format=" in f and "html" in f for f in result.findings)

    def test_non_dict_content_rejected(self) -> None:
        shape = ConsumerEnvelopeShape(
            consumer="strict",
            required_fields={"tenant_id", "content"},
            required_content_fields={"text"},
        )
        envelope = {"tenant_id": "x", "content": "string-not-object"}
        result = validate_against_consumer(envelope, shape)
        assert not result.accepted
        assert any("content must be an object" in f for f in result.findings)

    def test_null_required_top_level_field_rejected(self) -> None:
        # Codex round-4 P2: top-level required fields must also be
        # non-null. Producer regression like envelope.tenant_id=None
        # would pass the key-presence check but fail Pydantic.
        shape = ConsumerEnvelopeShape(
            consumer="strict",
            required_fields={"tenant_id", "user_id"},
        )
        envelope = {"tenant_id": None, "user_id": "u"}
        result = validate_against_consumer(envelope, shape)
        assert not result.accepted
        assert any("required fields are null" in f and "tenant_id" in f
                   for f in result.findings)

    def test_null_required_content_field_rejected(self) -> None:
        # Codex round-3 P2: ``content={"text": null}`` was accepted as
        # "key is present" but Pydantic rejects None on `text: str`.
        shape = ConsumerEnvelopeShape(
            consumer="strict",
            required_fields={"tenant_id", "content"},
            required_content_fields={"text"},
        )
        envelope = {"tenant_id": "x", "content": {"text": None, "format": "plain"}}
        result = validate_against_consumer(envelope, shape)
        assert not result.accepted
        assert any("null" in f and "text" in f for f in result.findings)

    def test_loose_consumer_skips_nested_check(self) -> None:
        # Loose consumer (no ContentPayload model declared) doesn't run
        # the nested validation. Producer can send any content shape.
        shape = ConsumerEnvelopeShape(
            consumer="loose",
            required_fields={"tenant_id"},
            required_content_fields=None,
        )
        envelope = {"tenant_id": "x", "content": "anything-goes"}
        result = validate_against_consumer(envelope, shape)
        assert result.accepted

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
    """Codex round-1 P2 + round-2 P1/P2: --detail-type, rule-filter
    cross-check, and producer-sync semantics."""

    def test_resolves_from_interaction_type_when_no_override(self) -> None:
        envelope = {"interaction_type": "transcript"}
        assert resolve_detail_type(envelope, None) == "EnvelopeV1.transcript"

    def test_override_wins(self) -> None:
        envelope = {"interaction_type": "transcript"}
        assert (
            resolve_detail_type(envelope, "EnvelopeV1.custom")
            == "EnvelopeV1.custom"
        )

    def test_unknown_interaction_type_raises_like_producer(self) -> None:
        # Codex round-2 P2: production's resolve_detail_type raises
        # UnmappedInteractionTypeError. The verifier must do the same
        # so --interaction-type with a new value doesn't return a false
        # green when the producer would refuse to emit.
        envelope = {"interaction_type": "something_new"}
        with pytest.raises(UnmappedInteractionTypeError) as exc_info:
            resolve_detail_type(envelope, None)
        assert "something_new" in str(exc_info.value)

    def test_missing_interaction_type_raises(self) -> None:
        with pytest.raises(UnmappedInteractionTypeError):
            resolve_detail_type({}, None)

    def test_batch_upload_maps_to_transcript_not_batch_upload(self) -> None:
        # Codex round-2 P1: the script previously had
        # batch_upload → EnvelopeV1.batch_upload, but the producer
        # collapses batch_upload to EnvelopeV1.transcript (per
        # services/account_provisioning/eventbridge_emit.py:66).
        # Mismatch caused false-positive rule-drop warnings.
        envelope = {"interaction_type": "batch_upload"}
        assert (
            resolve_detail_type(envelope, None) == "EnvelopeV1.transcript"
        )

    def test_lookup_stays_in_sync_with_producer(self) -> None:
        # Single source of truth: production's
        # services/account_provisioning/eventbridge_emit.py owns the
        # lookup. The script duplicates it (to avoid pulling boto3 +
        # sqlalchemy + DBOS into a CLI tool); this test asserts the
        # two stay byte-identical so drift fails CI immediately.
        from services.account_provisioning.eventbridge_emit import (  # noqa: E402
            INTERACTION_TYPE_TO_DETAIL_TYPE as PRODUCER_LOOKUP,
        )
        assert INTERACTION_TYPE_TO_DETAIL_TYPE == PRODUCER_LOOKUP


class TestAwsProbeBusName:
    """Codex round-5 P1: probe_eventbridge_rules must respect
    EVENTBRIDGE_BUS_NAME so the script validates against the SAME bus
    the producer emits to. Hardcoding 'default' silently reads the
    wrong ruleset in non-default deployments."""

    def test_uses_eventbridge_bus_name_env_var(self, monkeypatch) -> None:
        from scripts import verify_consumer_contracts as mod

        captured: dict = {}

        class _Paginator:
            def paginate(self, **kwargs):
                captured["kwargs"] = kwargs
                return iter([{"Rules": []}])

        class _Client:
            def get_paginator(self, op_name: str):
                captured["op"] = op_name
                return _Paginator()

        class _Boto3:
            def client(self, service: str, region_name: str):
                captured["service"] = service
                captured["region"] = region_name
                return _Client()

        monkeypatch.setenv("EVENTBRIDGE_BUS_NAME", "my-bus")
        monkeypatch.setenv("AWS_REGION", "us-west-2")
        monkeypatch.setitem(sys.modules, "boto3", _Boto3())

        rules, err = mod.probe_eventbridge_rules()
        assert err is None
        assert rules == []
        assert captured["service"] == "events"
        assert captured["region"] == "us-west-2"
        assert captured["op"] == "list_rules"
        assert captured["kwargs"]["EventBusName"] == "my-bus"

    def test_falls_back_to_default_bus_when_env_unset(self, monkeypatch) -> None:
        from scripts import verify_consumer_contracts as mod

        captured: dict = {}

        class _Paginator:
            def paginate(self, **kwargs):
                captured["kwargs"] = kwargs
                return iter([{"Rules": []}])

        class _Client:
            def get_paginator(self, op_name: str):
                return _Paginator()

        class _Boto3:
            def client(self, service: str, region_name: str):
                return _Client()

        monkeypatch.delenv("EVENTBRIDGE_BUS_NAME", raising=False)
        monkeypatch.setitem(sys.modules, "boto3", _Boto3())

        rules, err = mod.probe_eventbridge_rules()
        assert err is None
        assert captured["kwargs"]["EventBusName"] == "default"


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
