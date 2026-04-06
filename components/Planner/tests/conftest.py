"""
Planner test fixtures — mocked dependencies, sample data, configured services.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from components.Planner.adapters.circuit_breaker import CircuitBreaker
from components.Planner.adapters.plan_validator import PlanValidator
from components.Planner.adapters.prompt_builder import PromptBuilder
from components.Planner.domain.models import LLMCallError
from components.Planner.service.planner_service import PlannerService
from shared.schemas.evidence import EvidenceItem
from shared.schemas.intent import Intent

# ---------------------------------------------------------------------------
# Sample data constants
# ---------------------------------------------------------------------------

SAMPLE_USER_ID = "550e8400-e29b-41d4-a716-446655440000"
SAMPLE_TRACE_ID = "a" * 32

SAMPLE_INTENT = Intent(
    intent="schedule_meeting",
    entities={"attendee": "Alice", "time": "tomorrow 2pm", "duration_min": 30},
    constraints={"prefer_afternoon": True, "room": "any"},
    tz="America/Chicago",
    user_id=SAMPLE_USER_ID,
    trace_id=SAMPLE_TRACE_ID,
)

SAMPLE_EVIDENCE = [
    EvidenceItem(
        type="preference",
        key="meeting_duration_min",
        value=30,
        confidence=1.0,
        source_ref="profilestore:prefs/meeting_duration_min",
        tier=2,
    ),
    EvidenceItem(
        type="preference",
        key="preferred_room",
        value="Room A",
        confidence=0.9,
        source_ref="profilestore:prefs/preferred_room",
        tier=2,
    ),
    EvidenceItem(
        type="history",
        key="last_meeting_with_alice",
        value="2025-12-20T10:00:00Z",
        confidence=0.95,
        source_ref="history:interactions/alice-123",
        ttl_days=30,
        tier=3,
    ),
]


def _make_valid_plan_json(plan_id: str = "01JBXYZ1234567890ABCDEFGHI") -> str:
    """Generate a valid Plan JSON string matching GLOBAL_SPEC §2.3."""
    now = datetime.now(UTC).isoformat()
    return json.dumps(
        {
            "plan_id": plan_id,
            "intent": SAMPLE_INTENT.model_dump(mode="json"),
            "trace_id": SAMPLE_TRACE_ID,
            "graph": [
                {
                    "step": 1,
                    "mode": "interactive",
                    "role": "Fetcher",
                    "uses": "google.calendar",
                    "call": "list_events",
                    "args": {"date": "tomorrow"},
                    "after": [],
                    "timeout_s": 30,
                    "dry_run": True,
                },
                {
                    "step": 2,
                    "mode": "interactive",
                    "role": "Analyzer",
                    "uses": "system.echo",
                    "call": "analyze",
                    "args": {"check": "availability"},
                    "after": [1],
                    "timeout_s": 30,
                    "dry_run": True,
                },
                {
                    "step": 3,
                    "mode": "interactive",
                    "role": "Booker",
                    "uses": "google.calendar",
                    "call": "create_event",
                    "args": {"attendee": "Alice", "duration_min": 30},
                    "after": [2],
                    "timeout_s": 60,
                    "gate_id": "gate-A",
                    "dry_run": True,
                },
                {
                    "step": 4,
                    "mode": "interactive",
                    "role": "Notifier",
                    "uses": "system.echo",
                    "call": "notify",
                    "args": {"message": "Meeting scheduled"},
                    "after": [3],
                    "timeout_s": 30,
                    "dry_run": True,
                },
            ],
            "constraints": {
                "scopes": ["calendar.read", "calendar.write"],
                "ttl_s": 900,
                "max_retries": 3,
            },
            "plugins": ["google.calendar", "system.echo"],
            "meta": {
                "created_at": now,
                "author": "planner@system",
                "version": "v2.0.0",
                "canonical_hash": "a" * 64,
                "hash_algo": "sha256",
            },
        }
    )


SAMPLE_VALID_PLAN_JSON = _make_valid_plan_json()

SAMPLE_INVALID_JSON = "{ this is not valid json }"

SAMPLE_PLAN_FORWARD_DEP = json.dumps(
    {
        "plan_id": "01JBXYZ1234567890ABCDEFGHI",
        "intent": SAMPLE_INTENT.model_dump(mode="json"),
        "trace_id": SAMPLE_TRACE_ID,
        "graph": [
            {
                "step": 1,
                "mode": "interactive",
                "role": "Fetcher",
                "uses": "google.calendar",
                "call": "list",
                "args": {},
                "after": [2],
                "timeout_s": 30,
                "dry_run": True,
            },
            {
                "step": 2,
                "mode": "interactive",
                "role": "Analyzer",
                "uses": "system.echo",
                "call": "analyze",
                "args": {},
                "after": [],
                "timeout_s": 30,
                "dry_run": True,
            },
        ],
        "constraints": {"scopes": [], "ttl_s": 900, "max_retries": 3},
        "plugins": ["google.calendar", "system.echo"],
        "meta": {
            "created_at": datetime.now(UTC).isoformat(),
            "author": "planner@system",
            "version": "v2.0.0",
            "canonical_hash": "a" * 64,
            "hash_algo": "sha256",
        },
    }
)


def _make_too_many_steps_json() -> str:
    """Generate plan JSON with 101 steps (exceeds MAX_STEPS=100)."""
    now = datetime.now(UTC).isoformat()
    steps = []
    for i in range(1, 102):
        steps.append(
            {
                "step": i,
                "mode": "interactive",
                "role": "Fetcher",
                "uses": "system.echo",
                "call": "echo",
                "args": {},
                "after": [i - 1] if i > 1 else [],
                "timeout_s": 30,
                "dry_run": True,
            }
        )
    return json.dumps(
        {
            "plan_id": "01JBXYZ1234567890ABCDEFGHI",
            "intent": SAMPLE_INTENT.model_dump(mode="json"),
            "trace_id": SAMPLE_TRACE_ID,
            "graph": steps,
            "constraints": {"scopes": [], "ttl_s": 900, "max_retries": 3},
            "plugins": ["system.echo"],
            "meta": {
                "created_at": now,
                "author": "planner@system",
                "version": "v2.0.0",
                "canonical_hash": "a" * 64,
                "hash_algo": "sha256",
            },
        }
    )


SAMPLE_PLAN_TOO_MANY_STEPS = _make_too_many_steps_json()

SAMPLE_PLAN_MISSING_TOOL = json.dumps(
    {
        "plan_id": "01JBXYZ1234567890ABCDEFGHI",
        "intent": SAMPLE_INTENT.model_dump(mode="json"),
        "trace_id": SAMPLE_TRACE_ID,
        "graph": [
            {
                "step": 1,
                "mode": "interactive",
                "role": "Fetcher",
                "uses": "nonexistent.tool",
                "call": "fetch",
                "args": {},
                "after": [],
                "timeout_s": 30,
                "dry_run": True,
            },
        ],
        "constraints": {"scopes": [], "ttl_s": 900, "max_retries": 3},
        "plugins": ["nonexistent.tool"],
        "meta": {
            "created_at": datetime.now(UTC).isoformat(),
            "author": "planner@system",
            "version": "v2.0.0",
            "canonical_hash": "a" * 64,
            "hash_algo": "sha256",
        },
    }
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_tool_definition(name: str, description: str = "", server_name: str = "composio"):
    """Create a ToolDefinition-like object for tests."""
    from shared.mcp.catalog import ToolDefinition

    return ToolDefinition(
        name=name,
        server_name=server_name,
        provider_name=name.split(".")[0] if "." in name else name,
        description=description,
        input_schema={},
    )


@pytest.fixture()
def sample_intent() -> Intent:
    return SAMPLE_INTENT


@pytest.fixture()
def sample_evidence() -> list[EvidenceItem]:
    return list(SAMPLE_EVIDENCE)


@pytest.fixture()
def mock_llm_adapter():
    """LLM adapter that returns valid plan JSON."""
    adapter = AsyncMock()
    adapter.generate = AsyncMock(return_value=SAMPLE_VALID_PLAN_JSON)
    return adapter


@pytest.fixture()
def mock_failing_llm_adapter():
    """LLM adapter that always raises LLMCallError."""
    adapter = AsyncMock()
    adapter.generate = AsyncMock(side_effect=LLMCallError("test-model", "simulated failure"))
    return adapter


@pytest.fixture()
def mock_context_rag_service():
    """ContextRAG that returns sample evidence."""
    svc = AsyncMock()
    result = MagicMock()
    result.evidence = list(SAMPLE_EVIDENCE)
    result.total_bytes = 512
    result.degraded_sources = []
    result.query_duration_ms = 50
    svc.gather_evidence = AsyncMock(return_value=result)
    return svc


@pytest.fixture()
def mock_degraded_context_rag_service():
    """ContextRAG that returns empty/degraded evidence."""
    svc = AsyncMock()
    result = MagicMock()
    result.evidence = []
    result.total_bytes = 0
    result.degraded_sources = ["profilestore", "history"]
    result.query_duration_ms = 10
    svc.gather_evidence = AsyncMock(return_value=result)
    return svc


@pytest.fixture()
def mock_tool_catalog():
    """ToolCatalog with google.calendar and system.echo tools."""
    catalog = MagicMock()
    tools = [
        _make_tool_definition("google.calendar", "Google Calendar"),
        _make_tool_definition("system.echo", "System Echo"),
    ]
    catalog.get_all_tools = MagicMock(return_value=tools)
    catalog.get_tool = MagicMock(
        side_effect=lambda name: next((t for t in tools if t.name == name), None)
    )
    return catalog


@pytest.fixture()
def mock_empty_tool_catalog():
    """ToolCatalog with no tools."""
    catalog = MagicMock()
    catalog.get_all_tools = MagicMock(return_value=[])
    catalog.get_tool = MagicMock(return_value=None)
    return catalog


@pytest.fixture()
def mock_plan_service():
    """PlanLibrary that returns template evidence items."""
    svc = AsyncMock()
    template = EvidenceItem(
        type="plan",
        key="schedule_meeting:template",
        value=json.dumps(
            {
                "graph": [
                    {
                        "step": 1,
                        "mode": "interactive",
                        "role": "Fetcher",
                        "uses": "google.calendar",
                        "call": "list_events",
                        "args": {},
                        "after": [],
                        "timeout_s": 30,
                        "dry_run": True,
                    },
                    {
                        "step": 2,
                        "mode": "interactive",
                        "role": "Booker",
                        "uses": "google.calendar",
                        "call": "create_event",
                        "args": {},
                        "after": [1],
                        "timeout_s": 60,
                        "gate_id": "gate-A",
                        "dry_run": True,
                    },
                ],
            }
        ),
        confidence=0.85,
        source_ref="planlibrary:schedule_meeting",
        tier=3,
    )
    svc.get_plans_by_intent = AsyncMock(return_value=[template])
    return svc


@pytest.fixture()
def mock_empty_plan_service():
    """PlanLibrary with no matching templates."""
    svc = AsyncMock()
    svc.get_plans_by_intent = AsyncMock(return_value=[])
    return svc


@pytest.fixture()
def planner_service(
    mock_llm_adapter,
    mock_context_rag_service,
    mock_tool_catalog,
    mock_plan_service,
):
    """Fully wired PlannerService with all mocks."""
    return PlannerService(
        context_rag_service=mock_context_rag_service,
        tool_catalog=mock_tool_catalog,
        plan_service=mock_plan_service,
        llm_adapter=mock_llm_adapter,
        prompt_builder=PromptBuilder(),
        validator=PlanValidator(),
        primary_breaker=CircuitBreaker(model_name="primary-test"),
        fallback_breaker=CircuitBreaker(model_name="fallback-test"),
        primary_model="test-primary",
        fallback_model="test-fallback",
        max_output_tokens=4096,
    )
