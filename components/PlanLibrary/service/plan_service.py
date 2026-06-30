"""
Plan Service - Core Business Logic for PlanLibrary

Coordinates plan storage, canonicalization, and embedding generation.
Returns data in Evidence Item format.

Reference: LLD.md, tasks.md T200
"""

import copy
import logging
import re
import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import ulid

from shared.database.error_handler import DatabaseIntegrityError
from shared.schemas.evidence import EvidenceItem
from shared.schemas.intent import Intent
from shared.schemas.plan import Plan, PlanConstraints, PlanMeta, PlanStep

from ..adapters.db import DatabaseAdapter
from ..domain.models import (
    MAX_PLAN_SIZE_BYTES,
    MAX_STEP_COUNT,
    ULID_PATTERN,
    DuplicatePlanError,
    PlanDB,
    PlanMetricsDB,
    PlanNotFoundError,
    PlanOutcomeDB,
    PlanTooLargeError,
    StorePlanResponse,
    canonicalize_plan,
    compute_plan_hash,
)
from .evidence_service import EvidenceService

logger = logging.getLogger(__name__)


class PlanService:
    """
    Core business logic for plan storage and retrieval.

    Coordinates database and embedding operations.
    Returns plan data in Evidence Item format for ContextRAG integration.
    """

    def __init__(
        self,
        db_adapter: DatabaseAdapter,
    ) -> None:
        """
        Initialize plan service with adapters.

        Args:
            db_adapter: Database operations
        """
        self.db = db_adapter
        self.evidence_service = EvidenceService()
        logger.info(
            "Plan service initialized",
            extra={"component": "PlanLibrary"},
        )

    async def store_plan(
        self,
        plan: dict[str, Any],
        signature: dict[str, Any],
        outcome: dict[str, Any],
        metrics: dict[str, Any],
    ) -> StorePlanResponse:
        """
        Store executed plan with outcome and metrics.

        Decision rules applied top-to-bottom per SPEC:
        1. Validate plan_id is valid ULID
        2. Validate required fields (plan_id, graph, meta)
        3. Check for duplicate plan_id
        4. Check size limits (100 steps, 1MB)
        5. Canonicalize plan JSON
        6. Compute SHA-256 hash
        7. Store plan + outcome + metrics in single DB transaction
        8. Queue async embedding generation

        Args:
            plan: Plan data with plan_id, graph, meta
            signature: Legacy field (unused, pass empty dict)
            outcome: Execution outcome data
            metrics: Performance metrics data

        Returns:
            StorePlanResponse with plan_id and stored_at

        Raises:
            DuplicatePlanError: If plan_id already exists
            PlanTooLargeError: If plan exceeds limits
            ValueError: If plan data is malformed
        """
        start_time = time.time()
        plan_id = plan.get("plan_id", "")

        # Decision Rule 1: Validate plan_id is valid ULID
        if not plan_id or not ULID_PATTERN.match(plan_id):
            raise ValueError("Invalid plan_id: must be valid ULID format")

        # Decision Rule 2: Validate required fields
        required_fields = {"plan_id", "graph", "meta"}
        missing = required_fields - set(plan.keys())
        if missing:
            raise ValueError(f"Plan missing required fields: {missing}")

        # Decision Rule 3: Check size limits
        graph = plan.get("graph", [])
        step_count = len(graph) if isinstance(graph, list) else 0
        if step_count > MAX_STEP_COUNT:
            raise PlanTooLargeError(
                plan_id=plan_id,
                reason=f"Plan has {step_count} steps, maximum is {MAX_STEP_COUNT}",
            )

        # Decision Rule 6: Canonicalize plan JSON
        canonical = canonicalize_plan(plan)
        size_bytes = len(canonical.encode("utf-8"))

        if size_bytes > MAX_PLAN_SIZE_BYTES:
            raise PlanTooLargeError(
                plan_id=plan_id,
                reason=(f"Plan size {size_bytes} bytes exceeds maximum {MAX_PLAN_SIZE_BYTES}"),
            )

        # Decision Rule 7: Compute SHA-256 hash
        plan_hash = compute_plan_hash(canonical)

        # Verify determinism: same input -> same hash
        assert compute_plan_hash(canonical) == plan_hash

        # Extract intent type — prefer plan.intent.intent (the canonical
        # location), fall back to meta.intent_type for legacy plans.
        intent_obj = plan.get("intent", {})
        meta = plan.get("meta", {})
        intent_type = (
            intent_obj.get("intent")
            or meta.get("intent_type")
            or "unknown"
        )
        created_at_str = meta.get("created_at")
        if created_at_str:
            created_at = datetime.fromisoformat(created_at_str)
            # Strip tzinfo for TIMESTAMP WITHOUT TIME ZONE columns
            if created_at.tzinfo is not None:
                created_at = created_at.replace(tzinfo=None)
        else:
            created_at = datetime.now(UTC).replace(tzinfo=None)

        now = datetime.now(UTC).replace(tzinfo=None)

        # Build domain models
        plan_db = PlanDB(
            plan_id=plan_id,
            canonical_json=plan,
            signature_data=signature,
            intent_type=intent_type,
            step_count=step_count,
            plan_hash=plan_hash,
            size_bytes=size_bytes,
            created_at=created_at,
            stored_at=now,
        )

        exec_start = datetime.fromisoformat(outcome["execution_start"])
        exec_end = datetime.fromisoformat(outcome["execution_end"])
        if exec_start.tzinfo is not None:
            exec_start = exec_start.replace(tzinfo=None)
        if exec_end.tzinfo is not None:
            exec_end = exec_end.replace(tzinfo=None)

        outcome_db = PlanOutcomeDB(
            outcome_id=uuid4(),
            plan_id=plan_id,
            success=outcome.get("success", False),
            error_type=outcome.get("error_type"),
            error_details=outcome.get("error_details"),
            execution_start=exec_start,
            execution_end=exec_end,
            total_steps=outcome.get("total_steps", step_count),
            failed_step=outcome.get("failed_step"),
            context_data=outcome.get("context_data"),
            final_graph_json=outcome.get("final_graph_json"),
            plan_revision=outcome.get("plan_revision", 0),
        )

        metrics_db = PlanMetricsDB(
            metrics_id=uuid4(),
            plan_id=plan_id,
            preview_latency_ms=metrics.get("preview_latency_ms"),
            execute_latency_ms=metrics.get("execute_latency_ms", 0),
            step_timings=metrics.get("step_timings"),
            resource_usage=metrics.get("resource_usage"),
        )

        # Decision Rule 4 + 8: Store in single DB transaction
        # (duplicate check via DB unique constraint)
        try:
            await self.db.store_plan_transaction(
                plan=plan_db, outcome=outcome_db, metrics=metrics_db
            )
        except DatabaseIntegrityError as e:
            if "duplicate" in str(e).lower() or "unique" in str(e).lower():
                raise DuplicatePlanError(plan_id=plan_id)
            raise

        latency_ms = (time.time() - start_time) * 1000
        logger.info(
            "Plan stored successfully",
            extra={
                "plan_id": plan_id,
                "intent_type": intent_type,
                "step_count": step_count,
                "storage_latency_ms": round(latency_ms, 2),
                "component": "PlanLibrary",
                "operation": "store_plan",
            },
        )

        return StorePlanResponse(
            plan_id=plan_id,
            stored_at=now,
        )

    async def get_plans_by_intent(
        self,
        intent_type: str,
        success_threshold: float = 0.7,
        limit: int = 50,
        recency_days: int | None = None,
    ) -> list[EvidenceItem]:
        """
        Query plans by intent type with success filtering.

        Returns results as Evidence Items (type="plan", tier=3).
        Sorted by success_rate DESC, total_executions DESC.

        Args:
            intent_type: Intent type to filter by
            success_threshold: Minimum success rate (default 0.7)
            limit: Maximum results (default 50)
            recency_days: Optional recency filter

        Returns:
            List of Evidence Items
        """
        start_time = time.time()

        plans = await self.db.get_plans_by_intent(
            intent_type=intent_type,
            success_threshold=success_threshold,
            limit=limit,
            recency_days=recency_days,
        )

        evidence_items = self.evidence_service.to_evidence_items(plans)

        latency_ms = (time.time() - start_time) * 1000
        logger.info(
            "plans_queried_by_intent intent=%s result_count=%d latency_ms=%.1f",
            intent_type, len(evidence_items), latency_ms,
        )

        return evidence_items

    async def get_all_plans(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return all plans with their latest outcome."""
        return await self.db.get_all_plans(limit=limit)

    async def get_plan_by_id(self, plan_id: str) -> PlanDB | None:
        """
        Retrieve specific plan by ID.

        Args:
            plan_id: ULID plan identifier

        Returns:
            PlanDB if found, None if not found
        """
        plan = await self.db.get_plan_by_id(plan_id)

        if plan is None:
            logger.info(
                "Plan not found",
                extra={
                    "plan_id": plan_id,
                    "component": "PlanLibrary",
                    "operation": "get_plan_by_id",
                },
            )

        return plan

    async def get_plans_by_user(
        self,
        user_id: str,
        limit: int = 50,
        success_only: bool = False,
    ) -> list[dict[str, Any]]:
        """Return plans for a specific user."""
        return await self.db.get_plans_by_user(
            user_id=user_id, limit=limit, success_only=success_only
        )

    async def clone_plan_for_rerun(
        self,
        source_plan_id: str,
        fresh_entities: dict[str, Any],
        user_id: str,
        trace_id: str,
        constraints_override: dict[str, Any] | None = None,
    ) -> Plan:
        """Clone a previously executed plan with fresh entities for rerun.

        Reuses the plan's DAG structure (steps, roles, tools, dependencies)
        but replaces all entities and resets step statuses.

        Args:
            source_plan_id: ULID of the plan to clone
            fresh_entities: New entities to substitute into the plan
            user_id: User requesting the rerun
            trace_id: New trace ID for this execution
            constraints_override: Optional constraint overrides (replaces original)

        Returns:
            A new Plan ready for preview and execution

        Raises:
            PlanNotFoundError: If source plan does not exist
        """
        plan_db = await self.db.get_plan_by_id(source_plan_id)
        if plan_db is None:
            raise PlanNotFoundError(plan_id=source_plan_id)

        source_plan = Plan.model_validate(plan_db.canonical_json)

        new_plan_id = ulid.new().str

        # Build new intent: same type, fresh entities
        new_intent = Intent(
            intent=source_plan.intent.intent,
            entities=fresh_entities,
            constraints=constraints_override if constraints_override is not None else source_plan.intent.constraints,
            tz=source_plan.intent.tz,
            user_id=user_id,
            trace_id=trace_id,
            session_id=source_plan.intent.session_id,
        )

        # Clone graph steps: reset status, apply fresh entities
        cloned_steps: list[PlanStep] = []
        for step in source_plan.graph:
            step_data = step.model_dump()
            step_data["status"] = "pending"
            step_data["result"] = None
            step_data["error"] = None
            step_data["dry_run"] = True

            # Substitute {{entities.X}} patterns in args values
            if step_data.get("args"):
                step_data["args"] = self._substitute_entity_args(
                    step_data["args"], fresh_entities
                )

            cloned_steps.append(PlanStep.model_validate(step_data))

        # Build new constraints
        if constraints_override is not None:
            new_constraints = PlanConstraints.model_validate(constraints_override)
        else:
            new_constraints = source_plan.constraints.model_copy()

        # Build new meta
        now_iso = datetime.now(UTC).isoformat()
        # Compute canonical hash from the new plan content
        preliminary_plan_data = {
            "plan_id": new_plan_id,
            "intent": new_intent.model_dump(),
            "graph": [s.model_dump() for s in cloned_steps],
            "constraints": new_constraints.model_dump(),
        }
        canonical = canonicalize_plan(preliminary_plan_data)
        new_hash = compute_plan_hash(canonical)

        new_meta = PlanMeta(
            created_at=now_iso,
            author="planner@system",
            canonical_hash=new_hash,
            rerun_source=source_plan_id,
        )

        new_plan = Plan(
            plan_id=new_plan_id,
            intent=new_intent,
            trace_id=trace_id,
            graph=cloned_steps,
            constraints=new_constraints,
            plugins=source_plan.plugins.copy(),
            meta=new_meta,
            plan_revision=0,
        )

        logger.info(
            "Plan cloned for rerun",
            extra={
                "source_plan_id": source_plan_id,
                "new_plan_id": new_plan_id,
                "component": "PlanLibrary",
                "operation": "clone_plan_for_rerun",
            },
        )

        return new_plan

    @staticmethod
    def _substitute_entity_args(
        args: dict[str, Any], entities: dict[str, Any]
    ) -> dict[str, Any]:
        """Replace ``{{entities.X}}`` placeholders in step args with fresh entity values."""
        pattern = re.compile(r"\{\{entities\.(\w+)\}\}")
        result: dict[str, Any] = {}
        for key, value in args.items():
            if isinstance(value, str):
                def _replacer(match: re.Match) -> str:
                    entity_key = match.group(1)
                    return str(entities.get(entity_key, match.group(0)))

                result[key] = pattern.sub(_replacer, value)
            else:
                result[key] = copy.deepcopy(value)
        return result
