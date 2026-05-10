"""
Unit tests for PlannerService.build_skeleton() and parse_entity_refs().

Validates skeleton generation from workflow registry (0 LLM calls),
entity reference parsing, DAG level computation, and compound intent handling.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from components.Planner.adapters.workflow_registry import parse_entity_refs
from components.Planner.service.planner_service import PlannerService
from shared.schemas.skeleton import PlanSkeleton

# ---------------------------------------------------------------------------
# parse_entity_refs() tests
# ---------------------------------------------------------------------------


class TestParseEntityRefs:

    def test_extracts_entity_names(self):
        template = {
            "attendees": "{{entities.attendee}}",
            "summary": "{{entities.title}}",
            "duration": "{{entities.duration}}",
        }
        refs = parse_entity_refs(template)
        assert refs == ["attendee", "duration", "title"]

    def test_ignores_step_refs(self):
        template = {
            "start_datetime": "{{step_2.result.recommended_time}}",
            "attendees": "{{entities.attendee}}",
        }
        refs = parse_entity_refs(template)
        assert refs == ["attendee"]

    def test_none_template_returns_empty(self):
        assert parse_entity_refs(None) == []

    def test_empty_template_returns_empty(self):
        assert parse_entity_refs({}) == []

    def test_non_string_values_skipped(self):
        template = {
            "count": 5,
            "name": "{{entities.title}}",
            "flag": True,
        }
        refs = parse_entity_refs(template)
        assert refs == ["title"]

    def test_deduplicates_refs(self):
        template = {
            "to": "{{entities.recipient}}",
            "cc": "{{entities.recipient}}",
        }
        refs = parse_entity_refs(template)
        assert refs == ["recipient"]


# ---------------------------------------------------------------------------
# PlannerService.build_skeleton() tests
# ---------------------------------------------------------------------------


def _make_planner_service():
    """Build a PlannerService with mocked dependencies."""
    return PlannerService(
        context_rag_service=AsyncMock(),
        tool_catalog=MagicMock(
            get_all_tools=MagicMock(return_value=[]),
            get_user_tools=AsyncMock(return_value=[]),
        ),
        plan_service=AsyncMock(),
        llm_adapter=AsyncMock(),
        prompt_builder=MagicMock(),
        validator=MagicMock(),
        primary_breaker=MagicMock(),
        fallback_breaker=MagicMock(),
        primary_model="test-primary",
        fallback_model="test-fallback",
        max_output_tokens=4096,
    )


class TestBuildSkeleton:

    @pytest.mark.asyncio
    async def test_known_intent_returns_registry_skeleton(self):
        """schedule_meeting → 4 steps, 5 entities, intent_source='registry'."""
        svc = _make_planner_service()
        skeleton = await svc.build_skeleton(
            intent_type="schedule_meeting",
            partial_entities={"attendee": "Alice"},
            user_id="550e8400-e29b-41d4-a716-446655440000",
        )
        assert isinstance(skeleton, PlanSkeleton)
        assert skeleton.intent == "schedule_meeting"
        assert skeleton.intent_source == "registry"
        assert len(skeleton.steps) == 4
        assert len(skeleton.entities) == 5

    @pytest.mark.asyncio
    async def test_skeleton_step_roles(self):
        """Steps have correct roles: Fetcher, Reasoner, Resolver, Booker."""
        svc = _make_planner_service()
        skeleton = await svc.build_skeleton(
            intent_type="schedule_meeting",
            partial_entities={},
            user_id="550e8400-e29b-41d4-a716-446655440000",
        )
        roles = [s.role for s in skeleton.steps]
        assert roles == ["Fetcher", "Reasoner", "Resolver", "Booker"]

    @pytest.mark.asyncio
    async def test_dag_levels_sequential(self):
        """Sequential meeting workflow has dag_levels [[1],[2],[3],[4]]."""
        svc = _make_planner_service()
        skeleton = await svc.build_skeleton(
            intent_type="schedule_meeting",
            partial_entities={},
            user_id="550e8400-e29b-41d4-a716-446655440000",
        )
        assert skeleton.dag_levels == [[1], [2], [3], [4]]

    @pytest.mark.asyncio
    async def test_entity_refs_from_args_template(self):
        """Booker step entity_refs populated from args_template (provider-specific)."""
        svc = _make_planner_service()
        skeleton = await svc.build_skeleton(
            intent_type="schedule_meeting_google_calendar",
            partial_entities={},
            user_id="550e8400-e29b-41d4-a716-446655440000",
        )
        booker = next(s for s in skeleton.steps if s.role == "Booker")
        assert "attendee" in booker.entity_refs
        assert "title" in booker.entity_refs
        assert "duration" in booker.entity_refs

    @pytest.mark.asyncio
    async def test_used_by_steps_inverse(self):
        """Entity used_by_steps is inverse of step entity_refs (provider-specific)."""
        svc = _make_planner_service()
        skeleton = await svc.build_skeleton(
            intent_type="schedule_meeting_google_calendar",
            partial_entities={},
            user_id="550e8400-e29b-41d4-a716-446655440000",
        )
        # The 'attendee' entity should be used by the Booker step (step 4)
        attendee_entity = next(e for e in skeleton.entities if e.name == "attendee")
        assert 4 in attendee_entity.used_by_steps

    @pytest.mark.asyncio
    async def test_profile_defaults_queried(self):
        """duration entity gets default_value from preference_service."""
        svc = _make_planner_service()

        mock_evidence = MagicMock()
        mock_evidence.value = 45
        mock_pref = AsyncMock()
        mock_pref.get_preference = AsyncMock(return_value=mock_evidence)

        skeleton = await svc.build_skeleton(
            intent_type="schedule_meeting",
            partial_entities={},
            user_id="550e8400-e29b-41d4-a716-446655440000",
            preference_service=mock_pref,
        )
        duration_entity = next(e for e in skeleton.entities if e.name == "duration")
        assert duration_entity.default_value == 45
        assert duration_entity.default_source == "profile"

    @pytest.mark.asyncio
    async def test_profile_defaults_graceful_failure(self):
        """Preference service failure doesn't break skeleton generation."""
        svc = _make_planner_service()

        mock_pref = AsyncMock()
        mock_pref.get_preference = AsyncMock(side_effect=Exception("DB down"))

        skeleton = await svc.build_skeleton(
            intent_type="schedule_meeting",
            partial_entities={},
            user_id="550e8400-e29b-41d4-a716-446655440000",
            preference_service=mock_pref,
        )
        # Should still return a valid skeleton
        assert skeleton.intent_source == "registry"
        duration_entity = next(e for e in skeleton.entities if e.name == "duration")
        assert duration_entity.default_value is None

    @pytest.mark.asyncio
    async def test_send_email_skeleton(self):
        """send_email → 3 steps, 3 entities."""
        svc = _make_planner_service()
        skeleton = await svc.build_skeleton(
            intent_type="send_email",
            partial_entities={},
            user_id="550e8400-e29b-41d4-a716-446655440000",
        )
        assert skeleton.intent_source == "registry"
        assert len(skeleton.steps) == 3
        entity_names = {e.name for e in skeleton.entities}
        assert "recipient" in entity_names
        assert "subject" in entity_names
        assert "body" in entity_names

    @pytest.mark.asyncio
    async def test_compound_intent_merges_workflows(self):
        """Compound intent via sub_intents merges steps from multiple workflows."""
        svc = _make_planner_service()
        skeleton = await svc.build_skeleton(
            intent_type="schedule_meeting_and_send_email",
            partial_entities={},
            user_id="550e8400-e29b-41d4-a716-446655440000",
            sub_intents=["schedule_meeting", "send_email"],
        )
        assert skeleton.intent_source == "registry"
        # 4 meeting steps + 3 email steps = 7 total
        assert len(skeleton.steps) == 7

    @pytest.mark.asyncio
    async def test_compound_intent_entities_deduplicated(self):
        """Compound intent deduplicates entities across workflows."""
        svc = _make_planner_service()
        skeleton = await svc.build_skeleton(
            intent_type="schedule_meeting_and_send_email",
            partial_entities={},
            user_id="550e8400-e29b-41d4-a716-446655440000",
            sub_intents=["schedule_meeting", "send_email"],
        )
        entity_names = [e.name for e in skeleton.entities]
        # Each entity name should appear only once
        assert len(entity_names) == len(set(entity_names))

    @pytest.mark.asyncio
    async def test_step_descriptions_populated(self):
        """Every skeleton step has a non-empty description."""
        svc = _make_planner_service()
        skeleton = await svc.build_skeleton(
            intent_type="schedule_meeting",
            partial_entities={},
            user_id="550e8400-e29b-41d4-a716-446655440000",
        )
        for step in skeleton.steps:
            assert step.description, f"Step {step.step} has no description"

    @pytest.mark.asyncio
    async def test_gate_ids_preserved(self):
        """Gate IDs from workflow registry are preserved in skeleton."""
        svc = _make_planner_service()
        skeleton = await svc.build_skeleton(
            intent_type="schedule_meeting",
            partial_entities={},
            user_id="550e8400-e29b-41d4-a716-446655440000",
        )
        resolver = next(s for s in skeleton.steps if s.role == "Resolver")
        assert resolver.gate_id == "gate-confirm"
        booker = next(s for s in skeleton.steps if s.role == "Booker")
        assert booker.gate_id == "gate-execute"


