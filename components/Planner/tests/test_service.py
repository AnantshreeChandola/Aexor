"""
Planner service integration tests — PlannerService.generate_plan() with mocked deps.

Covers: happy path, determinism, fallback hierarchy, edge cases.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from components.Planner.adapters.circuit_breaker import CircuitBreaker
from components.Planner.adapters.plan_validator import PlanValidator
from components.Planner.adapters.prompt_builder import PromptBuilder
from components.Planner.domain.models import (
    LLMCallError,
    PlannerResult,
    ToolNotAvailableError,
)
from components.Planner.service.planner_service import PlannerService
from shared.schemas.intent import Intent
from shared.schemas.plan import Plan

from .conftest import SAMPLE_INTENT, SAMPLE_USER_ID, SAMPLE_VALID_PLAN_JSON

# ===========================
# T600: Happy Path Tests
# ===========================


class TestGeneratePlanHappyPath:
    @pytest.mark.asyncio
    async def test_generate_plan_happy_path(self, planner_service, sample_intent):
        result = await planner_service.generate_plan(sample_intent)
        assert isinstance(result, PlannerResult)
        assert isinstance(result.plan, Plan)
        assert result.fallback_level == 1
        assert result.generation_duration_ms >= 0

    @pytest.mark.asyncio
    async def test_generate_plan_deterministic_hash(self, planner_service, sample_intent):
        r1 = await planner_service.generate_plan(sample_intent)
        r2 = await planner_service.generate_plan(sample_intent)
        assert r1.plan.meta.canonical_hash == r2.plan.meta.canonical_hash

    @pytest.mark.asyncio
    async def test_generate_plan_plan_id_is_ulid(self, planner_service, sample_intent):
        result = await planner_service.generate_plan(sample_intent)
        assert len(result.plan.plan_id) == 26

    @pytest.mark.asyncio
    async def test_generate_plan_plugins_populated(self, planner_service, sample_intent):
        result = await planner_service.generate_plan(sample_intent)
        assert len(result.plan.plugins) > 0
        # All tool_ids from graph should be in plugins
        graph_tools = {s.uses for s in result.plan.graph}
        assert graph_tools == set(result.plan.plugins)

    @pytest.mark.asyncio
    async def test_generate_plan_dry_run_enforced(self, planner_service, sample_intent):
        result = await planner_service.generate_plan(sample_intent)
        for step in result.plan.graph:
            assert step.dry_run is True

    @pytest.mark.asyncio
    async def test_generate_plan_context_degraded_flag(
        self,
        mock_degraded_context_rag_service,
        mock_tool_catalog,
        mock_plan_service,
        mock_llm_adapter,
    ):
        svc = PlannerService(
            context_rag_service=mock_degraded_context_rag_service,
            tool_catalog=mock_tool_catalog,
            plan_service=mock_plan_service,
            llm_adapter=mock_llm_adapter,
            prompt_builder=PromptBuilder(),
            validator=PlanValidator(),
            primary_breaker=CircuitBreaker(model_name="p"),
            fallback_breaker=CircuitBreaker(model_name="f"),
            primary_model="test-primary",
            fallback_model="test-fallback",
            max_output_tokens=4096,
        )
        result = await svc.generate_plan(SAMPLE_INTENT)
        assert result.context_degraded is True

    @pytest.mark.asyncio
    async def test_generate_plan_registry_version_in_result(
        self,
        planner_service,
        sample_intent,
    ):
        result = await planner_service.generate_plan(sample_intent)
        assert result.registry_version == 0  # ToolCatalog has no versioning


# ===========================
# T601: Fallback Hierarchy Tests
# ===========================


class TestFallbackHierarchy:
    def _make_service(
        self,
        llm_adapter,
        context_rag,
        registry,
        plan_service,
    ):
        return PlannerService(
            context_rag_service=context_rag,
            tool_catalog=registry,
            plan_service=plan_service,
            llm_adapter=llm_adapter,
            prompt_builder=PromptBuilder(),
            validator=PlanValidator(),
            primary_breaker=CircuitBreaker(model_name="p", failure_threshold=1),
            fallback_breaker=CircuitBreaker(model_name="f", failure_threshold=1),
            primary_model="test-primary",
            fallback_model="test-fallback",
            max_output_tokens=4096,
        )

    @pytest.mark.asyncio
    async def test_fallback_level_2_on_primary_failure(
        self,
        mock_context_rag_service,
        mock_tool_catalog,
        mock_plan_service,
    ):
        """Primary fails, fallback succeeds -> level 2."""
        call_count = 0

        async def side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise LLMCallError("primary", "simulated failure")
            return SAMPLE_VALID_PLAN_JSON

        adapter = AsyncMock()
        adapter.generate = AsyncMock(side_effect=side_effect)

        svc = self._make_service(
            adapter,
            mock_context_rag_service,
            mock_tool_catalog,
            mock_plan_service,
        )
        result = await svc.generate_plan(SAMPLE_INTENT)
        assert result.fallback_level == 2

    @pytest.mark.asyncio
    async def test_fallback_level_3_on_both_llms_fail(
        self,
        mock_failing_llm_adapter,
        mock_context_rag_service,
        mock_tool_catalog,
        mock_plan_service,
    ):
        """Both LLMs fail -> PlanLibrary template -> level 3."""
        svc = self._make_service(
            mock_failing_llm_adapter,
            mock_context_rag_service,
            mock_tool_catalog,
            mock_plan_service,
        )
        result = await svc.generate_plan(SAMPLE_INTENT)
        assert result.fallback_level == 3

    @pytest.mark.asyncio
    async def test_fallback_level_4_minimal_plan(
        self,
        mock_failing_llm_adapter,
        mock_context_rag_service,
        mock_tool_catalog,
        mock_empty_plan_service,
    ):
        """Both LLMs fail + no templates -> level 4 minimal plan."""
        svc = self._make_service(
            mock_failing_llm_adapter,
            mock_context_rag_service,
            mock_tool_catalog,
            mock_empty_plan_service,
        )
        result = await svc.generate_plan(SAMPLE_INTENT)
        assert result.fallback_level == 4
        assert result.plan.graph[0].uses == "system.echo"

    @pytest.mark.asyncio
    async def test_fallback_level_indicator(
        self,
        mock_context_rag_service,
        mock_tool_catalog,
        mock_plan_service,
        mock_llm_adapter,
    ):
        """Level 1 when primary succeeds."""
        svc = self._make_service(
            mock_llm_adapter,
            mock_context_rag_service,
            mock_tool_catalog,
            mock_plan_service,
        )
        result = await svc.generate_plan(SAMPLE_INTENT)
        assert result.fallback_level == 1

    @pytest.mark.asyncio
    async def test_minimal_plan_structure(
        self,
        mock_failing_llm_adapter,
        mock_context_rag_service,
        mock_tool_catalog,
        mock_empty_plan_service,
    ):
        """Minimal plan has 1 Fetcher step with system.echo and dry_run=True."""
        svc = self._make_service(
            mock_failing_llm_adapter,
            mock_context_rag_service,
            mock_tool_catalog,
            mock_empty_plan_service,
        )
        result = await svc.generate_plan(SAMPLE_INTENT)
        assert len(result.plan.graph) == 1
        step = result.plan.graph[0]
        assert step.role == "Fetcher"
        assert step.uses == "system.echo"
        assert step.call == "echo"
        assert step.dry_run is True

    @pytest.mark.asyncio
    async def test_validation_failure_triggers_fallback(
        self,
        mock_context_rag_service,
        mock_tool_catalog,
        mock_plan_service,
    ):
        """LLM returns invalid plan -> falls to next level."""
        adapter = AsyncMock()
        # Returns invalid JSON that parses but fails schema
        adapter.generate = AsyncMock(return_value='{"invalid": "plan"}')

        svc = self._make_service(
            adapter,
            mock_context_rag_service,
            mock_tool_catalog,
            mock_plan_service,
        )
        result = await svc.generate_plan(SAMPLE_INTENT)
        # Should fall through LLM levels to template (level 3) or minimal (level 4)
        assert result.fallback_level >= 3


# ===========================
# T602: Edge Cases and Concurrent Safety
# ===========================


class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_entities_still_generates_plan(
        self,
        planner_service,
    ):
        from shared.schemas.intent import Intent

        intent = Intent(
            intent="schedule_meeting",
            entities={},
            constraints={},
            user_id="test-user",
        )
        result = await planner_service.generate_plan(intent)
        assert isinstance(result, PlannerResult)

    @pytest.mark.asyncio
    async def test_empty_evidence_context_degraded(
        self,
        mock_degraded_context_rag_service,
        mock_tool_catalog,
        mock_plan_service,
        mock_llm_adapter,
    ):
        svc = PlannerService(
            context_rag_service=mock_degraded_context_rag_service,
            tool_catalog=mock_tool_catalog,
            plan_service=mock_plan_service,
            llm_adapter=mock_llm_adapter,
            prompt_builder=PromptBuilder(),
            validator=PlanValidator(),
            primary_breaker=CircuitBreaker(model_name="p"),
            fallback_breaker=CircuitBreaker(model_name="f"),
            primary_model="test-primary",
            fallback_model="test-fallback",
            max_output_tokens=4096,
        )
        result = await svc.generate_plan(SAMPLE_INTENT)
        assert result.context_degraded is True

    @pytest.mark.asyncio
    async def test_empty_catalog_fallback_to_minimal(
        self,
        mock_context_rag_service,
        mock_empty_tool_catalog,
        mock_empty_plan_service,
        mock_failing_llm_adapter,
    ):
        """Empty tool catalog + LLM fails -> minimal plan."""
        svc = PlannerService(
            context_rag_service=mock_context_rag_service,
            tool_catalog=mock_empty_tool_catalog,
            plan_service=mock_empty_plan_service,
            llm_adapter=mock_failing_llm_adapter,
            prompt_builder=PromptBuilder(),
            validator=PlanValidator(),
            primary_breaker=CircuitBreaker(model_name="p", failure_threshold=1),
            fallback_breaker=CircuitBreaker(model_name="f", failure_threshold=1),
            primary_model="test-primary",
            fallback_model="test-fallback",
            max_output_tokens=4096,
        )
        result = await svc.generate_plan(SAMPLE_INTENT)
        assert result.fallback_level == 4

    @pytest.mark.asyncio
    async def test_concurrent_calls_safe(self, planner_service, sample_intent):
        """5 concurrent generate_plan calls should all succeed."""
        results = await asyncio.gather(
            *[planner_service.generate_plan(sample_intent) for _ in range(5)]
        )
        assert len(results) == 5
        for r in results:
            assert isinstance(r, PlannerResult)


# ===========================
# T603: get_required_entities — registry-down path
# ===========================


class TestGetRequiredEntitiesCatalogDown:
    """Verify that get_required_entities raises ToolNotAvailableError
    when the tool catalog is unreachable and the LLM suggests tools."""

    def _make_service(self, *, llm_response: str, tool_catalog):
        adapter = AsyncMock()
        adapter.generate = AsyncMock(return_value=llm_response)
        return PlannerService(
            context_rag_service=AsyncMock(),
            tool_catalog=tool_catalog,
            plan_service=AsyncMock(),
            llm_adapter=adapter,
            prompt_builder=PromptBuilder(),
            validator=PlanValidator(),
            primary_breaker=CircuitBreaker(model_name="p"),
            fallback_breaker=CircuitBreaker(model_name="f"),
            primary_model="test-primary",
            fallback_model="test-fallback",
            max_output_tokens=4096,
        )

    @pytest.mark.asyncio
    async def test_catalog_down_with_tools_raises(self):
        """Catalog throws + LLM suggests tools -> ToolNotAvailableError.

        Uses a non-static-map intent so the LLM path is exercised.
        """
        import json

        llm_response = json.dumps(
            {
                "tools_needed": ["google.calendar"],
                "entities": [{"name": "attendee", "description": "Who?", "required": True}],
            }
        )
        catalog = MagicMock()
        catalog.get_all_tools = MagicMock(side_effect=ConnectionError("unavailable"))

        svc = self._make_service(llm_response=llm_response, tool_catalog=catalog)

        with pytest.raises(ToolNotAvailableError) as exc_info:
            await svc.get_required_entities("reschedule_meeting")

        assert exc_info.value.intent_type == "reschedule_meeting"
        assert "google.calendar" in exc_info.value.required_tools

    @pytest.mark.asyncio
    async def test_catalog_down_no_tools_returns_normally(self):
        """Catalog throws + LLM suggests no tools -> returns normally."""
        import json

        llm_response = json.dumps(
            {
                "tools_needed": [],
                "entities": [{"name": "query", "description": "Search term", "required": True}],
            }
        )
        catalog = MagicMock()
        catalog.get_all_tools = MagicMock(side_effect=ConnectionError("unavailable"))

        svc = self._make_service(llm_response=llm_response, tool_catalog=catalog)

        result = await svc.get_required_entities("general_search")
        assert result.intent_type == "general_search"
        assert result.resolved_tools == []

    @pytest.mark.asyncio
    async def test_catalog_empty_with_tools_raises(self, mock_empty_tool_catalog):
        """Catalog available but empty + LLM suggests tools -> ToolNotAvailableError.

        Uses a non-static-map intent so the LLM path is exercised.
        """
        import json

        llm_response = json.dumps(
            {
                "tools_needed": ["google.calendar"],
                "entities": [{"name": "attendee", "description": "Who?", "required": True}],
            }
        )

        svc = self._make_service(llm_response=llm_response, tool_catalog=mock_empty_tool_catalog)

        with pytest.raises(ToolNotAvailableError) as exc_info:
            await svc.get_required_entities("reschedule_meeting")

        assert exc_info.value.intent_type == "reschedule_meeting"
        assert "google.calendar" in exc_info.value.required_tools


# ===========================
# T604: get_required_entities — provider-level matching
# ===========================


class TestGetRequiredEntitiesProviderMatching:
    """Verify that provider-level fuzzy matching resolves LLM-suggested
    tool names (e.g. 'google.calendar') to actual Composio-style catalog
    tool names (e.g. 'GOOGLECALENDAR_CREATE_EVENT')."""

    @staticmethod
    def _make_composio_catalog():
        """Catalog with Composio-style tool names."""
        from shared.mcp.catalog import ToolDefinition, _extract_provider_name

        tools = [
            ToolDefinition(
                name="GOOGLECALENDAR_CREATE_EVENT",
                server_name="composio",
                provider_name=_extract_provider_name("GOOGLECALENDAR_CREATE_EVENT"),
                description="Create a Google Calendar event",
            ),
            ToolDefinition(
                name="GOOGLECALENDAR_LIST_EVENTS",
                server_name="composio",
                provider_name=_extract_provider_name("GOOGLECALENDAR_LIST_EVENTS"),
                description="List Google Calendar events",
            ),
            ToolDefinition(
                name="SLACK_SEND_MESSAGE",
                server_name="composio",
                provider_name=_extract_provider_name("SLACK_SEND_MESSAGE"),
                description="Send a Slack message",
            ),
            ToolDefinition(
                name="GMAIL_SEND_EMAIL",
                server_name="composio",
                provider_name=_extract_provider_name("GMAIL_SEND_EMAIL"),
                description="Send an email via Gmail",
            ),
        ]
        catalog = MagicMock()
        catalog.get_all_tools = MagicMock(return_value=tools)
        catalog.get_tool = MagicMock(
            side_effect=lambda name: next((t for t in tools if t.name == name), None)
        )
        return catalog

    def _make_service(self, *, llm_response: str, tool_catalog):
        adapter = AsyncMock()
        adapter.generate = AsyncMock(return_value=llm_response)
        return PlannerService(
            context_rag_service=AsyncMock(),
            tool_catalog=tool_catalog,
            plan_service=AsyncMock(),
            llm_adapter=adapter,
            prompt_builder=PromptBuilder(),
            validator=PlanValidator(),
            primary_breaker=CircuitBreaker(model_name="p"),
            fallback_breaker=CircuitBreaker(model_name="f"),
            primary_model="test-primary",
            fallback_model="test-fallback",
            max_output_tokens=4096,
        )

    @pytest.mark.asyncio
    async def test_provider_match_google_calendar(self):
        """LLM suggests 'google.calendar', catalog has GOOGLECALENDAR_* tools."""
        import json

        llm_response = json.dumps(
            {
                "tools_needed": ["google.calendar"],
                "entities": [
                    {"name": "attendee", "description": "Who to invite", "required": True},
                ],
            }
        )
        catalog = self._make_composio_catalog()
        svc = self._make_service(llm_response=llm_response, tool_catalog=catalog)

        # Use a non-registered intent to force LLM path
        result = await svc.get_required_entities("book_appointment")

        assert result.intent_type == "book_appointment"
        assert len(result.resolved_tools) >= 1
        # Resolved tool should be a Composio-style name, not the LLM suggestion
        assert any("GOOGLECALENDAR" in t for t in result.resolved_tools)

    @pytest.mark.asyncio
    async def test_provider_match_slack(self):
        """LLM suggests 'slack.messaging', catalog has SLACK_SEND_MESSAGE."""
        import json

        llm_response = json.dumps(
            {
                "tools_needed": ["slack.messaging"],
                "entities": [
                    {"name": "channel", "description": "Channel to post in", "required": True},
                ],
            }
        )
        catalog = self._make_composio_catalog()
        svc = self._make_service(llm_response=llm_response, tool_catalog=catalog)

        result = await svc.get_required_entities("send_message")

        assert len(result.resolved_tools) >= 1
        assert any("SLACK" in t for t in result.resolved_tools)

    @pytest.mark.asyncio
    async def test_provider_match_multiple_tools(self):
        """LLM suggests multiple providers, all resolved via provider matching."""
        import json

        llm_response = json.dumps(
            {
                "tools_needed": ["google.calendar", "slack.messaging"],
                "entities": [
                    {"name": "attendee", "description": "Who", "required": True},
                ],
            }
        )
        catalog = self._make_composio_catalog()
        svc = self._make_service(llm_response=llm_response, tool_catalog=catalog)

        result = await svc.get_required_entities("coordinate_event")

        assert len(result.resolved_tools) >= 2
        resolved_str = " ".join(result.resolved_tools)
        assert "GOOGLECALENDAR" in resolved_str
        assert "SLACK" in resolved_str

    @pytest.mark.asyncio
    async def test_no_provider_match_raises(self):
        """LLM suggests 'jira.issues' but catalog has no Jira tools."""
        import json

        llm_response = json.dumps(
            {
                "tools_needed": ["jira.issues"],
                "entities": [
                    {"name": "issue_key", "description": "Jira issue key", "required": True},
                ],
            }
        )
        catalog = self._make_composio_catalog()
        svc = self._make_service(llm_response=llm_response, tool_catalog=catalog)

        with pytest.raises(ToolNotAvailableError) as exc_info:
            await svc.get_required_entities("track_issue")

        assert "jira.issues" in exc_info.value.required_tools

    @pytest.mark.asyncio
    async def test_partial_provider_match(self):
        """One tool resolves, one doesn't — should still succeed."""
        import json

        llm_response = json.dumps(
            {
                "tools_needed": ["google.calendar", "jira.issues"],
                "entities": [],
            }
        )
        catalog = self._make_composio_catalog()
        svc = self._make_service(llm_response=llm_response, tool_catalog=catalog)

        result = await svc.get_required_entities("schedule_and_track")

        assert len(result.resolved_tools) >= 1
        assert any("GOOGLECALENDAR" in t for t in result.resolved_tools)

    @pytest.mark.asyncio
    async def test_exact_match_takes_priority(self):
        """If a tool name exactly matches the catalog, use it directly."""
        import json

        llm_response = json.dumps(
            {
                "tools_needed": ["SLACK_SEND_MESSAGE"],
                "entities": [],
            }
        )
        catalog = self._make_composio_catalog()
        svc = self._make_service(llm_response=llm_response, tool_catalog=catalog)

        result = await svc.get_required_entities("send_slack_message")

        assert "SLACK_SEND_MESSAGE" in result.resolved_tools


