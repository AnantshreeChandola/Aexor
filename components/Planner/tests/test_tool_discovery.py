"""
Tool Discovery unit tests — covers all 3 tiers + graceful degradation.

Tests use mocked adapters (no real ONNX model or database).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from components.Planner.adapters.tool_discovery import ToolDiscoveryService
from components.Planner.adapters.tool_embedding_adapter import ToolEmbeddingAdapter
from components.Planner.domain.tool_discovery_models import (
    NoToolsConnectedError,
    ToolDiscoveryResult,
    ToolEmbeddingResult,
    ToolNotConnectedError,
)
from shared.mcp.catalog import ToolDefinition

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tool(name: str, provider: str = "", description: str = "", schema: dict | None = None) -> ToolDefinition:
    """Create a ToolDefinition for tests."""
    if not provider:
        parts = name.split("_")
        provider = parts[0].lower() if parts else name.lower()
    return ToolDefinition(
        name=name,
        server_name="composio",
        provider_name=provider,
        description=description,
        input_schema=schema or {},
    )


SAMPLE_TOOLS = [
    _tool("GOOGLECALENDAR_CREATE_EVENT", "googlecalendar", "Create a calendar event",
          {"properties": {"start_time": {}, "summary": {}, "attendees": {}}}),
    _tool("GOOGLECALENDAR_LIST_EVENTS", "googlecalendar", "List calendar events"),
    _tool("GMAIL_SEND_EMAIL", "gmail", "Send an email via Gmail",
          {"properties": {"to": {}, "subject": {}, "body": {}}}),
    _tool("SLACK_SENDS_A_MESSAGE", "slack", "Send a Slack message"),
    _tool("NOTION_CREATE_A_NEW_PAGE", "notion", "Create a Notion page"),
]


# ---------------------------------------------------------------------------
# build_tool_search_text
# ---------------------------------------------------------------------------


class TestBuildToolSearchText:
    def test_basic(self):
        tool = _tool(
            "GOOGLECALENDAR_CREATE_EVENT",
            "googlecalendar",
            "Create a new calendar event",
            {"properties": {"start_time": {}, "summary": {}, "attendees": {}}},
        )
        text = ToolEmbeddingAdapter.build_tool_search_text(tool)
        assert "googlecalendar" in text
        assert "create event" in text
        assert "Create a new calendar event" in text
        assert "start_time" in text
        assert "summary" in text
        assert "attendees" in text

    def test_no_description(self):
        tool = _tool("GMAIL_SEND_EMAIL", "gmail", "", {"properties": {"to": {}}})
        text = ToolEmbeddingAdapter.build_tool_search_text(tool)
        assert "gmail" in text
        assert "send email" in text
        assert "to" in text

    def test_no_schema(self):
        tool = _tool("SLACK_SENDS_A_MESSAGE", "slack", "Send a message")
        text = ToolEmbeddingAdapter.build_tool_search_text(tool)
        assert "slack" in text
        assert "Send a message" in text

    def test_single_part_name(self):
        tool = _tool("SYSTEM", "system", "System tool")
        text = ToolEmbeddingAdapter.build_tool_search_text(tool)
        assert "system" in text


# ---------------------------------------------------------------------------
# ToolEmbeddingResult model
# ---------------------------------------------------------------------------


class TestToolEmbeddingResult:
    def test_creation(self):
        result = ToolEmbeddingResult(
            tool_name="GMAIL_SEND_EMAIL",
            provider_name="gmail",
            rrf_score=0.032,
            keyword_rank=1,
            semantic_rank=3,
        )
        assert result.tool_name == "GMAIL_SEND_EMAIL"
        assert result.rrf_score == 0.032
        assert result.keyword_rank == 1
        assert result.semantic_rank == 3

    def test_optional_ranks(self):
        result = ToolEmbeddingResult(
            tool_name="TEST", provider_name="test", rrf_score=0.01
        )
        assert result.keyword_rank is None
        assert result.semantic_rank is None


# ---------------------------------------------------------------------------
# ToolDiscoveryResult model
# ---------------------------------------------------------------------------


class TestToolDiscoveryResult:
    def test_creation(self):
        result = ToolDiscoveryResult(
            tools=SAMPLE_TOOLS[:2],
            discovery_tier=2,
            candidate_count=10,
            reranked_count=2,
        )
        assert len(result.tools) == 2
        assert result.discovery_tier == 2


# ---------------------------------------------------------------------------
# Error models
# ---------------------------------------------------------------------------


class TestToolNotConnectedError:
    def test_creation(self):
        missing = [{"tool_name": "GMAIL_SEND_EMAIL", "provider_name": "gmail"}]
        err = ToolNotConnectedError(missing_tools=missing)
        assert err.missing_tools == missing
        assert "GMAIL_SEND_EMAIL" in str(err)

    def test_custom_message(self):
        err = ToolNotConnectedError(
            missing_tools=[{"tool_name": "X", "provider_name": "x"}],
            message="Custom msg",
        )
        assert err.message == "Custom msg"


class TestNoToolsConnectedError:
    def test_creation(self):
        err = NoToolsConnectedError(user_id="user-123")
        assert err.user_id == "user-123"
        assert "user-123" in str(err)


# ---------------------------------------------------------------------------
# ToolDiscoveryService — discover_tools
# ---------------------------------------------------------------------------


def _make_discovery_service(
    *,
    tool_search_results: list[ToolEmbeddingResult] | None = None,
    plan_search_results: list | None = None,
    reranker_results: list | None = None,
    vector_index: MagicMock | None = MagicMock(),
) -> ToolDiscoveryService:
    """Create a ToolDiscoveryService with mocked adapters."""
    # Mock tool embedding adapter
    tool_emb = AsyncMock(spec=ToolEmbeddingAdapter)
    tool_emb.search_by_intent = AsyncMock(
        return_value=tool_search_results or []
    )
    tool_emb.search_by_tool_name = AsyncMock(
        return_value=tool_search_results or []
    )

    # Mock vector index (plan search)
    vi = vector_index
    if vi is not None:
        vi.search = AsyncMock(return_value=plan_search_results or [])

    # Mock plan service
    plan_svc = AsyncMock()
    plan_svc.get_plan_by_id = AsyncMock(return_value=None)

    # Mock reranker
    reranker = None
    if reranker_results is not None:
        reranker = MagicMock()
        reranker.rerank = MagicMock(return_value=reranker_results)

    return ToolDiscoveryService(
        tool_embedding_adapter=tool_emb,
        reranker=reranker,
        vector_index_service=vi,
        plan_service=plan_svc,
        max_candidates=20,
        max_reranked=5,
        min_tools_threshold=3,
    )


class TestDiscoverTools:
    @pytest.mark.asyncio
    async def test_tier1b_direct_search_returns_tools(self):
        """Tier 1B returns tool embedding results that match available tools."""
        search_results = [
            ToolEmbeddingResult("GMAIL_SEND_EMAIL", "gmail", 0.05),
            ToolEmbeddingResult("GOOGLECALENDAR_CREATE_EVENT", "googlecalendar", 0.04),
            ToolEmbeddingResult("SLACK_SENDS_A_MESSAGE", "slack", 0.03),
        ]
        svc = _make_discovery_service(
            tool_search_results=search_results,
            vector_index=None,  # No plan search
        )

        result = await svc.discover_tools(
            intent_text="send an email to Alice",
            available_tools=SAMPLE_TOOLS,
        )

        assert result.discovery_tier >= 1
        assert result.candidate_count >= 3
        # Should not fail-open since we have >= 3 candidates
        assert len(result.tools) <= 5

    @pytest.mark.asyncio
    async def test_fail_open_when_insufficient_candidates(self):
        """When Tier 1 returns < min_threshold candidates, fail-open to full list."""
        search_results = [
            ToolEmbeddingResult("GMAIL_SEND_EMAIL", "gmail", 0.05),
        ]
        svc = _make_discovery_service(
            tool_search_results=search_results,
            vector_index=None,
        )

        result = await svc.discover_tools(
            intent_text="do something",
            available_tools=SAMPLE_TOOLS,
        )

        # Should fail-open: return all available tools
        assert result.discovery_tier == 0
        assert result.tools == SAMPLE_TOOLS

    @pytest.mark.asyncio
    async def test_empty_search_results_fail_open(self):
        """No search results → fail-open."""
        svc = _make_discovery_service(
            tool_search_results=[],
            vector_index=None,
        )

        result = await svc.discover_tools(
            intent_text="unknown intent",
            available_tools=SAMPLE_TOOLS,
        )

        assert result.discovery_tier == 0
        assert len(result.tools) == len(SAMPLE_TOOLS)

    @pytest.mark.asyncio
    async def test_tier2_reranking(self):
        """When reranker is available, Tier 2 reranks and returns top_k."""
        search_results = [
            ToolEmbeddingResult("GMAIL_SEND_EMAIL", "gmail", 0.05),
            ToolEmbeddingResult("GOOGLECALENDAR_CREATE_EVENT", "googlecalendar", 0.04),
            ToolEmbeddingResult("SLACK_SENDS_A_MESSAGE", "slack", 0.03),
            ToolEmbeddingResult("NOTION_CREATE_A_NEW_PAGE", "notion", 0.02),
        ]

        gmail_tool = SAMPLE_TOOLS[2]  # GMAIL_SEND_EMAIL
        cal_tool = SAMPLE_TOOLS[0]    # GOOGLECALENDAR_CREATE_EVENT

        reranker_results = [
            (gmail_tool, 0.95),
            (cal_tool, 0.80),
        ]

        svc = _make_discovery_service(
            tool_search_results=search_results,
            reranker_results=reranker_results,
            vector_index=None,
        )

        result = await svc.discover_tools(
            intent_text="send an email",
            available_tools=SAMPLE_TOOLS,
        )

        assert result.discovery_tier == 2
        assert result.reranked_count == 2
        assert result.tools[0].name == "GMAIL_SEND_EMAIL"

    @pytest.mark.asyncio
    async def test_reranker_failure_degrades_to_tier1(self):
        """When reranker throws, fall back to Tier 1 results."""
        search_results = [
            ToolEmbeddingResult("GMAIL_SEND_EMAIL", "gmail", 0.05),
            ToolEmbeddingResult("GOOGLECALENDAR_CREATE_EVENT", "googlecalendar", 0.04),
            ToolEmbeddingResult("SLACK_SENDS_A_MESSAGE", "slack", 0.03),
        ]

        reranker = MagicMock()
        reranker.rerank = MagicMock(side_effect=RuntimeError("model failed"))

        svc = _make_discovery_service(
            tool_search_results=search_results,
            vector_index=None,
        )
        svc._reranker = reranker

        result = await svc.discover_tools(
            intent_text="send an email",
            available_tools=SAMPLE_TOOLS,
        )

        # Should still succeed with Tier 1 results
        assert result.discovery_tier == 1
        assert len(result.tools) > 0

    @pytest.mark.asyncio
    async def test_tool_not_connected_error_raised(self):
        """When Tier 1A high-confidence tool is missing from available_tools."""
        svc = _make_discovery_service(vector_index=MagicMock())

        # Simulate plan-based discovery returning a tool not in available_tools
        async def _plan_based(intent_text):
            return {"MISSING_TOOL_CREATE": 0.8}

        svc._plan_based_discovery = AsyncMock(side_effect=_plan_based)
        svc._tool_embedding.search_by_intent = AsyncMock(return_value=[])

        available = [_tool("OTHER_TOOL", "other", "Other")]

        with pytest.raises(ToolNotConnectedError) as exc_info:
            await svc.discover_tools(
                intent_text="schedule meeting",
                available_tools=available,
            )

        assert exc_info.value.missing_tools[0]["tool_name"] == "MISSING_TOOL_CREATE"

    @pytest.mark.asyncio
    async def test_skip_tool_check_bypasses_error(self):
        """skip_tool_check=True suppresses ToolNotConnectedError."""
        svc = _make_discovery_service(vector_index=MagicMock())

        async def _plan_based(intent_text):
            return {"MISSING_TOOL_CREATE": 0.8}

        svc._plan_based_discovery = AsyncMock(side_effect=_plan_based)
        # Provide enough direct results to not fail-open
        search_results = [
            ToolEmbeddingResult("OTHER_TOOL", "other", 0.05),
            ToolEmbeddingResult("OTHER_TOOL_2", "other", 0.04),
            ToolEmbeddingResult("OTHER_TOOL_3", "other", 0.03),
        ]
        svc._tool_embedding.search_by_intent = AsyncMock(return_value=search_results)

        available = [
            _tool("OTHER_TOOL", "other"),
            _tool("OTHER_TOOL_2", "other"),
            _tool("OTHER_TOOL_3", "other"),
        ]

        # Should NOT raise
        result = await svc.discover_tools(
            intent_text="schedule meeting",
            available_tools=available,
            skip_tool_check=True,
        )
        assert isinstance(result, ToolDiscoveryResult)

    @pytest.mark.asyncio
    async def test_intersects_with_available_tools(self):
        """Only tools in available_tools are returned."""
        search_results = [
            ToolEmbeddingResult("GMAIL_SEND_EMAIL", "gmail", 0.05),
            ToolEmbeddingResult("UNKNOWN_TOOL", "unknown", 0.04),
            ToolEmbeddingResult("SLACK_SENDS_A_MESSAGE", "slack", 0.03),
        ]
        svc = _make_discovery_service(
            tool_search_results=search_results,
            vector_index=None,
        )

        # Only Gmail and Slack in available
        available = [SAMPLE_TOOLS[2], SAMPLE_TOOLS[3]]  # GMAIL, SLACK

        result = await svc.discover_tools(
            intent_text="send email",
            available_tools=available,
        )

        tool_names = {t.name for t in result.tools}
        assert "UNKNOWN_TOOL" not in tool_names

    @pytest.mark.asyncio
    async def test_fewer_candidates_than_top_k_is_ok(self):
        """When fewer candidates than top_k=5, return all without error."""
        search_results = [
            ToolEmbeddingResult("GMAIL_SEND_EMAIL", "gmail", 0.05),
            ToolEmbeddingResult("GOOGLECALENDAR_CREATE_EVENT", "googlecalendar", 0.04),
            ToolEmbeddingResult("SLACK_SENDS_A_MESSAGE", "slack", 0.03),
        ]
        svc = _make_discovery_service(
            tool_search_results=search_results,
            vector_index=None,
        )

        result = await svc.discover_tools(
            intent_text="send email",
            available_tools=SAMPLE_TOOLS,
        )

        assert len(result.tools) == 3  # All 3, not padded to 5


# ---------------------------------------------------------------------------
# ToolDiscoveryService — agentic_expand (Tier 3)
# ---------------------------------------------------------------------------


class TestAgenticExpand:
    @pytest.mark.asyncio
    async def test_resolves_tool_by_name(self):
        """Agentic expand finds canonical tool for a non-canonical name."""
        search_results = [
            ToolEmbeddingResult("GOOGLECALENDAR_CREATE_EVENT", "googlecalendar", 0.08),
        ]
        svc = _make_discovery_service(tool_search_results=search_results)

        expanded = await svc.agentic_expand(
            missing_tool_name="google_calendar_create_event",
            available_tools=SAMPLE_TOOLS,
            current_selected=[],
        )

        assert len(expanded) == 1
        assert expanded[0].name == "GOOGLECALENDAR_CREATE_EVENT"

    @pytest.mark.asyncio
    async def test_no_match_returns_current(self):
        """When no matching tool found, return current_selected unchanged."""
        svc = _make_discovery_service(tool_search_results=[])

        current = [SAMPLE_TOOLS[0]]
        expanded = await svc.agentic_expand(
            missing_tool_name="nonexistent_tool",
            available_tools=SAMPLE_TOOLS,
            current_selected=current,
        )

        assert expanded == current

    @pytest.mark.asyncio
    async def test_does_not_duplicate(self):
        """If the resolved tool is already in current_selected, skip it."""
        cal_tool = SAMPLE_TOOLS[0]  # GOOGLECALENDAR_CREATE_EVENT
        search_results = [
            ToolEmbeddingResult("GOOGLECALENDAR_CREATE_EVENT", "googlecalendar", 0.08),
        ]
        svc = _make_discovery_service(tool_search_results=search_results)

        expanded = await svc.agentic_expand(
            missing_tool_name="google.calendar",
            available_tools=SAMPLE_TOOLS,
            current_selected=[cal_tool],
        )

        # Should not add duplicate
        assert expanded == [cal_tool]

    @pytest.mark.asyncio
    async def test_search_failure_returns_current(self):
        """When search fails, return current_selected unchanged."""
        svc = _make_discovery_service()
        svc._tool_embedding.search_by_tool_name = AsyncMock(
            side_effect=RuntimeError("DB error")
        )

        current = [SAMPLE_TOOLS[0]]
        expanded = await svc.agentic_expand(
            missing_tool_name="broken_tool",
            available_tools=SAMPLE_TOOLS,
            current_selected=current,
        )

        assert expanded == current

    @pytest.mark.asyncio
    async def test_only_returns_available_tools(self):
        """Agentic expand only returns tools in available_tools."""
        search_results = [
            ToolEmbeddingResult("UNAVAILABLE_TOOL", "x", 0.08),
            ToolEmbeddingResult("GMAIL_SEND_EMAIL", "gmail", 0.06),
        ]
        svc = _make_discovery_service(tool_search_results=search_results)

        expanded = await svc.agentic_expand(
            missing_tool_name="some_tool",
            available_tools=SAMPLE_TOOLS,
            current_selected=[],
        )

        # UNAVAILABLE_TOOL is not in SAMPLE_TOOLS, so it should pick GMAIL
        assert len(expanded) == 1
        assert expanded[0].name == "GMAIL_SEND_EMAIL"


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------


class TestGracefulDegradation:
    @pytest.mark.asyncio
    async def test_vector_index_unavailable(self):
        """When VectorIndex is None, only Tier 1B runs."""
        search_results = [
            ToolEmbeddingResult("GMAIL_SEND_EMAIL", "gmail", 0.05),
            ToolEmbeddingResult("GOOGLECALENDAR_CREATE_EVENT", "googlecalendar", 0.04),
            ToolEmbeddingResult("SLACK_SENDS_A_MESSAGE", "slack", 0.03),
        ]
        svc = _make_discovery_service(
            tool_search_results=search_results,
            vector_index=None,  # No VectorIndex
        )

        result = await svc.discover_tools(
            intent_text="send email",
            available_tools=SAMPLE_TOOLS,
        )

        assert result.plan_based_tools == 0
        assert result.direct_tools == 3

    @pytest.mark.asyncio
    async def test_tool_search_failure_still_works(self):
        """When Tier 1B fails, rely on Tier 1A or fail-open."""
        svc = _make_discovery_service(vector_index=None)
        svc._tool_embedding.search_by_intent = AsyncMock(
            side_effect=RuntimeError("DB error")
        )

        result = await svc.discover_tools(
            intent_text="send email",
            available_tools=SAMPLE_TOOLS,
        )

        # Both tiers failed → fail-open to full catalog
        assert result.discovery_tier == 0
        assert result.tools == SAMPLE_TOOLS

    @pytest.mark.asyncio
    async def test_empty_available_tools(self):
        """When available_tools is empty, return empty result."""
        svc = _make_discovery_service(vector_index=None)

        result = await svc.discover_tools(
            intent_text="send email",
            available_tools=[],
        )

        assert result.discovery_tier == 0
        assert result.tools == []
