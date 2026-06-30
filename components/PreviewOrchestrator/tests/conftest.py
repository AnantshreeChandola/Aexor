"""
PreviewOrchestrator test fixtures -- mock adapters, sample plans, configured services.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from components.ExecuteOrchestrator.adapters.dag_resolver import DAGResolver
from components.ExecuteOrchestrator.adapters.template_resolver import (
    TemplateResolver,
)
from components.PreviewOrchestrator.adapters.preview_cache import (
    PreviewCacheAdapter,
)
from components.PreviewOrchestrator.adapters.previewability_checker import (
    PreviewabilityChecker,
)
from components.PreviewOrchestrator.domain.models import (
    PreviewRequest,
)
from components.PreviewOrchestrator.service.preview_service import (
    PreviewService,
)
from shared.mcp.catalog import ToolDefinition
from shared.schemas.intent import Intent
from shared.schemas.plan import Plan, PlanMeta, PlanStep

SAMPLE_PLAN_ID = "01JBXYZ1234567890ABCDEFGHI"
SAMPLE_USER_ID = "test-user-001"
SAMPLE_TRACE_ID = "trace-abc-123"


def _plan_meta() -> PlanMeta:
    return PlanMeta(
        created_at=datetime.now(UTC).isoformat(),
        canonical_hash="a" * 64,
    )


def _intent() -> Intent:
    return Intent(
        intent="schedule_meeting",
        entities={"attendee": "alice@company.com", "day": "Tuesday"},
        constraints={"scopes": ["calendar.read"]},
        tz="America/Chicago",
        user_id=SAMPLE_USER_ID,
    )


@pytest.fixture()
def sample_plan() -> Plan:
    """5-step plan: 3 previewable API, 1 gated Booker, 1 Notifier.

    DAG: steps 1,2 parallel; step 3 after [1,2]; step 4 after [3] (Booker, gate_id);
    step 5 after [4] (Notifier).
    """
    return Plan(
        plan_id=SAMPLE_PLAN_ID,
        intent=_intent(),
        graph=[
            PlanStep(
                step=1,
                mode="interactive",
                role="Fetcher",
                uses="google.calendar",
                call="list_events",
                args={"calendar_id": "primary"},
                after=[],
            ),
            PlanStep(
                step=2,
                mode="interactive",
                role="Fetcher",
                uses="google.calendar",
                call="list_events",
                args={"calendar_id": "secondary"},
                after=[],
            ),
            PlanStep(
                step=3,
                mode="interactive",
                role="Analyzer",
                uses="system.analyzer",
                call="find_overlaps",
                args={
                    "cal1": "{{step_1.result.events}}",
                    "cal2": "{{step_2.result.events}}",
                },
                after=[1, 2],
            ),
            PlanStep(
                step=4,
                mode="interactive",
                role="Booker",
                uses="google.calendar",
                call="create_event",
                args={},
                after=[3],
                gate_id="gate-A",
            ),
            PlanStep(
                step=5,
                mode="interactive",
                role="Notifier",
                uses="system.notifier",
                call="send_notification",
                args={},
                after=[4],
            ),
        ],
        meta=_plan_meta(),
    )


@pytest.fixture()
def hybrid_plan() -> Plan:
    """Mixed-type plan: 2 API (previewable), 1 llm_reasoning, 1 policy_check, 1 API after reasoning."""
    return Plan(
        plan_id=SAMPLE_PLAN_ID,
        intent=_intent(),
        graph=[
            PlanStep(
                step=1,
                mode="interactive",
                role="Fetcher",
                uses="google.flights",
                call="search_flights",
                args={"dest": "Tokyo"},
                after=[],
                type="api",
            ),
            PlanStep(
                step=2,
                mode="interactive",
                role="Fetcher",
                uses="google.hotels",
                call="search_hotels",
                args={"dest": "Tokyo"},
                after=[],
                type="api",
            ),
            PlanStep(
                step=3,
                mode="interactive",
                role="Reasoner",
                uses="system.llm",
                call="analyze_options",
                args={},
                after=[1, 2],
                type="llm_reasoning",
                trust_level="untrusted_input",
            ),
            PlanStep(
                step=4,
                mode="interactive",
                role="Analyzer",
                uses="system.policy",
                call="check_budget",
                args={},
                after=[3],
                type="policy_check",
            ),
            PlanStep(
                step=5,
                mode="interactive",
                role="Fetcher",
                uses="google.flights",
                call="get_details",
                args={"flight": "{{step_3.result.best_flight}}"},
                after=[3],
                type="api",
            ),
        ],
        meta=_plan_meta(),
    )


@pytest.fixture()
def parallel_plan() -> Plan:
    """4 steps, all at same DAG level (no deps), all previewable."""
    return Plan(
        plan_id=SAMPLE_PLAN_ID,
        intent=_intent(),
        graph=[
            PlanStep(
                step=1,
                mode="interactive",
                role="Fetcher",
                uses="google.calendar",
                call="list_events",
                args={},
                after=[],
            ),
            PlanStep(
                step=2,
                mode="interactive",
                role="Fetcher",
                uses="google.calendar",
                call="list_events",
                args={},
                after=[],
            ),
            PlanStep(
                step=3,
                mode="interactive",
                role="Fetcher",
                uses="google.calendar",
                call="list_events",
                args={},
                after=[],
            ),
            PlanStep(
                step=4,
                mode="interactive",
                role="Fetcher",
                uses="google.calendar",
                call="list_events",
                args={},
                after=[],
            ),
        ],
        meta=_plan_meta(),
    )


@pytest.fixture()
def empty_previewable_plan() -> Plan:
    """All steps are non-previewable (Booker with gate_id)."""
    return Plan(
        plan_id=SAMPLE_PLAN_ID,
        intent=_intent(),
        graph=[
            PlanStep(
                step=1,
                mode="interactive",
                role="Booker",
                uses="google.calendar",
                call="create_event",
                args={},
                after=[],
                gate_id="gate-1",
            ),
            PlanStep(
                step=2,
                mode="interactive",
                role="Booker",
                uses="google.calendar",
                call="create_event",
                args={},
                after=[],
                gate_id="gate-2",
            ),
        ],
        meta=_plan_meta(),
    )


@pytest.fixture()
def single_step_plan() -> Plan:
    """Single previewable step."""
    return Plan(
        plan_id=SAMPLE_PLAN_ID,
        intent=_intent(),
        graph=[
            PlanStep(
                step=1,
                mode="interactive",
                role="Fetcher",
                uses="google.calendar",
                call="list_events",
                args={},
                after=[],
            ),
        ],
        meta=_plan_meta(),
    )


@pytest.fixture()
def mock_mcp_client() -> AsyncMock:
    """Mock implementing the MCPClient Protocol.

    invoke() returns configurable dict results. Tracks calls for assertion.
    """
    client = AsyncMock()
    client.invoke = AsyncMock(return_value={"events": [{"id": "evt-1", "summary": "Meeting"}]})
    return client


def _make_tool(
    tool_id: str,
    operations: dict[str, bool] | None = None,
) -> ToolDefinition:
    """Build a ToolDefinition for test fixtures."""
    return ToolDefinition(
        name=tool_id,
        server_name="test",
        provider_name=tool_id.split(".")[0] if "." in tool_id else tool_id,
        description=f"Test tool {tool_id}",
    )


PREVIEWABLE_TOOLS = {
    "google.calendar": _make_tool(
        "google.calendar",
        {
            "list_events": True,
            "create_event": False,
        },
    ),
    "google.flights": _make_tool(
        "google.flights",
        {
            "search_flights": True,
            "get_details": True,
        },
    ),
    "google.hotels": _make_tool(
        "google.hotels",
        {
            "search_hotels": True,
        },
    ),
    "system.analyzer": _make_tool(
        "system.analyzer",
        {
            "find_overlaps": True,
        },
    ),
    "system.notifier": _make_tool(
        "system.notifier",
        {
            "send_notification": False,
        },
    ),
    "system.llm": _make_tool(
        "system.llm",
        {
            "analyze_options": False,
        },
    ),
    "system.policy": _make_tool(
        "system.policy",
        {
            "check_budget": False,
        },
    ),
    "GMAIL_SEND_EMAIL": _make_tool(
        "GMAIL_SEND_EMAIL",
    ),
    "GOOGLECALENDAR_CREATE_EVENT": _make_tool(
        "GOOGLECALENDAR_CREATE_EVENT",
    ),
}


@pytest.fixture()
def mock_registry_service() -> AsyncMock:
    """Mock ToolCatalog. get_tool() returns ToolDefinition or None."""
    service = AsyncMock()

    def _get_tool(tool_id: str) -> ToolDefinition | None:
        return PREVIEWABLE_TOOLS.get(tool_id)

    service.get_tool = _get_tool
    return service


class FakeRedisClient:
    """In-memory fake async Redis client for testing."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._ttls: dict[str, int] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._store[key] = value
        if ex is not None:
            self._ttls[key] = ex

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)
        self._ttls.pop(key, None)

    def get_ttl(self, key: str) -> int | None:
        """Test helper: retrieve the TTL set for a key."""
        return self._ttls.get(key)


