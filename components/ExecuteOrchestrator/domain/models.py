"""
ExecuteOrchestrator Domain Models

Pydantic models for execution request/response, step results,
compensation records, and mutable execution context.
Custom exceptions for domain-specific error handling.

Reference: LLD.md Sections 5.1, 5.2
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from shared.schemas.plan import Plan, PlanStep
from shared.schemas.policy import PolicyAttestation

# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------


class ExecuteRequest(BaseModel):
    """Input contract for plan execution."""

    plan: Plan
    approval_token: str = Field(..., min_length=1)
    user_id: str = Field(..., min_length=1)
    trace_id: str = Field(..., min_length=1)
    preview_state: dict[str, Any] | None = None
    integration_credentials: dict[str, str] = Field(default_factory=dict)


class StepResult(BaseModel):
    """Result of executing a single step."""

    step: int
    status: Literal["completed", "failed", "skipped"]
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    latency_ms: int = 0
    retries: int = 0


class CompensationRecord(BaseModel):
    """Undo info for a completed Booker step."""

    step: int
    tool_id: str
    operation: str
    result: dict[str, Any]
    compensation_operation: str | None = None
    compensation_args: dict[str, Any] | None = None


class ExecutionContext:
    """Mutable runtime state (not a Pydantic model -- internal only)."""

    def __init__(
        self,
        plan: Plan,
        user_id: str,
        trace_id: str,
    ) -> None:
        self.plan = plan
        self.user_id = user_id
        self.trace_id = trace_id
        self.step_results: dict[int, StepResult] = {}
        self.compensation_stack: list[CompensationRecord] = []
        self.spawned_steps: list[PlanStep] = []
        self.attestations: list[PolicyAttestation] = []
        self.plan_revision: int = 0
        self.recovery_action_count: int = 0


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class ExecuteError(Exception):
    """Base error for ExecuteOrchestrator."""


class ApprovalTokenError(ExecuteError):
    """Approval token invalid or expired."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Approval token error: {reason}")


class PlanExpiredError(ExecuteError):
    """Plan TTL exceeded."""

    def __init__(self, plan_id: str, ttl_s: int) -> None:
        self.plan_id = plan_id
        self.ttl_s = ttl_s
        super().__init__(f"Plan {plan_id} expired (TTL {ttl_s}s)")


class StepExecutionError(ExecuteError):
    """Step failed after retries."""

    def __init__(self, step: int, reason: str, retries: int = 0) -> None:
        self.step = step
        self.retries = retries
        super().__init__(f"Step {step} failed: {reason}")


class IdempotencyConflict(ExecuteError):
    """Another execution owns this idempotency slot."""

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(f"Idempotency conflict: {key}")


class ResourceLockTimeout(ExecuteError):
    """Could not acquire resource lock within timeout."""

    def __init__(self, lock_key: str, timeout_s: int) -> None:
        self.lock_key = lock_key
        self.timeout_s = timeout_s
        super().__init__(f"Lock timeout ({timeout_s}s): {lock_key}")


class MCPInvocationError(ExecuteError):
    """MCP tool invocation failed."""

    def __init__(self, server: str, tool: str, reason: str) -> None:
        self.server = server
        self.tool = tool
        super().__init__(f"MCP error ({server}/{tool}): {reason}")


class SpawnDeniedError(ExecuteError):
    """PolicyEngine denied a spawn request."""

    def __init__(self, reason: str, violations: list[str] | None = None) -> None:
        self.violations = violations or []
        super().__init__(f"Spawn denied: {reason}")


class RecoveryExhaustedError(ExecuteError):
    """All recovery attempts exhausted."""

    def __init__(self, step: int, attempts: int) -> None:
        self.step = step
        self.attempts = attempts
        super().__init__(f"Recovery exhausted for step {step} after {attempts} attempts")


class CycleDetectedError(ExecuteError):
    """Circular dependencies in plan graph."""

    def __init__(self, details: str = "cycle in step graph") -> None:
        self.details = details
        super().__init__(f"Cycle detected: {details}")


class IntegrationNotConnectedError(ExecuteError):
    """A required integration (e.g. Notion, Gmail) is not connected on Composio."""

    def __init__(self, provider: str, step: int | None = None) -> None:
        self.provider = provider
        self.step = step
        super().__init__(f"{provider} not connected on Composio")


class GateApprovalRequired(ExecuteError):
    """Execution paused — a gated step requires user approval before continuing."""

    def __init__(
        self,
        gate_id: str,
        step: int,
        context_data: dict[str, Any] | None = None,
        partial_results: dict[str, Any] | None = None,
    ) -> None:
        self.gate_id = gate_id
        self.step = step
        self.context_data = context_data or {}
        self.partial_results = partial_results or {}
        super().__init__(f"Approval required for gate {gate_id} at step {step}")