# ===========================
# T605: Deterministic override for collected entities
# ===========================


class TestDeterministicEntityOverride:
    """Verify that entities whose name exactly matches a key in
    collected_entities are never marked as missing, regardless of
    what the LLM returns.

    Uses ``reschedule_meeting`` (not in the static entity map) so
    these tests exercise the LLM path's Step 2b override logic.
    """

    def _make_service(self, *, llm_response: str, tool_catalog):
        adapter = AsyncMock()
        adapter.generate = AsyncMock(return_value=llm_response)
        return PlannerService(
            context_rag_service=AsyncMock(),
            tool_catalog=tool_catalog,
            plan_service=AsyncMock(),
            llm_adapter=adapter,
            prompt_builder=PromptBuilder(),
            validator=PlanValidator(),
            primary_breaker=CircuitBreaker(model_name="p"),
            fallback_breaker=CircuitBreaker(model_name="f"),
            primary_model="test-primary",
            fallback_model="test-fallback",
            max_output_tokens=4096,
        )

    @pytest.mark.asyncio
    async def test_collected_entity_not_in_missing(self, mock_tool_catalog):
        """LLM marks attendee_email as missing, but it's in collected -> not missing."""
        import json

        llm_response = json.dumps(
            {
                "tools_needed": ["google.calendar"],
                "entities": [
                    {
                        "name": "attendee_email",
                        "description": "Attendee email",
                        "required": True,
                        "missing": True,  # LLM incorrectly says missing
                    },
                    {
                        "name": "duration",
                        "description": "How long?",
                        "required": True,
                        "missing": True,  # Genuinely missing
                    },
                ],
            }
        )
        svc = self._make_service(llm_response=llm_response, tool_catalog=mock_tool_catalog)

        result = await svc.get_required_entities(
            "reschedule_meeting",
            collected_entities={"attendee_email": "alice@example.com", "attendee": "Alice"},
        )

        missing_names = [e.name for e in result.missing_entities]
        assert "attendee_email" not in missing_names
        assert "duration" in missing_names

    @pytest.mark.asyncio
    async def test_all_collected_none_missing(self, mock_tool_catalog):
        """All entities are collected -> missing list is empty."""
        import json

        llm_response = json.dumps(
            {
                "tools_needed": ["google.calendar"],
                "entities": [
                    {
                        "name": "attendee",
                        "description": "Who?",
                        "required": True,
                        "missing": True,
                    },
                    {
                        "name": "time",
                        "description": "When?",
                        "required": True,
                        "missing": True,
                    },
                ],
            }
        )
        svc = self._make_service(llm_response=llm_response, tool_catalog=mock_tool_catalog)

        result = await svc.get_required_entities(
            "reschedule_meeting",
            collected_entities={"attendee": "Alice", "time": "3 PM"},
        )

        assert len(result.missing_entities) == 0

    @pytest.mark.asyncio
    async def test_uncollected_entity_stays_missing(self, mock_tool_catalog):
        """Entities NOT in collected stay missing when LLM says so."""
        import json

        llm_response = json.dumps(
            {
                "tools_needed": ["google.calendar"],
                "entities": [
                    {
                        "name": "attendee",
                        "description": "Who?",
                        "required": True,
                        "missing": False,
                    },
                    {
                        "name": "duration",
                        "description": "How long?",
                        "required": True,
                        "missing": True,
                    },
                ],
            }
        )
        svc = self._make_service(llm_response=llm_response, tool_catalog=mock_tool_catalog)

        result = await svc.get_required_entities(
            "reschedule_meeting",
            collected_entities={"attendee": "Alice"},
        )

        missing_names = [e.name for e in result.missing_entities]
        assert "attendee" not in missing_names
        assert "duration" in missing_names