# ---------------------------------------------------------------------------
# _compute_dag_levels() tests
# ---------------------------------------------------------------------------


class TestComputeDAGLevels:

    def test_linear_chain(self):
        """Steps 1→2→3→4 produce [[1],[2],[3],[4]]."""
        from shared.schemas.skeleton import SkeletonStep

        steps = [
            SkeletonStep(step=1, role="A", after=[]),
            SkeletonStep(step=2, role="B", after=[1]),
            SkeletonStep(step=3, role="C", after=[2]),
            SkeletonStep(step=4, role="D", after=[3]),
        ]
        levels = PlannerService._compute_dag_levels(steps)
        assert levels == [[1], [2], [3], [4]]

    def test_parallel_fork(self):
        """1 → (2,3) → 4 produces [[1],[2,3],[4]]."""
        from shared.schemas.skeleton import SkeletonStep

        steps = [
            SkeletonStep(step=1, role="A", after=[]),
            SkeletonStep(step=2, role="B", after=[1]),
            SkeletonStep(step=3, role="C", after=[1]),
            SkeletonStep(step=4, role="D", after=[2, 3]),
        ]
        levels = PlannerService._compute_dag_levels(steps)
        assert levels == [[1], [2, 3], [4]]

    def test_single_step(self):
        """Single step produces [[1]]."""
        from shared.schemas.skeleton import SkeletonStep

        steps = [SkeletonStep(step=1, role="A", after=[])]
        levels = PlannerService._compute_dag_levels(steps)
        assert levels == [[1]]

    def test_empty_steps(self):
        """No steps produce empty levels."""
        levels = PlannerService._compute_dag_levels([])
        assert levels == []
