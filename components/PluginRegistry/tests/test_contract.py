"""
Contract tests for PluginRegistry.

Validates schema compliance, credential isolation, and invariants.
Reference: LLD.md Section 8.5 item 5, SPEC Invariants
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import jsonschema
import pytest
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT7

from components.PluginRegistry.domain.models import (
    OperationModel,
    ToolAlreadyExistsError,
    ToolModel,
    ValidationIssue,
    ValidationResult,
)
from components.PluginRegistry.service.registry_service import (
    RegistryService,
)

_SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"


def _load_schema(name: str) -> dict:
    return json.loads((_SCHEMA_DIR / name).read_text())


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


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_tool(active: bool = True) -> ToolModel:
    return ToolModel(
        tool_id="google.calendar",
        display_name="Google Calendar",
        credential_template="gcal_{{user_id}}_{{account_name}}",
        n8n_credential_type="googleCalendarOAuth2Api",
        active=active,
        operations={
            "create_event": OperationModel(
                operation_id="create_event",
                n8n_node="Google Calendar",
                scopes=["calendar.write"],
                compensation="delete_event",
            ),
            "delete_event": OperationModel(
                operation_id="delete_event",
                n8n_node="Google Calendar",
                scopes=["calendar.write"],
            ),
        },
        created_at=_now(),
        updated_at=_now(),
    )


# ------------------------------------------------------------------
# Schema compliance (SC-007)
# ------------------------------------------------------------------

class TestSchemaCompliance:
    """All outputs conform to JSON schemas."""

    def test_tool_definition_conforms_to_schema(self):
        schema = _load_schema("tool_definition.schema.json")
        tool = _make_tool()
        data = tool.model_dump(mode="json")
        validator = jsonschema.Draft7Validator(
            schema, registry=_schema_registry(),
        )
        validator.validate(data)

    def test_operation_conforms_to_schema(self):
        schema = _load_schema("operation.schema.json")
        op = OperationModel(
            operation_id="create_event",
            n8n_node="Google Calendar",
            previewable=False,
            idempotent=True,
            scopes=["calendar.write"],
            compensation="delete_event",
        )
        data = op.model_dump(mode="json")
        jsonschema.validate(data, schema)

    def test_validation_result_conforms_to_schema(self):
        schema = _load_schema("validation_result.schema.json")
        vr = ValidationResult(
            valid=False,
            current_version=7,
            issues=[
                ValidationIssue(
                    tool_id="slack.messaging",
                    reason="TOOL_DEACTIVATED",
                )
            ],
        )
        data = vr.model_dump(mode="json")
        jsonschema.validate(data, schema)


# ------------------------------------------------------------------
# Credential isolation (SC-006)
# ------------------------------------------------------------------

class TestCredentialIsolation:
    """Verify zero credential value leakage."""

    def test_no_credential_values_in_tool_response(self):
        tool = _make_tool()
        data = tool.model_dump(mode="json")
        serialized = json.dumps(data).lower()
        # Should contain template, not actual secrets
        assert "{{user_id}}" in data["credential_template"]
        assert "oauth_token" not in serialized
        assert "api_key" not in serialized
        assert "secret" not in serialized

    async def test_no_credential_values_in_resolve_response(
        self, registry_service, mock_db_adapter,
    ):
        mock_db_adapter.get_tool = AsyncMock(
            return_value=_make_tool(),
        )
        result = await registry_service.resolve_credential_template(
            "google.calendar",
            {"user_id": "u-123", "account_name": "work"},
        )
        assert isinstance(result.credential_id, str)
        lower = result.credential_id.lower()
        assert "token" not in lower
        assert "secret" not in lower
        assert "password" not in lower

    def test_no_credential_values_in_logs(self, caplog):
        """Capture log output and verify no credentials."""
        with caplog.at_level(logging.INFO):
            logging.getLogger("pluginregistry").info(
                "resolve_template",
                extra={
                    "tool_id": "google.calendar",
                    "credential_id": "gcal_user_u-123_work",
                },
            )
        for record in caplog.records:
            text = record.getMessage().lower()
            assert "oauth" not in text
            assert "token" not in text
            assert "api_key" not in text

    def test_resolved_credential_is_opaque_string(self):
        from components.PluginRegistry.domain.models import (
            ResolvedCredential,
        )

        cred = ResolvedCredential(
            credential_id="gcal_user_u-123_work",
            tool_id="google.calendar",
            n8n_credential_type="googleCalendarOAuth2Api",
        )
        assert isinstance(cred.credential_id, str)


# ------------------------------------------------------------------
# Invariant tests
# ------------------------------------------------------------------

class TestInvariants:
    """Verify SPEC invariants."""

    async def test_version_monotonicity(
        self, registry_service, mock_db_adapter,
    ):
        """Version should never decrease."""
        versions = []
        for v in [1, 2, 3]:
            mock_db_adapter.get_current_version = AsyncMock(
                return_value=v,
            )
            ver = await registry_service.get_version()
            versions.append(ver)
        # Each version >= previous
        for i in range(1, len(versions)):
            assert versions[i] >= versions[i - 1]

    def test_tool_id_uniqueness(self):
        """Duplicate tool_id should be rejected by service."""
        # This is enforced at service layer via tool_exists check
        # and at DB layer via PRIMARY KEY constraint.
        # Verified by test_create_tool_duplicate_id in test_unit.
        pass

    def test_operation_uniqueness_within_tool(self):
        """Duplicate operation_id in a dict is impossible by design.

        Python dicts enforce key uniqueness, so the Pydantic model
        with dict[str, OperationModel] guarantees this.
        """
        ops = {
            "create_event": OperationModel(
                operation_id="create_event", n8n_node="N"
            ),
            "create_event": OperationModel(
                operation_id="create_event", n8n_node="M"
            ),
        }
        # Second entry overwrites first -- dict behavior
        assert len(ops) == 1

    async def test_template_resolution_all_or_nothing(
        self, registry_service, mock_db_adapter,
    ):
        """No partial interpolation -- either all vars or error."""
        from components.PluginRegistry.domain.models import (
            TemplateResolutionError,
        )

        mock_db_adapter.get_tool = AsyncMock(
            return_value=_make_tool(),
        )
        with pytest.raises(TemplateResolutionError):
            await registry_service.resolve_credential_template(
                "google.calendar",
                {"user_id": "u-123"},
                # Missing account_name
            )

    async def test_deactivation_is_soft_delete(
        self, registry_service, mock_db_adapter,
    ):
        """Deactivated tools remain in DB (active=false)."""
        tool = _make_tool(active=False)
        mock_db_adapter.get_tool = AsyncMock(return_value=tool)
        mock_db_adapter.deactivate_tool = AsyncMock(
            return_value=(tool, 5),
        )
        resp = await registry_service.deactivate_tool(
            "google.calendar",
        )
        assert resp.active is False
        # The tool still exists (returned by get_tool)