# ===========================
# T606: get_required_entities — new registry intents
# ===========================


class TestGetRequiredEntitiesRegistryIntents:
    """Verify that all 26 workflow registry intents use the static fast path."""

    @pytest.mark.asyncio
    async def test_draft_email_static_path(self, planner_service):
        result = await planner_service.get_required_entities("draft_email_gmail")
        assert result.intent_type == "draft_email_gmail"
        assert len(result.resolved_tools) >= 1

    @pytest.mark.asyncio
    async def test_list_email_static_path(self, planner_service):
        result = await planner_service.get_required_entities("list_email")
        assert result.intent_type == "list_email"

    @pytest.mark.asyncio
    async def test_search_email_static_path(self, planner_service):
        result = await planner_service.get_required_entities("search_email_gmail")
        assert result.intent_type == "search_email_gmail"
        required_names = [e.name for e in result.required_entities]
        assert "query" in required_names

    @pytest.mark.asyncio
    async def test_list_meetings_static_path(self, planner_service):
        result = await planner_service.get_required_entities("list_meetings")
        assert result.intent_type == "list_meetings"

    @pytest.mark.asyncio
    async def test_check_calendar_static_path(self, planner_service):
        result = await planner_service.get_required_entities("check_calendar_google_calendar")
        assert result.intent_type == "check_calendar_google_calendar"

    @pytest.mark.asyncio
    async def test_alias_matching_for_send_email(self, planner_service):
        """Entity collected via alias should not show as missing."""
        result = await planner_service.get_required_entities(
            "send_email",
            collected_entities={"to": "alice@x.com", "email_subject": "Hi", "content": "Body"},
        )
        assert len(result.missing_entities) == 0

    # --- Provider-specific intents ---

    @pytest.mark.asyncio
    async def test_create_document_static_path(self, planner_service):
        result = await planner_service.get_required_entities("create_document_google_docs")
        assert result.intent_type == "create_document_google_docs"
        required_names = [e.name for e in result.required_entities]
        assert "title" in required_names
        assert "content" in required_names

    @pytest.mark.asyncio
    async def test_search_files_static_path(self, planner_service):
        result = await planner_service.get_required_entities("search_files")
        assert result.intent_type == "search_files"
        required_names = [e.name for e in result.required_entities]
        assert "query" in required_names

    @pytest.mark.asyncio
    async def test_create_page_static_path(self, planner_service):
        result = await planner_service.get_required_entities("create_page_notion")
        assert result.intent_type == "create_page_notion"
        required_names = [e.name for e in result.required_entities]
        assert "title" in required_names

    @pytest.mark.asyncio
    async def test_create_issue_static_path(self, planner_service):
        result = await planner_service.get_required_entities("create_issue_github")
        assert result.intent_type == "create_issue_github"
        required_names = [e.name for e in result.required_entities]
        assert "title" in required_names
        assert "repo" in required_names

    @pytest.mark.asyncio
    async def test_send_message_static_path(self, planner_service):
        result = await planner_service.get_required_entities("send_message_slack")
        assert result.intent_type == "send_message_slack"
        required_names = [e.name for e in result.required_entities]
        assert "channel" in required_names
        assert "message" in required_names

    @pytest.mark.asyncio
    async def test_list_prs_static_path(self, planner_service):
        result = await planner_service.get_required_entities("list_prs_github")
        assert result.intent_type == "list_prs_github"
        required_names = [e.name for e in result.required_entities]
        assert "repo" in required_names


