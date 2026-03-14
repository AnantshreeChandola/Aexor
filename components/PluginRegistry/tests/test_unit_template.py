"""
Unit tests for credential template resolution.

Reference: LLD.md Section 8.5 item 2
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from components.PluginRegistry.domain.models import (
    TemplateResolutionError,
    ToolModel,
    ToolNotFoundError,
)


def _make_tool(
    template: str = "gcal_user_{{user_id}}_{{account_name}}",
    active: bool = True,
) -> ToolModel:
    now = datetime.now(UTC)
    return ToolModel(
        tool_id="google.calendar",
        display_name="Google Calendar",
        credential_template=template,
        n8n_credential_type="googleCalendarOAuth2Api",
        active=active,
        created_at=now,
        updated_at=now,
    )


class TestResolveTemplateHappyPath:
    """Happy-path template resolution tests."""

    async def test_resolve_template_happy_path(
        self,
        registry_service,
        mock_db_adapter,
    ):
        mock_db_adapter.get_tool = AsyncMock(
            return_value=_make_tool(),
        )
        result = await registry_service.resolve_credential_template(
            "google.calendar",
            {"user_id": "u-123", "account_name": "work"},
        )
        assert result.credential_id == "gcal_user_u-123_work"
        assert result.tool_id == "google.calendar"
        assert result.n8n_credential_type == "googleCalendarOAuth2Api"

    async def test_resolve_template_extra_variables_ignored(
        self,
        registry_service,
        mock_db_adapter,
    ):
        mock_db_adapter.get_tool = AsyncMock(
            return_value=_make_tool(),
        )
        result = await registry_service.resolve_credential_template(
            "google.calendar",
            {
                "user_id": "u-123",
                "account_name": "work",
                "extra": "ignored",
            },
        )
        assert result.credential_id == "gcal_user_u-123_work"

    async def test_resolve_template_sanitization_allows_hyphen(
        self,
        registry_service,
        mock_db_adapter,
    ):
        mock_db_adapter.get_tool = AsyncMock(
            return_value=_make_tool(),
        )
        result = await registry_service.resolve_credential_template(
            "google.calendar",
            {"user_id": "u-123", "account_name": "my-work"},
        )
        assert result.credential_id == "gcal_user_u-123_my-work"

    async def test_resolve_template_sanitization_allows_underscore(
        self,
        registry_service,
        mock_db_adapter,
    ):
        mock_db_adapter.get_tool = AsyncMock(
            return_value=_make_tool(),
        )
        result = await registry_service.resolve_credential_template(
            "google.calendar",
            {"user_id": "u_123", "account_name": "work_main"},
        )
        assert result.credential_id == "gcal_user_u_123_work_main"


class TestResolveTemplateErrors:
    """Error-path template resolution tests."""

    async def test_resolve_template_missing_variable(
        self,
        registry_service,
        mock_db_adapter,
    ):
        mock_db_adapter.get_tool = AsyncMock(
            return_value=_make_tool(),
        )
        with pytest.raises(TemplateResolutionError) as exc_info:
            await registry_service.resolve_credential_template(
                "google.calendar",
                {"user_id": "u-123"},
            )
        assert "account_name" in exc_info.value.missing_variables

    async def test_resolve_template_sanitization_rejects_slashes(
        self,
        registry_service,
        mock_db_adapter,
    ):
        mock_db_adapter.get_tool = AsyncMock(
            return_value=_make_tool(),
        )
        with pytest.raises(TemplateResolutionError):
            await registry_service.resolve_credential_template(
                "google.calendar",
                {"user_id": "../etc", "account_name": "work"},
            )

    async def test_resolve_template_sanitization_rejects_braces(
        self,
        registry_service,
        mock_db_adapter,
    ):
        mock_db_adapter.get_tool = AsyncMock(
            return_value=_make_tool(),
        )
        with pytest.raises(TemplateResolutionError):
            await registry_service.resolve_credential_template(
                "google.calendar",
                {"user_id": "{{}}", "account_name": "work"},
            )

    async def test_resolve_template_sanitization_rejects_spaces(
        self,
        registry_service,
        mock_db_adapter,
    ):
        mock_db_adapter.get_tool = AsyncMock(
            return_value=_make_tool(),
        )
        with pytest.raises(TemplateResolutionError):
            await registry_service.resolve_credential_template(
                "google.calendar",
                {"user_id": "user 123", "account_name": "work"},
            )

    async def test_resolve_template_sanitization_rejects_semicolon(
        self,
        registry_service,
        mock_db_adapter,
    ):
        mock_db_adapter.get_tool = AsyncMock(
            return_value=_make_tool(),
        )
        with pytest.raises(TemplateResolutionError):
            await registry_service.resolve_credential_template(
                "google.calendar",
                {
                    "user_id": ";DROP TABLE users",
                    "account_name": "work",
                },
            )

    async def test_resolve_template_empty_variable_value(
        self,
        registry_service,
        mock_db_adapter,
    ):
        mock_db_adapter.get_tool = AsyncMock(
            return_value=_make_tool(),
        )
        with pytest.raises(TemplateResolutionError):
            await registry_service.resolve_credential_template(
                "google.calendar",
                {"user_id": "", "account_name": "work"},
            )

    async def test_resolve_template_tool_not_found(
        self,
        registry_service,
        mock_db_adapter,
    ):
        mock_db_adapter.get_tool = AsyncMock(return_value=None)
        with pytest.raises(ToolNotFoundError):
            await registry_service.resolve_credential_template(
                "nonexistent.tool",
                {"user_id": "u-123"},
            )


class TestCredentialIsolation:
    """Verify resolved credential is an opaque string reference."""

    async def test_credential_id_never_contains_secrets(
        self,
        registry_service,
        mock_db_adapter,
    ):
        mock_db_adapter.get_tool = AsyncMock(
            return_value=_make_tool(),
        )
        result = await registry_service.resolve_credential_template(
            "google.calendar",
            {"user_id": "u-123", "account_name": "work"},
        )
        # credential_id is a simple string, not a token/key
        assert isinstance(result.credential_id, str)
        assert "oauth" not in result.credential_id.lower()
        assert "token" not in result.credential_id.lower()
        assert "secret" not in result.credential_id.lower()