@pytest.fixture()
def mock_redis_client() -> FakeRedisClient:
    """Fake async Redis client with in-memory dict storage."""
    return FakeRedisClient()


@pytest.fixture()
def preview_request(sample_plan: Plan) -> PreviewRequest:
    """PreviewRequest with the sample plan."""
    return PreviewRequest(
        plan=sample_plan,
        user_id=SAMPLE_USER_ID,
        trace_id=SAMPLE_TRACE_ID,
    )


@pytest.fixture()
def preview_service(
    mock_mcp_client: AsyncMock,
    mock_registry_service: AsyncMock,
    mock_redis_client: FakeRedisClient,
) -> PreviewService:
    """Fully wired PreviewService with all mock dependencies."""
    dag_resolver = DAGResolver()
    template_resolver = TemplateResolver()
    checker = PreviewabilityChecker(mock_registry_service)
    cache = PreviewCacheAdapter(mock_redis_client, ttl_s=900)

    return PreviewService(
        dag_resolver=dag_resolver,
        template_resolver=template_resolver,
        mcp_client=mock_mcp_client,
        checker=checker,
        cache=cache,
        tool_catalog=mock_registry_service,
    )


@pytest.fixture()
def preview_service_no_redis(
    mock_mcp_client: AsyncMock,
    mock_registry_service: AsyncMock,
) -> PreviewService:
    """PreviewService with Redis unavailable (None)."""
    dag_resolver = DAGResolver()
    template_resolver = TemplateResolver()
    checker = PreviewabilityChecker(mock_registry_service)
    cache = PreviewCacheAdapter(None, ttl_s=900)

    return PreviewService(
        dag_resolver=dag_resolver,
        template_resolver=template_resolver,
        mcp_client=mock_mcp_client,
        checker=checker,
        cache=cache,
        tool_catalog=mock_registry_service,
    )
