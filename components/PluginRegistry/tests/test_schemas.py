"""
Schema validation tests for PluginRegistry.

Validates JSON schemas and Pydantic model serialization.
Reference: LLD.md Section 8.5, FR-006
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT7

from components.PluginRegistry.domain.models import (
    OperationModel,
    ToolModel,
    ValidationResult,
)

_SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"


def _load_schema(name: str) -> dict:
    path = _SCHEMA_DIR / name
    return json.loads(path.read_text())


def _schema_registry() -> Registry:
    """Build a referencing.Registry for local $ref resolution."""
    pairs = []
    for path in _SCHEMA_DIR.glob("*.schema.json"):
        contents = json.loads(path.read_text())
        resource = Resource.from_contents(
            contents, default_specification=DRAFT7,
        )
        pairs.append((path.name, resource))
    return Registry().with_resources(pairs)


@pytest.fixture()
def tool_schema() -> dict:
    return _load_schema("tool_definition.schema.json")


@pytest.fixture()
def operation_schema() -> dict:
    return _load_schema("operation.schema.json")


@pytest.fixture()
def validation_result_schema() -> dict:
    return _load_schema("validation_result.schema.json")


# ------------------------------------------------------------------
# Tool definition schema tests
# ------------------------------------------------------------------

class TestToolDefinitionSchema:
    """Tests for tool_definition.schema.json."""

    def test_valid_tool_validates(self, tool_schema):
        doc = {
            "tool_id": "google.calendar",
            "display_name": "Google Calendar",
            "credential_template": "gcal_{{user_id}}",
            "n8n_credential_type": "googleCalendarOAuth2Api",
            "operations": {
                "create_event": {
                    "n8n_node": "Google Calendar",
                    "previewable": False,
                    "idempotent": True,
                    "scopes": ["calendar.write"],
                }
            },
        }
        # Should not raise
        validator = jsonschema.Draft7Validator(
            tool_schema, registry=_schema_registry(),
        )
        validator.validate(doc)

    def test_invalid_tool_id_rejected(self, tool_schema):
        doc = {
            "tool_id": "INVALID",
            "display_name": "X",
            "credential_template": "x",
            "n8n_credential_type": "x",
            "operations": {"op": {"n8n_node": "N"}},
        }
        validator = jsonschema.Draft7Validator(
            tool_schema, registry=_schema_registry(),
        )
        with pytest.raises(jsonschema.ValidationError):
            validator.validate(doc)

    def test_missing_required_fields_rejected(self, tool_schema):
        doc = {"tool_id": "a.b"}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(doc, tool_schema)

    def test_tool_id_missing_dot_rejected(self, tool_schema):
        doc = {
            "tool_id": "googlecalendar",
            "display_name": "G",
            "credential_template": "x",
            "n8n_credential_type": "x",
            "operations": {"op": {"n8n_node": "N"}},
        }
        validator = jsonschema.Draft7Validator(
            tool_schema, registry=_schema_registry(),
        )
        with pytest.raises(jsonschema.ValidationError):
            validator.validate(doc)


# ------------------------------------------------------------------
# Operation schema tests
# ------------------------------------------------------------------

class TestOperationSchema:
    """Tests for operation.schema.json."""

    def test_valid_operation_validates(self, operation_schema):
        doc = {
            "n8n_node": "Google Calendar",
            "previewable": True,
            "idempotent": True,
            "scopes": ["calendar.read"],
            "compensation": None,
        }
        jsonschema.validate(doc, operation_schema)

    def test_minimal_operation_validates(self, operation_schema):
        doc = {"n8n_node": "Slack"}
        jsonschema.validate(doc, operation_schema)

    def test_missing_n8n_node_rejected(self, operation_schema):
        doc = {"previewable": True}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(doc, operation_schema)


# ------------------------------------------------------------------
# Validation result schema tests
# ------------------------------------------------------------------

class TestValidationResultSchema:
    """Tests for validation_result.schema.json."""

    def test_valid_pass_result(self, validation_result_schema):
        doc = {"valid": True, "current_version": 5}
        jsonschema.validate(doc, validation_result_schema)

    def test_valid_fail_result_with_issues(
        self, validation_result_schema
    ):
        doc = {
            "valid": False,
            "current_version": 7,
            "issues": [
                {
                    "tool_id": "slack.messaging",
                    "reason": "TOOL_DEACTIVATED",
                }
            ],
        }
        jsonschema.validate(doc, validation_result_schema)

    def test_missing_valid_field_rejected(
        self, validation_result_schema
    ):
        doc = {"current_version": 5}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(doc, validation_result_schema)


# ------------------------------------------------------------------
# Pydantic model tests
# ------------------------------------------------------------------

class TestPydanticModels:
    """Verify Pydantic models serialize/deserialize correctly."""

    def test_tool_model_round_trip(self, sample_tool_model):
        data = sample_tool_model.model_dump(mode="json")
        restored = ToolModel.model_validate(data)
        assert restored.tool_id == sample_tool_model.tool_id
        assert len(restored.operations) == len(
            sample_tool_model.operations
        )

    def test_tool_id_pattern_rejects_uppercase(self):
        with pytest.raises(ValidationError):
            ToolModel(
                tool_id="Google.Calendar",
                display_name="x",
                credential_template="x",
                n8n_credential_type="x",
            )

    def test_tool_id_pattern_rejects_no_dot(self):
        with pytest.raises(ValidationError):
            ToolModel(
                tool_id="googlecalendar",
                display_name="x",
                credential_template="x",
                n8n_credential_type="x",
            )

    def test_operation_id_pattern_rejects_uppercase(self):
        with pytest.raises(ValidationError):
            OperationModel(
                operation_id="CreateEvent",
                n8n_node="N",
            )

    def test_operation_id_pattern_rejects_short(self):
        with pytest.raises(ValidationError):
            OperationModel(
                operation_id="x",
                n8n_node="N",
            )

    def test_validation_result_serializes(self):
        vr = ValidationResult(valid=True, current_version=3)
        data = vr.model_dump()
        assert data["valid"] is True
        assert data["current_version"] == 3
        assert data["issues"] == []
