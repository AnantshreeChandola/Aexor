"""
Unit Tests for ExecuteOrchestrator

Domain models, DAG resolver, template resolver, resource lock,
credential vault, MCP client, retry adapter, and API routes.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shared.schemas.plan import PlanStep

from ..adapters.dag_resolver import DAGResolver
from ..adapters.resource_lock import ResourceLockAdapter
from ..adapters.retry import RetryPolicy
from ..adapters.template_resolver import TemplateResolver
from ..domain.models import (
    ApprovalTokenError,
    CompensationRecord,
    CycleDetectedError,
    ExecuteRequest,
    ExecutionContext,
    IdempotencyConflict,
    MCPInvocationError,
    PlanExpiredError,
    RecoveryExhaustedError,
    ResourceLockTimeout,
    SpawnDeniedError,
    StepExecutionError,
    StepResult,
)

# ======================================================================
# Domain Model Tests (T102)
# ======================================================================


class TestExecuteRequest:
    def test_required_fields(self, sample_plan):
        req = ExecuteRequest(
            plan=sample_plan,
            approval_token="tok",
            user_id="u1",
            trace_id="t1",
        )
        assert req.user_id == "u1"
        assert req.preview_state is None
        assert req.integration_credentials == {}

    def test_with_preview_state(self, sample_plan):
        req = ExecuteRequest(
            plan=sample_plan,
            approval_token="tok",
            user_id="u1",
            trace_id="t1",
            preview_state={"1": {"events": []}},
        )
        assert req.preview_state["1"]["events"] == []

    def test_with_credentials(self, sample_plan):
        req = ExecuteRequest(
            plan=sample_plan,
            approval_token="tok",
            user_id="u1",
            trace_id="t1",
            integration_credentials={"google.calendar": "cred-123"},
        )
        assert req.integration_credentials["google.calendar"] == "cred-123"


class TestStepResult:
    def test_completed(self):
        sr = StepResult(step=1, status="completed", result={"id": "x"})
        assert sr.status == "completed"
        assert sr.latency_ms == 0

    def test_failed(self):
        sr = StepResult(step=2, status="failed", error={"msg": "err"})
        assert sr.error["msg"] == "err"

    def test_skipped(self):
        sr = StepResult(step=3, status="skipped")
        assert sr.result is None


class TestCompensationRecord:
    def test_with_compensation(self):
        cr = CompensationRecord(
            step=1,
            tool_id="t",
            operation="create",
            result={"id": "x"},
            compensation_operation="delete",
            compensation_args={"id": "x"},
        )
        assert cr.compensation_operation == "delete"

    def test_without_compensation(self):
        cr = CompensationRecord(
            step=1,
            tool_id="t",
            operation="create",
            result={"id": "x"},
        )
        assert cr.compensation_operation is None


class TestExecutionContext:
    def test_initialization(self, sample_plan):
        ctx = ExecutionContext(plan=sample_plan, user_id="u1", trace_id="t1")
        assert ctx.step_results == {}
        assert ctx.compensation_stack == []
        assert ctx.spawned_steps == []
        assert ctx.attestations == []
        assert ctx.plan_revision == 0
        assert ctx.recovery_action_count == 0

    def test_mutability(self, sample_plan):
        ctx = ExecutionContext(plan=sample_plan, user_id="u1", trace_id="t1")
        ctx.plan_revision = 2
        ctx.recovery_action_count = 3
        assert ctx.plan_revision == 2


class TestErrorClasses:
    def test_approval_error(self):
        e = ApprovalTokenError("expired")
        assert e.reason == "expired"

    def test_plan_expired(self):
        e = PlanExpiredError("P" * 26, 900)
        assert e.plan_id == "P" * 26
        assert e.ttl_s == 900

    def test_step_execution_error(self):
        e = StepExecutionError(3, "timeout", retries=2)
        assert e.step == 3
        assert e.retries == 2

    def test_idempotency_conflict(self):
        e = IdempotencyConflict("idem:key")
        assert e.key == "idem:key"

    def test_resource_lock_timeout(self):
        e = ResourceLockTimeout("lock:key", 30)
        assert e.timeout_s == 30

    def test_mcp_invocation_error(self):
        e = MCPInvocationError("srv", "tool", "503")
        assert e.server == "srv"
        assert e.tool == "tool"

    def test_spawn_denied(self):
        e = SpawnDeniedError("limit", ["v1"])
        assert e.violations == ["v1"]

    def test_recovery_exhausted(self):
        e = RecoveryExhaustedError(5, 3)
        assert e.step == 5
        assert e.attempts == 3

    def test_cycle_detected(self):
        e = CycleDetectedError("steps 1,2")
        assert "steps 1,2" in str(e)


# ======================================================================
# DAG Resolver Tests (T201)
# ======================================================================


class TestDAGResolver:
    def setup_method(self):
        self.resolver = DAGResolver()

    def _step(self, num, after=None):
        return PlanStep(
            step=num,
            mode="interactive",
            role="Fetcher",
            uses="test.tool",
            call="op",
            after=after or [],
        )

    def test_linear_chain(self):
        graph = [self._step(1), self._step(2, [1]), self._step(3, [2])]
        levels = self.resolver.resolve(graph)
        assert len(levels) == 3
        assert [s.step for s in levels[0]] == [1]
        assert [s.step for s in levels[1]] == [2]
        assert [s.step for s in levels[2]] == [3]

    def test_parallel_steps(self):
        graph = [
            self._step(1),
            self._step(2),
            self._step(3, [1, 2]),
        ]
        levels = self.resolver.resolve(graph)
        assert len(levels) == 2
        assert sorted(s.step for s in levels[0]) == [1, 2]
        assert [s.step for s in levels[1]] == [3]

    def test_diamond_shape(self):
        graph = [
            self._step(1),
            self._step(2, [1]),
            self._step(3, [1]),
            self._step(4, [2, 3]),
        ]
        levels = self.resolver.resolve(graph)
        assert len(levels) == 3

    def test_cycle_detected(self):
        graph = [
            self._step(1, [2]),
            self._step(2, [1]),
        ]
        with pytest.raises(CycleDetectedError):
            self.resolver.resolve(graph)

    def test_single_step(self):
        graph = [self._step(1)]
        levels = self.resolver.resolve(graph)
        assert len(levels) == 1

    def test_empty_graph(self):
        with pytest.raises(ValueError, match="at least one step"):
            self.resolver.resolve([])


# ======================================================================
# Template Resolver Tests (T211)
# ======================================================================


class TestTemplateResolver:
    def setup_method(self):
        self.resolver = TemplateResolver()

    def test_simple_template(self):
        results = {
            1: StepResult(step=1, status="completed", result={"event_id": "e1"}),
        }
        args = {"id": "{{step_1.result.event_id}}"}
        resolved = self.resolver.resolve(args, results)
        assert resolved["id"] == "e1"

    def test_nested_path(self):
        results = {
            2: StepResult(step=2, status="completed", result={"data": {"name": "Alice"}}),
        }
        args = {"name": "{{step_2.result.data.name}}"}
        resolved = self.resolver.resolve(args, results)
        assert resolved["name"] == "Alice"

    def test_no_template_passthrough(self):
        args = {"plain": "value", "num": 42}
        resolved = self.resolver.resolve(args, {})
        assert resolved == args

    def test_missing_step_raises(self):
        args = {"id": "{{step_99.result.field}}"}
        with pytest.raises(KeyError, match="Step 99"):
            self.resolver.resolve(args, {})

    def test_preview_state_template(self):
        preview = {"1": {"selected": "optA"}}
        args = {"choice": "{{preview.cached_state.step_1_result.selected}}"}
        resolved = self.resolver.resolve(args, {}, preview)
        assert resolved["choice"] == "optA"

    def test_multiple_templates(self):
        results = {
            1: StepResult(step=1, status="completed", result={"a": "v1"}),
            2: StepResult(step=2, status="completed", result={"b": "v2"}),
        }
        args = {
            "x": "{{step_1.result.a}}",
            "y": "{{step_2.result.b}}",
        }
        resolved = self.resolver.resolve(args, results)
        assert resolved == {"x": "v1", "y": "v2"}


# ======================================================================
# Resource Lock Tests (T231)
# ======================================================================


class TestResourceLock:
    @pytest.fixture()
    def mock_redis(self):
        r = AsyncMock()
        r.set = AsyncMock(return_value=True)
        r.get = AsyncMock(return_value=None)
        r.delete = AsyncMock(return_value=1)
        return r

    async def test_acquire_succeeds(self, mock_redis):
        lock = ResourceLockAdapter(mock_redis)
        result = await lock.acquire("lock:test")
        assert result is True

    async def test_acquire_timeout(self, mock_redis):
        mock_redis.set = AsyncMock(return_value=False)
        lock = ResourceLockAdapter(mock_redis)
        with pytest.raises(ResourceLockTimeout):
            await lock.acquire("lock:test", timeout_s=0.5)

    async def test_release(self, mock_redis):
        lock = ResourceLockAdapter(mock_redis)
        await lock.acquire("lock:test")
        owner = lock._held_locks["lock:test"]
        mock_redis.get = AsyncMock(return_value=owner)
        await lock.release("lock:test")
        mock_redis.delete.assert_called()

    async def test_release_not_owned(self, mock_redis):
        lock = ResourceLockAdapter(mock_redis)
        await lock.release("lock:unknown")
        mock_redis.delete.assert_not_called()


# ======================================================================
# Retry Adapter Tests (T271)
# ======================================================================


class TestRetryPolicy:
    async def test_succeeds_first_try(self):
        policy = RetryPolicy(max_retries=3, backoff_base_s=0)
        step = MagicMock()
        step.step = 1
        op = AsyncMock(return_value={"ok": True})
        result = await policy.execute_with_retry(op, step)
        assert result == {"ok": True}
        assert op.await_count == 1

    async def test_fails_then_succeeds(self):
        policy = RetryPolicy(max_retries=3, backoff_base_s=0)
        step = MagicMock()
        step.step = 1
        call_count = 0

        async def op():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise MCPInvocationError("srv", "tool", "503")
            return {"ok": True}

        result = await policy.execute_with_retry(op, step)
        assert result == {"ok": True}
        assert call_count == 3

    async def test_all_retries_fail(self):
        policy = RetryPolicy(max_retries=2, backoff_base_s=0)
        step = MagicMock()
        step.step = 1

        async def op():
            raise MCPInvocationError("srv", "tool", "503")

        with pytest.raises(MCPInvocationError):
            await policy.execute_with_retry(op, step)

    async def test_non_retryable_error(self):
        policy = RetryPolicy(max_retries=3, backoff_base_s=0)
        step = MagicMock()
        step.step = 1

        async def op():
            raise MCPInvocationError("srv", "tool", "400 bad request")

        with pytest.raises(MCPInvocationError):
            await policy.execute_with_retry(op, step)

    async def test_non_mcp_error_not_retried(self):
        policy = RetryPolicy(max_retries=3, backoff_base_s=0)
        step = MagicMock()
        step.step = 1

        async def op():
            raise ValueError("bad")

        with pytest.raises(ValueError):
            await policy.execute_with_retry(op, step)


# ======================================================================
# MCP Client Tests (T251)
# ======================================================================


class TestMCPClient:
    """Tests for the rewritten MCPClientAdapter using config + session manager."""

    def _make_adapter(self, mock_http, session_id="ses-1"):

        from shared.mcp.config import MCPConfigRegistry, MCPServerConfig
        from shared.mcp.session import MCPSession, MCPSessionManager

        from ..adapters.mcp_client import MCPClientAdapter

        cfg = MCPServerConfig(name="srv", url="http://srv.test/mcp", api_key="k")
        registry = MCPConfigRegistry({"srv": cfg})
        mgr = AsyncMock(spec=MCPSessionManager)
        mgr.get_session = AsyncMock(
            return_value=MCPSession(server_name="srv", session_id=session_id)
        )
        return MCPClientAdapter(config=registry, http_client=mock_http, session_manager=mgr)

    async def test_invoke_success(self):
        import httpx

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.post = AsyncMock(
            return_value=httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "result": {"id": "x"}}
            )
        )
        client = self._make_adapter(mock_http)
        result = await client.invoke("srv", "tool", {"a": 1})
        assert result == {"id": "x"}

    async def test_invoke_http_error(self):
        import httpx

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.post = AsyncMock(
            return_value=httpx.Response(503, text="Service Unavailable")
        )
        client = self._make_adapter(mock_http)
        with pytest.raises(MCPInvocationError):
            await client.invoke("srv", "tool", {})

    async def test_invoke_timeout(self):
        import httpx

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        client = self._make_adapter(mock_http)
        with pytest.raises(MCPInvocationError, match="timeout"):
            await client.invoke("srv", "tool", {})


# ======================================================================
# Credential Vault Tests (T241)
# ======================================================================


class TestCredentialVault:
    async def test_decrypt_missing_master_key(self):
        from ..adapters.credential_vault import (
            CredentialVaultAdapter,
            CredentialVaultError,
        )

        with patch.dict("os.environ", {}, clear=True):
            vault = CredentialVaultAdapter(db=AsyncMock())
            with pytest.raises(CredentialVaultError, match="not configured"):
                await vault.decrypt("cred-1", "user-1")

    async def test_decrypt_not_found(self):
        from ..adapters.credential_vault import (
            CredentialVaultAdapter,
            CredentialVaultError,
        )

        key_hex = "00" * 32
        with patch.dict("os.environ", {"CREDENTIAL_MASTER_KEY": key_hex}):
            vault = CredentialVaultAdapter(db=AsyncMock())
            vault._fetch_record = AsyncMock(return_value=None)
            with pytest.raises(CredentialVaultError, match="not found"):
                await vault.decrypt("cred-1", "user-1")

    async def test_decrypt_user_mismatch(self):
        from ..adapters.credential_vault import (
            CredentialVaultAdapter,
            CredentialVaultError,
        )

        key_hex = "00" * 32
        with patch.dict("os.environ", {"CREDENTIAL_MASTER_KEY": key_hex}):
            vault = CredentialVaultAdapter(db=AsyncMock())
            vault._fetch_record = AsyncMock(
                return_value={
                    "encrypted_value": b"x",
                    "iv": b"y",
                    "key_version": 1,
                    "user_id": "other-user",
                }
            )
            with pytest.raises(CredentialVaultError, match="mismatch"):
                await vault.decrypt("cred-1", "user-1")

    async def test_decrypt_success(self):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        from ..adapters.credential_vault import CredentialVaultAdapter

        key = AESGCM.generate_key(bit_length=256)
        key_hex = key.hex()
        iv = b"0" * 12
        aesgcm = AESGCM(key)
        encrypted = aesgcm.encrypt(iv, b"my-secret", None)

        with patch.dict("os.environ", {"CREDENTIAL_MASTER_KEY": key_hex}):
            vault = CredentialVaultAdapter(db=AsyncMock())
            vault._fetch_record = AsyncMock(
                return_value={
                    "encrypted_value": encrypted,
                    "iv": iv,
                    "key_version": 1,
                    "user_id": "user-1",
                }
            )
            result = await vault.decrypt("cred-1", "user-1")
            assert result == "my-secret"
