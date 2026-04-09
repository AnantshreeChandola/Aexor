"""
Tests for Intake ↔ ConnectionCache integration:
- Session creation triggers warm_connection_cache
- _check_provider_connections uses cached path
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from components.Intake.domain.models import ParseResult, Session
from components.Intake.service.intake_service import (
    IntakeService,
    ProviderNotConnectedError,
)

# ---------------------------------------------------------------------------
# Minimal stubs
# ---------------------------------------------------------------------------


@dataclass
class _ToolDef:
    name: str
    server_name: str = "composio"
    provider_name: str = "github"
    description: str = ""
    input_schema: dict = None

    def __post_init__(self):
        if self.input_schema is None:
            self.input_schema = {}


@dataclass
class _EntityReq:
    name: str
    required: bool = True
    description: str = ""
    default_preference_key: str | None = None


@dataclass
class _PlannerResult:
    required_entities: list
    missing_entities: list
    resolved_tools: list[str]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_session_store():
    store = AsyncMock()
    store.get = AsyncMock(return_value=None)
    store.save = AsyncMock()
    store.delete = AsyncMock(return_value=True)
    return store


@pytest.fixture
def mock_parser():
    parser = AsyncMock()
    parser.parse = AsyncMock(
        return_value=ParseResult(
            intent="schedule_meeting",
            entities={
                "attendee": "Alice",
                "attendee_email": "alice@example.com",
                "date": "2026-04-10",
                "time": "10:00",
                "duration": "30m",
            },
            constraints={},
        )
    )
    return parser


@pytest.fixture
def mock_planner():
    planner = AsyncMock()
    planner.get_required_entities = AsyncMock(
        return_value=_PlannerResult(
            required_entities=[
                _EntityReq("attendee"),
                _EntityReq("attendee_email"),
                _EntityReq("date"),
                _EntityReq("time"),
                _EntityReq("duration"),
            ],
            missing_entities=[],
            resolved_tools=["GOOGLECALENDAR_CREATE_EVENT"],
        )
    )
    return planner


@pytest.fixture
def mock_tool_catalog():
    catalog = MagicMock()
    catalog.get_tool = MagicMock(
        return_value=_ToolDef(
            name="GOOGLECALENDAR_CREATE_EVENT",
            provider_name="google_calendar",
        )
    )
    return catalog


@pytest.fixture
def mock_integration_manager():
    mgr = AsyncMock()
    mgr.warm_connection_cache = AsyncMock()
    mgr.is_user_connected_cached = AsyncMock(return_value=True)
    return mgr


@pytest.fixture
def intake_service(
    mock_session_store,
    mock_parser,
    mock_planner,
    mock_tool_catalog,
    mock_integration_manager,
):
    svc = IntakeService(
        session_store=mock_session_store,
        intent_parser=mock_parser,
        planner_service=mock_planner,
        preference_service=AsyncMock(),
        tool_catalog=mock_tool_catalog,
        integration_manager=mock_integration_manager,
    )
    return svc


# ---------------------------------------------------------------------------
# Session creation warms cache
# ---------------------------------------------------------------------------


class TestSessionCreationWarmsCache:
    async def test_new_session_triggers_warm(self, intake_service, mock_integration_manager):
        """Creating a new session should call warm_connection_cache."""
        await intake_service.process_message(
            user_id="user-1",
            message="Schedule a meeting with Alice",
            context_tier=1,
        )

        mock_integration_manager.warm_connection_cache.assert_awaited_once_with("user-1")

    async def test_existing_session_does_not_warm(
        self,
        intake_service,
        mock_session_store,
        mock_integration_manager,
    ):
        """Resuming an existing session should NOT re-warm."""
        existing = Session(session_id="ses_existing", user_id="user-1")
        mock_session_store.get.return_value = existing

        await intake_service.process_message(
            user_id="user-1",
            message="With Bob too",
            context_tier=1,
            session_id="ses_existing",
        )

        mock_integration_manager.warm_connection_cache.assert_not_awaited()

    async def test_warm_failure_does_not_block_session(
        self,
        intake_service,
        mock_integration_manager,
    ):
        """If cache warming fails, session should still be created."""
        mock_integration_manager.warm_connection_cache.side_effect = RuntimeError("Redis down")

        # Should not raise
        response = await intake_service.process_message(
            user_id="user-1",
            message="Schedule a meeting",
            context_tier=1,
        )
        assert response.session_id.startswith("ses_")

    async def test_no_integration_manager_no_error(
        self, mock_session_store, mock_parser, mock_planner
    ):
        """Without integration_manager, cache warming is silently skipped."""
        svc = IntakeService(
            session_store=mock_session_store,
            intent_parser=mock_parser,
            planner_service=mock_planner,
            preference_service=AsyncMock(),
            integration_manager=None,
        )

        response = await svc.process_message(
            user_id="user-1",
            message="Do something",
            context_tier=1,
        )
        assert response.session_id.startswith("ses_")


# ---------------------------------------------------------------------------
# Provider connection check uses cached path
# ---------------------------------------------------------------------------


class TestProviderCheckUsesCachedPath:
    async def test_uses_is_user_connected_cached(self, intake_service, mock_integration_manager):
        """_check_provider_connections should use cached method."""
        await intake_service.process_message(
            user_id="user-1",
            message="Schedule a meeting with Alice",
            context_tier=1,
        )

        mock_integration_manager.is_user_connected_cached.assert_awaited_once_with(
            "user-1", "google_calendar"
        )

    async def test_not_connected_raises(self, intake_service, mock_integration_manager):
        """If user not connected, should raise ProviderNotConnectedError."""
        mock_integration_manager.is_user_connected_cached.return_value = False

        with pytest.raises(ProviderNotConnectedError) as exc_info:
            await intake_service.process_message(
                user_id="user-1",
                message="Schedule a meeting with Alice",
                context_tier=1,
            )

        assert "google_calendar" in str(exc_info.value)
