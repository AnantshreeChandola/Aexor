"""
Saga Compensation Tests

Tests for reverse-order compensation execution when steps fail.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from ..domain.models import (
    CompensationRecord,
    ExecutionContext,
)


class TestCompensation:
    @pytest.fixture()
    def ctx(self, sample_plan):
        return ExecutionContext(plan=sample_plan, user_id="u1", trace_id="t1")

    async def test_reverse_order(self, execute_service, ctx):
        """Compensation runs in reverse order: step 2 before step 1."""
        ctx.compensation_stack = [
            CompensationRecord(
                step=1,
                tool_id="google.calendar",
                operation="create_event",
                result={"id": "e1"},
                compensation_operation="delete_event",
                compensation_args={"event_id": "e1"},
            ),
            CompensationRecord(
                step=2,
                tool_id="google.calendar",
                operation="create_event",
                result={"id": "e2"},
                compensation_operation="delete_event",
                compensation_args={"event_id": "e2"},
            ),
        ]

        call_order = []

        async def track_invoke(server, tool, args, **kwargs):
            call_order.append(args.get("event_id"))
            return {"status": "deleted"}

        execute_service._mcp.invoke = AsyncMock(side_effect=track_invoke)
        await execute_service._run_compensation(ctx)

        # Step 2 compensated first, then step 1
        assert call_order == ["e2", "e1"]

    async def test_skip_no_compensation_operation(self, execute_service, ctx):
        """Steps without compensation_operation are skipped."""
        ctx.compensation_stack = [
            CompensationRecord(
                step=1,
                tool_id="t",
                operation="op",
                result={"id": "x"},
                compensation_operation=None,
            ),
        ]
        await execute_service._run_compensation(ctx)
        execute_service._mcp.invoke.assert_not_called()

    async def test_compensation_failure_logged_not_raised(self, execute_service, ctx):
        """Failed compensation is logged but does not stop others."""
        ctx.compensation_stack = [
            CompensationRecord(
                step=1,
                tool_id="t1",
                operation="op1",
                result={"id": "x1"},
                compensation_operation="undo1",
                compensation_args={"id": "x1"},
            ),
            CompensationRecord(
                step=2,
                tool_id="t2",
                operation="op2",
                result={"id": "x2"},
                compensation_operation="undo2",
                compensation_args={"id": "x2"},
            ),
        ]

        call_count = 0

        async def fail_then_succeed(server, tool, args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("comp failed")
            return {"ok": True}

        execute_service._mcp.invoke = AsyncMock(side_effect=fail_then_succeed)
        # Should not raise
        await execute_service._run_compensation(ctx)
        assert call_count == 2

    async def test_all_compensations_succeed(self, execute_service, ctx):
        """All compensation operations execute successfully."""
        ctx.compensation_stack = [
            CompensationRecord(
                step=1,
                tool_id="t",
                operation="op",
                result={"id": "x"},
                compensation_operation="undo",
                compensation_args={"id": "x"},
            ),
        ]
        execute_service._mcp.invoke = AsyncMock(return_value={"status": "undone"})
        await execute_service._run_compensation(ctx)
        execute_service._mcp.invoke.assert_called_once()

    async def test_empty_stack(self, execute_service, ctx):
        """Empty compensation stack is a no-op."""
        ctx.compensation_stack = []
        await execute_service._run_compensation(ctx)
        execute_service._mcp.invoke.assert_not_called()

    async def test_compensation_uses_correct_args(self, execute_service, ctx):
        """Compensation call uses the correct tool and args."""
        ctx.compensation_stack = [
            CompensationRecord(
                step=3,
                tool_id="google.calendar",
                operation="create_event",
                result={"id": "evt-99"},
                compensation_operation="delete_event",
                compensation_args={"event_id": "evt-99"},
            ),
        ]
        execute_service._mcp.invoke = AsyncMock(return_value={"ok": True})
        await execute_service._run_compensation(ctx)

        invoke_call = execute_service._mcp.invoke.call_args
        assert invoke_call.kwargs.get("tool") == "delete_event" or (
            invoke_call[0][1] == "delete_event"
        )

    async def test_outcome_after_compensation(self, execute_service, sample_execute_request):
        """After compensation, PlanOutcome has success=False."""
        # Make the MCP client fail on step 4 (Booker)
        call_count = 0

        async def fail_on_booker(server, tool, args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count >= 4:
                raise RuntimeError("MCP error")
            return {"status": "ok", "id": f"r{call_count}"}

        execute_service._mcp.invoke = AsyncMock(side_effect=fail_on_booker)

        outcome = await execute_service.execute_plan(sample_execute_request)
        assert outcome.success is False