# ===========================
# T607: get_required_entities — compound intents
# ===========================


class TestGetRequiredEntitiesCompound:
    """Verify compound intent entity merge with no LLM call."""

    @pytest.mark.asyncio
    async def test_compound_sub_intents_merge_entities(self, planner_service):
        """sub_intents provided → entities merged from both workflows, no LLM call."""
        result = await planner_service.get_required_entities(
            "schedule_meeting_and_email",
            sub_intents=["schedule_meeting", "send_email"],
        )
        assert result.intent_type == "schedule_meeting_and_email"
        entity_names = [e.name for e in result.required_entities]
        # Should have entities from both workflows
        assert "attendee" in entity_names
        assert "recipient" in entity_names
        assert "subject" in entity_names

    @pytest.mark.asyncio
    async def test_compound_resolved_tools_combined(self, planner_service):
        """Resolved tools include tools from all provider-specific sub-workflows."""
        result = await planner_service.get_required_entities(
            "schedule_meeting_and_email",
            sub_intents=["schedule_meeting_google_calendar", "send_email_gmail"],
        )
        tools_str = " ".join(result.resolved_tools)
        assert "GOOGLECALENDAR" in tools_str or "GMAIL" in tools_str

    @pytest.mark.asyncio
    async def test_compound_with_collected_reduces_missing(self, planner_service):
        """Collected entities reduce the missing list for compound intents."""
        result = await planner_service.get_required_entities(
            "schedule_meeting_and_email",
            collected_entities={"attendee": "Alice", "recipient": "alice@x.com"},
            sub_intents=["schedule_meeting", "send_email"],
        )
        missing_names = [e.name for e in result.missing_entities]
        assert "attendee" not in missing_names
        assert "recipient" not in missing_names


