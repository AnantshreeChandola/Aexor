"""
Tests for PlanService.clone_plan_for_rerun()

Validates that plan cloning preserves graph structure while replacing
entities and resetting step statuses.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from components.PlanLibrary.domain.models import PlanDB, PlanNotFoundError
from components.PlanLibrary.service.plan_service import PlanService

VALID_ULID = "01HX1234567890ABCDEFGHJKMN"
VALID_ULID_2 = "01HX9876543210ZYXWVTSRQPNM"


def _make_canonical_json(
    plan_id=VALID_ULID,
    intent_type="schedule_meeting",
    entities=None,
    constraints=None,
    step_args=None,
):
    """Build a minimal canonical_json that Plan.model_validate() accepts."""
    if entities is None:
        entities = {"title": "Team standup", "date": "2025-06-01"}
    if constraints is None:
        constraints = {"max_duration": "30m"}
    steps = [
        {
            "step": 1,
            "mode": "interactive",
            "role": "Fetcher",
            "uses": "google.calendar",
            "call": "list_events",
            "args": step_args or {"date": "{{entities.date}}"},
            "after": [],
            "timeout_s": 30,
            "dry_run": True,
            "status": "completed",
            "result": {"events": []},
            "error": None,
        },
        {
            "step": 2,
            "mode": "interactive",
            "role": "Booker",
            "uses": "google.calendar",
            "call": "create_event",
            "args": {"title": "{{entities.title}}", "date": "{{entities.date}}"},
            "after": [1],
            "timeout_s": 30,
            "dry_run": True,
            "status": "completed",
            "result": {"event_id": "abc123"},
            "error": None,
        },
    ]
    return {
        "plan_id": plan_id,
        "intent": {
            "intent": intent_type,
            "entities": entities,
            "constraints": constraints,
            "tz": "America/Chicago",
            "user_id": "user-original",
        },
        "graph": steps,
        "constraints": {"scopes": ["calendar.events.create"], "ttl_s": 900, "max_retries": 3},
        "plugins": ["google.calendar"],
        "meta": {
            "created_at": "2025-06-01T00:00:00+00:00",
            "author": "planner@system",
            "canonical_hash": "a" * 64,
        },
    }


def _make_plan_db(plan_id=VALID_ULID, canonical_json=None):
    """Build a PlanDB with valid canonical_json."""
    if canonical_json is None:
        canonical_json = _make_canonical_json(plan_id=plan_id)
    return PlanDB(
        plan_id=plan_id,
        canonical_json=canonical_json,
        signature_data={},
        intent_type="schedule_meeting",
        step_count=2,
        plan_hash="a" * 64,
        size_bytes=500,
        created_at=datetime.utcnow(),
    )


@pytest.fixture
def mock_db_adapter():
    adapter = MagicMock()
    adapter.get_plan_by_id = AsyncMock(return_value=_make_plan_db())
    adapter.store_plan_transaction = AsyncMock(return_value=True)
    adapter.get_plans_by_intent = AsyncMock(return_value=[])
    adapter.get_plan_outcomes = AsyncMock(return_value=[])
    adapter.get_success_rates = AsyncMock(return_value={})
    adapter.health_check = AsyncMock(return_value=True)
    adapter.get_plans_by_user = AsyncMock(return_value=[])
    return adapter


@pytest.fixture
def plan_service(mock_db_adapter):
    return PlanService(db_adapter=mock_db_adapter)


FRESH_ENTITIES = {"title": "New meeting", "date": "2025-07-15"}


class TestClonePlanForRerun:

    @pytest.mark.asyncio
    async def test_clone_generates_new_plan_id(self, plan_service):
        """Cloned plan has a new ULID, different from source."""
        result = await plan_service.clone_plan_for_rerun(
            source_plan_id=VALID_ULID,
            fresh_entities=FRESH_ENTITIES,
            user_id="user-new",
            trace_id="trace-001",
        )
        assert result.plan_id != VALID_ULID
        assert len(result.plan_id) == 26

    @pytest.mark.asyncio
    async def test_clone_preserves_graph_structure(self, plan_service):
        """Cloned plan has same step count, roles, tools, and dependencies."""
        result = await plan_service.clone_plan_for_rerun(
            source_plan_id=VALID_ULID,
            fresh_entities=FRESH_ENTITIES,
            user_id="user-new",
            trace_id="trace-001",
        )
        assert len(result.graph) == 2
        assert result.graph[0].role == "Fetcher"
        assert result.graph[0].uses == "google.calendar"
        assert result.graph[0].call == "list_events"
        assert result.graph[1].role == "Booker"
        assert result.graph[1].after == [1]

    @pytest.mark.asyncio
    async def test_clone_applies_fresh_entities(self, plan_service):
        """Cloned plan intent has fresh entities, not original ones."""
        result = await plan_service.clone_plan_for_rerun(
            source_plan_id=VALID_ULID,
            fresh_entities=FRESH_ENTITIES,
            user_id="user-new",
            trace_id="trace-001",
        )
        assert result.intent.entities == FRESH_ENTITIES
        assert result.intent.entities["title"] == "New meeting"

    @pytest.mark.asyncio
    async def test_clone_resets_step_status(self, plan_service):
        """All cloned steps have status=pending, result=None, error=None."""
        result = await plan_service.clone_plan_for_rerun(
            source_plan_id=VALID_ULID,
            fresh_entities=FRESH_ENTITIES,
            user_id="user-new",
            trace_id="trace-001",
        )
        for step in result.graph:
            assert step.status == "pending"
            assert step.result is None
            assert step.error is None

    @pytest.mark.asyncio
    async def test_clone_sets_rerun_source_in_meta(self, plan_service):
        """meta.rerun_source points to the original plan_id."""
        result = await plan_service.clone_plan_for_rerun(
            source_plan_id=VALID_ULID,
            fresh_entities=FRESH_ENTITIES,
            user_id="user-new",
            trace_id="trace-001",
        )
        assert result.meta.rerun_source == VALID_ULID

    @pytest.mark.asyncio
    async def test_clone_recomputes_hash(self, plan_service):
        """Canonical hash differs from original."""
        result = await plan_service.clone_plan_for_rerun(
            source_plan_id=VALID_ULID,
            fresh_entities=FRESH_ENTITIES,
            user_id="user-new",
            trace_id="trace-001",
        )
        assert result.meta.canonical_hash != "a" * 64

    @pytest.mark.asyncio
    async def test_clone_plan_not_found_raises(self, plan_service, mock_db_adapter):
        """Nonexistent source_plan_id raises PlanNotFoundError."""
        mock_db_adapter.get_plan_by_id.return_value = None
        with pytest.raises(PlanNotFoundError):
            await plan_service.clone_plan_for_rerun(
                source_plan_id=VALID_ULID_2,
                fresh_entities=FRESH_ENTITIES,
                user_id="user-new",
                trace_id="trace-001",
            )

    @pytest.mark.asyncio
    async def test_clone_constraints_override(self, plan_service):
        """When constraints_override provided, it replaces original."""
        override = {"scopes": ["mail.send"], "ttl_s": 600, "max_retries": 1}
        result = await plan_service.clone_plan_for_rerun(
            source_plan_id=VALID_ULID,
            fresh_entities=FRESH_ENTITIES,
            user_id="user-new",
            trace_id="trace-001",
            constraints_override=override,
        )
        assert result.constraints.scopes == ["mail.send"]
        assert result.constraints.ttl_s == 600
        # Also check intent constraints are overridden
        assert result.intent.constraints == override

    @pytest.mark.asyncio
    async def test_clone_args_template_substitution(self, plan_service):
        """{{entities.X}} patterns in args are replaced with fresh values."""
        result = await plan_service.clone_plan_for_rerun(
            source_plan_id=VALID_ULID,
            fresh_entities=FRESH_ENTITIES,
            user_id="user-new",
            trace_id="trace-001",
        )
        # Step 1 args had date: "{{entities.date}}"
        assert result.graph[0].args["date"] == "2025-07-15"
        # Step 2 args had title: "{{entities.title}}" and date: "{{entities.date}}"
        assert result.graph[1].args["title"] == "New meeting"
        assert result.graph[1].args["date"] == "2025-07-15"

    @pytest.mark.asyncio
    async def test_clone_preserves_user_id(self, plan_service):
        """Cloned plan uses the new user_id, not the original."""
        result = await plan_service.clone_plan_for_rerun(
            source_plan_id=VALID_ULID,
            fresh_entities=FRESH_ENTITIES,
            user_id="user-new",
            trace_id="trace-001",
        )
        assert result.intent.user_id == "user-new"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
