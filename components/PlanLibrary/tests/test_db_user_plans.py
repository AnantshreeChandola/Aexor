"""
Tests for DatabaseAdapter.get_plans_by_user()

Validates user-scoped plan queries.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from components.PlanLibrary.adapters.db import DatabaseAdapter


@pytest.fixture
def mock_shared_db():
    """Create a mock shared database adapter."""
    shared_db = MagicMock()
    session = AsyncMock()
    shared_db.get_session.return_value.__aenter__ = AsyncMock(return_value=session)
    shared_db.get_session.return_value.__aexit__ = AsyncMock(return_value=False)
    return shared_db, session


@pytest.fixture
def db_adapter(mock_shared_db):
    """Create DatabaseAdapter with mocked shared db."""
    shared_db, _ = mock_shared_db
    with patch(
        "components.PlanLibrary.adapters.db.get_database_adapter",
        return_value=shared_db,
    ):
        adapter = DatabaseAdapter()
    return adapter


class TestGetPlansByUser:

    @pytest.mark.asyncio
    async def test_returns_only_user_plans(self, db_adapter, mock_shared_db):
        """get_plans_by_user filters by user_id from canonical_json."""
        _, session = mock_shared_db

        # Mock rows — only user-abc's plans should be returned
        user_row = MagicMock()
        user_row._mapping = {
            "plan_id": "01HX1234567890ABCDEFGHJKMN",
            "intent_type": "schedule_meeting",
            "step_count": 3,
            "stored_at": "2025-01-01T00:00:00",
            "intent_name": "schedule_meeting",
            "intent_entities": {},
            "success": True,
            "error_type": None,
            "execution_start": "2025-01-01T00:00:00",
            "execution_end": "2025-01-01T00:01:00",
            "total_steps": 3,
            "failed_step": None,
            "context_data": None,
        }
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [user_row]
        session.execute = AsyncMock(return_value=mock_result)

        result = await db_adapter.get_plans_by_user(user_id="user-abc", limit=50)

        assert len(result) == 1
        assert result[0]["plan_id"] == "01HX1234567890ABCDEFGHJKMN"
        # Verify the query was called with user_id parameter
        call_args = session.execute.call_args
        params = call_args[0][1] if len(call_args[0]) > 1 else call_args.kwargs.get("parameters", {})
        assert params["user_id"] == "user-abc"

    @pytest.mark.asyncio
    async def test_success_only_filter(self, db_adapter, mock_shared_db):
        """success_only=True includes the success filter in query."""
        _, session = mock_shared_db

        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        session.execute = AsyncMock(return_value=mock_result)

        await db_adapter.get_plans_by_user(
            user_id="user-abc", limit=50, success_only=True
        )

        # Verify the SQL contains the success filter
        call_args = session.execute.call_args
        query_text = str(call_args[0][0])
        assert "success = true" in query_text.lower()

    @pytest.mark.asyncio
    async def test_limit_respected(self, db_adapter, mock_shared_db):
        """Limit parameter is passed to the query."""
        _, session = mock_shared_db

        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        session.execute = AsyncMock(return_value=mock_result)

        await db_adapter.get_plans_by_user(user_id="user-abc", limit=10)

        call_args = session.execute.call_args
        params = call_args[0][1] if len(call_args[0]) > 1 else call_args.kwargs.get("parameters", {})
        assert params["limit"] == 10


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