# ===========================
# T610: Generic Intent + Tool Override Tests
# ===========================


class TestGenericIntentAndToolOverrides:
    """Tests for generic intents and tool_overrides flow."""

    def test_generic_intent_produces_empty_tool_steps(self):
        """Generic intent 'list_meetings' has steps with tool=''."""
        from components.Planner.adapters.workflow_registry import (
            decompose_intent,
        )

        workflows = decompose_intent("list_meetings")
        assert workflows is not None
        assert len(workflows) == 1
        wf = workflows[0]
        assert wf.provider == "generic"
        # The Fetcher step should have tool=""
        api_steps = [s for s in wf.steps if s.type == "api" and s.role == "Fetcher"]
        assert len(api_steps) >= 1
        assert api_steps[0].tool == ""

    def test_deterministic_planner_rejects_tool_overrides(self):
        """can_handle() returns False when intent.tool_overrides is non-empty."""
        from components.Planner.adapters.deterministic_planner import (
            DeterministicPlanner,
        )

        planner = DeterministicPlanner()
        intent_with_overrides = Intent(
            intent="list_meetings_google_calendar",
            entities={},
            constraints={},
            user_id=SAMPLE_USER_ID,
            tool_overrides={1: "NOTION_SEARCH_NOTION"},
        )
        assert planner.can_handle(intent_with_overrides) is False

    def test_deterministic_planner_rejects_generic_intent(self):
        """DeterministicPlanner._validate_tools returns False for generic workflows (empty tool)."""
        from components.Planner.adapters.deterministic_planner import (
            DeterministicPlanner,
        )
        from shared.mcp.catalog import ToolDefinition

        planner = DeterministicPlanner()
        intent = Intent(
            intent="list_meetings",
            entities={"date_range": "this week"},
            constraints={},
            user_id=SAMPLE_USER_ID,
        )
        tools = [
            ToolDefinition(
                name="GOOGLECALENDAR_LIST_EVENTS",
                server_name="composio",
                provider_name="googlecalendar",
                description="List events",
                input_schema={},
            ),
        ]
        # The deterministic planner should return None for generic intents
        # because _validate_tools rejects empty tool names
        result = planner.build_plan(intent, tools)
        assert result is None

    @pytest.mark.asyncio
    async def test_skeleton_has_available_tools_field(self, planner_service):
        """build_skeleton for generic intent returns steps with empty tools."""
        skeleton = await planner_service.build_skeleton(
            intent_type="list_meetings",
            partial_entities={},
            user_id=SAMPLE_USER_ID,
        )
        api_steps = [s for s in skeleton.steps if s.type == "api" and s.role == "Fetcher"]
        assert len(api_steps) >= 1
        assert api_steps[0].tool == ""
        # available_tools is empty until orchestrate_routes populates it
        assert isinstance(api_steps[0].available_tools, list)

    def test_provider_specific_intent_still_works(self):
        """Provider-specific intents (e.g. list_meetings_google_calendar) still resolve."""
        from components.Planner.adapters.workflow_registry import (
            get_workflow,
            has_workflow,
        )

        assert has_workflow("list_meetings_google_calendar") is True
        wf = get_workflow("list_meetings_google_calendar")
        assert wf is not None
        assert wf.provider == "googlecalendar"
        # Should have actual tool names
        api_steps = [s for s in wf.steps if s.type == "api" and s.role == "Fetcher"]
        assert api_steps[0].tool != ""

    def test_entity_map_includes_both_generic_and_specific(self):
        """get_entity_map() includes both generic and provider-specific intents."""
        from components.Planner.adapters.workflow_registry import get_entity_map

        entity_map = get_entity_map()
        # Generic
        assert "list_meetings" in entity_map
        assert "send_email" in entity_map
        # Provider-specific
        assert "list_meetings_google_calendar" in entity_map
        assert "send_email_gmail" in entity_map

    def test_tool_overrides_applied_in_skeleton_plan(self, planner_service):
        """_build_plan_from_skeleton uses override tools."""
        import time

        cached = {
            "steps": [
                {"step": 1, "role": "Fetcher", "type": "api", "tool": "", "after": []},
                {"step": 2, "role": "Reasoner", "type": "llm_reasoning", "tool": "summarizer", "after": [1]},
            ],
            "timestamp": time.monotonic(),
        }
        intent = Intent(
            intent="list_meetings",
            entities={"date_range": "this week"},
            constraints={},
            user_id=SAMPLE_USER_ID,
            tool_overrides={1: "NOTION_SEARCH_NOTION"},
        )
        from shared.mcp.catalog import ToolDefinition

        tools = [
            ToolDefinition(
                name="NOTION_SEARCH_NOTION",
                server_name="composio",
                provider_name="notion",
                description="Search Notion",
                input_schema={},
            ),
        ]
        plan = planner_service._build_plan_from_skeleton(cached, intent, tools)
        assert plan is not None
        # Step 1 should use the override tool
        assert plan.graph[0].uses == "NOTION_SEARCH_NOTION"
