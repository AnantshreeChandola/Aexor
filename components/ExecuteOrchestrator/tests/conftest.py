"""
Test Fixtures for ExecuteOrchestrator

Shared fixtures, mock factories, and sample data for all test files.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from jose import jwt

from shared.schemas.intent import Intent
from shared.schemas.plan import Plan, PlanConstraints, PlanMeta, PlanStep
from shared.schemas.policy import (
    PolicyDecision,
    ReasoningConfig,
)
from shared.schemas.signature import Signature

from ..adapters.dag_resolver import DAGResolver
from ..adapters.idempotency import IdempotencyAdapter
from ..adapters.resource_lock import ResourceLockAdapter
from ..adapters.retry import RetryPolicy
from ..adapters.template_resolver import TemplateResolver
from ..domain.models import ExecuteRequest
from ..service.execute_service import ExecuteService

# Approval token secret (must match service)
_TOKEN_SECRET = "approval-gate-secret"


# ---------------------------------------------------------------------------
# Sample data builders
# ---------------------------------------------------------------------------


def _make_intent() -> Intent:
    return Intent(
        intent="schedule_meeting",
        entities={"attendee": "alice@example.com", "time": "3pm"},
        constraints={"duration": 30},
        user_id="user-001",
    )


def _make_plan_meta() -> PlanMeta:
    return PlanMeta(
        created_at=datetime.now(UTC).isoformat(),
        author="planner@system",
        version="v2.0.0",
        canonical_hash="a" * 64,
        hash_algo="sha256",
    )


def _make_plan_constraints() -> PlanConstraints:
    return PlanConstraints(
        scopes=["calendar.write"],
        ttl_s=900,
        max_retries=3,
        policy_version=1,
    )


def _make_signature(plan_id: str) -> Signature:
    return Signature(
        algo="Ed25519",
        signer="planner@system",
        signature="s" * 88,
        pubkey_id="k1",
        plan_hash="a" * 64,
        ts=datetime.now(UTC).isoformat(),
        nonce="N" * 26,
    )


def _make_approval_token(plan_id: str) -> str:
    return jwt.encode(
        {"plan_id": plan_id, "exp": time.time() + 900},
        _TOKEN_SECRET,
        algorithm="HS256",
    )


# ---------------------------------------------------------------------------
# Sample plan fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_plan() -> Plan:
    """Valid 4-step pure API plan."""
    plan_id = "A" * 26
    return Plan(
        plan_id=plan_id,
        intent=_make_intent(),
        trace_id="trace-001",
        graph=[
            PlanStep(
                step=1,
                mode="interactive",
                role="Fetcher",
                uses="google.calendar",
                call="list_events",
                args={"date": "2026-04-01"},
                after=[],
            ),
            PlanStep(
                step=2,
                mode="interactive",
                role="Fetcher",
                uses="google.contacts",
                call="get_contact",
                args={"email": "alice@example.com"},
                after=[],
            ),
            PlanStep(
                step=3,
                mode="interactive",
                role="Analyzer",
                uses="google.calendar",
                call="find_slot",
                args={"events": "{{step_1.result.events}}"},
                after=[1, 2],
            ),
            PlanStep(
                step=4,
                mode="interactive",
                role="Booker",
                uses="google.calendar",
                call="create_event",
                args={"slot": "{{step_3.result.slot}}"},
                after=[3],
            ),
        ],
        constraints=_make_plan_constraints(),
        plugins=["google.calendar", "google.contacts"],
        meta=_make_plan_meta(),
    )


@pytest.fixture()
def sample_hybrid_plan() -> Plan:
    """6-step hybrid plan with Reasoner."""
    plan_id = "B" * 26
    return Plan(
        plan_id=plan_id,
        intent=_make_intent(),
        trace_id="trace-002",
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
                uses="google.contacts",
                call="get_contact",
                args={},
                after=[],
            ),
            PlanStep(
                step=3,
                mode="interactive",
                role="Analyzer",
                uses="google.calendar",
                call="analyze",
                args={},
                after=[1, 2],
                type="llm_reasoning",
                trust_level="untrusted_input",
                reasoning_config=ReasoningConfig(
                    system_prompt_ref="analyzer.calendar",
                ),
                context_from=[1, 2],
            ),
            PlanStep(
                step=4,
                mode="interactive",
                role="Reasoner",
                uses="system.reasoner",
                call="reason",
                args={},
                after=[3],
                type="llm_reasoning",
                trust_level="trusted",
                can_spawn=True,
                max_spawned_steps=3,
                reasoning_config=ReasoningConfig(
                    system_prompt_ref="reasoner.scheduling",
                ),
                context_from=[3],
                policy_ref="policy-001",
            ),
            PlanStep(
                step=5,
                mode="interactive",
                role="Booker",
                uses="google.calendar",
                call="create_event",
                args={},
                after=[4],
            ),
            PlanStep(
                step=6,
                mode="interactive",
                role="Notifier",
                uses="google.gmail",
                call="send_email",
                args={},
                after=[5],
            ),
        ],
        constraints=_make_plan_constraints(),
        plugins=["google.calendar", "google.contacts", "google.gmail", "system.reasoner"],
        meta=_make_plan_meta(),
    )


@pytest.fixture()
def sample_signature(sample_plan: Plan) -> Signature:
    return _make_signature(sample_plan.plan_id)


@pytest.fixture()
def sample_approval_token(sample_plan: Plan) -> str:
    return _make_approval_token(sample_plan.plan_id)


@pytest.fixture()
def sample_execute_request(
    sample_plan: Plan,
    sample_signature: Signature,
    sample_approval_token: str,
) -> ExecuteRequest:
    return ExecuteRequest(
        plan=sample_plan,
        signature=sample_signature,
        approval_token=sample_approval_token,
        user_id="user-001",
        trace_id="trace-001",
        preview_state=None,
        integration_credentials={},
    )


# ---------------------------------------------------------------------------
# Mock service fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_signer_service() -> AsyncMock:
    svc = AsyncMock()
    svc.verify_signature = AsyncMock(return_value=True)
    return svc


@pytest.fixture()
def mock_policy_service() -> AsyncMock:
    svc = AsyncMock()
    svc.evaluate_spawn = AsyncMock(
        return_value=PolicyDecision(
            allowed=True,
            requires_approval=False,
            reason="Approved by policy 'test-policy' v1",
        )
    )
    return svc


@pytest.fixture()
def mock_registry_service() -> AsyncMock:
    svc = AsyncMock()
    tool_mock = MagicMock()
    tool_mock.mcp_server = "http://mcp.test:8080"
    tool_mock.operations = {
        "list_events": MagicMock(n8n_node="list_events", compensation=None),
        "get_contact": MagicMock(n8n_node="get_contact", compensation=None),
        "find_slot": MagicMock(n8n_node="find_slot", compensation=None),
        "create_event": MagicMock(n8n_node="create_event", compensation="delete_event"),
        "analyze": MagicMock(n8n_node="analyze", compensation=None),
        "reason": MagicMock(n8n_node="reason", compensation=None),
        "send_email": MagicMock(n8n_node="send_email", compensation=None),
    }
    svc.get_tool = AsyncMock(return_value=tool_mock)
    return svc


@pytest.fixture()
def mock_plan_writer_service() -> AsyncMock:
    svc = AsyncMock()
    svc.persist_outcome = AsyncMock(return_value=None)
    return svc


@pytest.fixture()
def mock_mcp_client() -> AsyncMock:
    """MCP client that returns template-compatible results per call type."""
    client = AsyncMock()

    async def _dynamic_invoke(server, tool, args, **kwargs):
        """Return results that match what downstream templates expect."""
        results = {
            "list_events": {"status": "ok", "events": [{"id": "e1", "time": "3pm"}]},
            "get_contact": {"status": "ok", "name": "Alice", "email": "alice@example.com"},
            "find_slot": {"status": "ok", "slot": "2026-04-01T15:00:00"},
            "create_event": {"status": "ok", "id": "evt-123"},
            "delete_event": {"status": "ok", "deleted": True},
            "analyze": {"status": "ok", "analysis": "Available slot found"},
            "reason": {"status": "ok", "content": "Reasoning complete"},
            "send_email": {"status": "ok", "message_id": "msg-001"},
            "search": {"status": "ok", "results": []},
        }
        return results.get(tool, {"status": "ok", "id": "rslt-001"})

    client.invoke = AsyncMock(side_effect=_dynamic_invoke)
    return client


@pytest.fixture()
def mock_llm_client() -> AsyncMock:
    client = AsyncMock()
    client.reason = AsyncMock(
        return_value={
            "content": "Analysis complete.",
            "spawn_requests": [],
        }
    )
    return client


@pytest.fixture()
def mock_credential_vault() -> AsyncMock:
    vault = AsyncMock()
    vault.decrypt = AsyncMock(return_value="secret-token-value")
    return vault


@pytest.fixture()
def mock_redis() -> AsyncMock:
    """Simple mock Redis for tests that do not need fakeredis."""
    r = AsyncMock()
    r.hgetall = AsyncMock(return_value={})
    r.hset = AsyncMock(return_value=True)
    r.expire = AsyncMock(return_value=True)
    r.delete = AsyncMock(return_value=1)
    r.set = AsyncMock(return_value=True)
    r.get = AsyncMock(return_value=None)
    return r


@pytest.fixture()
def execute_service(
    mock_signer_service: AsyncMock,
    mock_policy_service: AsyncMock,
    mock_registry_service: AsyncMock,
    mock_plan_writer_service: AsyncMock,
    mock_mcp_client: AsyncMock,
    mock_llm_client: AsyncMock,
    mock_credential_vault: AsyncMock,
    mock_redis: AsyncMock,
) -> ExecuteService:
    """Fully wired ExecuteService with all mocked dependencies."""
    return ExecuteService(
        signer_service=mock_signer_service,
        policy_service=mock_policy_service,
        registry_service=mock_registry_service,
        plan_writer_service=mock_plan_writer_service,
        mcp_client=mock_mcp_client,
        llm_client=mock_llm_client,
        credential_vault=mock_credential_vault,
        idempotency=IdempotencyAdapter(mock_redis),
        resource_lock=ResourceLockAdapter(mock_redis),
        dag_resolver=DAGResolver(),
        template_resolver=TemplateResolver(),
        retry_policy=RetryPolicy(max_retries=0, backoff_base_s=0),
    )
