"""
Plan Service - Core Business Logic for PlanLibrary

Coordinates plan storage, canonicalization, signature verification,
and embedding generation. Returns data in Evidence Item format.

Reference: LLD.md, tasks.md T200
"""

import asyncio
import logging
import time
from datetime import datetime
from typing import Any
from uuid import uuid4

from shared.database.error_handler import DatabaseIntegrityError
from shared.schemas.evidence import EvidenceItem

from ..adapters.db import DatabaseAdapter
from ..adapters.signature_verifier import SignatureVerifier
from ..domain.models import (
    DuplicatePlanError,
    InvalidSignatureError,
    PlanDB,
    PlanMetricsDB,
    PlanNotFoundError,
    PlanOutcomeDB,
    PlanTooLargeError,
    StorePlanResponse,
    canonicalize_plan,
    compute_plan_hash,
    MAX_PLAN_SIZE_BYTES,
    MAX_STEP_COUNT,
    ULID_PATTERN,
)
from .evidence_service import EvidenceService

logger = logging.getLogger(__name__)


class PlanService:
    """
    Core business logic for plan storage and retrieval.

    Coordinates database, signature verification, and embedding operations.
    Returns plan data in Evidence Item format for ContextRAG integration.
    """

    def __init__(
        self,
        db_adapter: DatabaseAdapter,
        vector_service: Any = None,
        signature_verifier: SignatureVerifier | None = None,
    ) -> None:
        """
        Initialize plan service with adapters.

        Args:
            db_adapter: Database operations
            vector_service: Vector/embedding operations (optional)
            signature_verifier: Signature verification (optional)
        """
        self.db = db_adapter
        self.vector_service = vector_service
        self.signature_verifier = signature_verifier or SignatureVerifier()
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
        3. Verify Ed25519 signature
        4. Check for duplicate plan_id
        5. Check size limits (100 steps, 1MB)
        6. Canonicalize plan JSON
        7. Compute SHA-256 hash
        8. Store plan + outcome + metrics in single DB transaction
        9. Queue async embedding generation

        Args:
            plan: Plan data with plan_id, graph, meta
            signature: Ed25519 signature data
            outcome: Execution outcome data
            metrics: Performance metrics data

        Returns:
            StorePlanResponse with plan_id and stored_at

        Raises:
            InvalidSignatureError: If signature verification fails
            DuplicatePlanError: If plan_id already exists
            PlanTooLargeError: If plan exceeds limits
            ValueError: If plan data is malformed
        """
        start_time = time.time()
        plan_id = plan.get("plan_id", "")

        # Decision Rule 1: Validate plan_id is valid ULID
        if not plan_id or not ULID_PATTERN.match(plan_id):
            raise ValueError(f"Invalid plan_id: must be valid ULID format")

        # Decision Rule 2: Validate required fields
        required_fields = {"plan_id", "graph", "meta"}
        missing = required_fields - set(plan.keys())
        if missing:
            raise ValueError(f"Plan missing required fields: {missing}")

        # Decision Rule 3: Verify signature
        self.signature_verifier.verify_signature(plan, signature)

        # Decision Rule 5: Check size limits
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
                reason=(
                    f"Plan size {size_bytes} bytes exceeds "
                    f"maximum {MAX_PLAN_SIZE_BYTES}"
                ),
            )

        # Decision Rule 7: Compute SHA-256 hash
        plan_hash = compute_plan_hash(canonical)

        # Verify determinism: same input -> same hash
        assert compute_plan_hash(canonical) == plan_hash

        # Extract intent type from meta
        meta = plan.get("meta", {})
        intent_type = meta.get("intent_type", "unknown")
        created_at_str = meta.get("created_at")
        created_at = (
            datetime.fromisoformat(created_at_str)
            if created_at_str
            else datetime.utcnow()
        )

        now = datetime.utcnow()

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

        outcome_db = PlanOutcomeDB(
            outcome_id=uuid4(),
            plan_id=plan_id,
            success=outcome.get("success", False),
            error_type=outcome.get("error_type"),
            error_details=outcome.get("error_details"),
            execution_start=datetime.fromisoformat(
                outcome["execution_start"]
            ),
            execution_end=datetime.fromisoformat(
                outcome["execution_end"]
            ),
            total_steps=outcome.get("total_steps", step_count),
            failed_step=outcome.get("failed_step"),
            context_data=outcome.get("context_data"),
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

        # Decision Rule 9: Queue async embedding generation
        embedding_queued = False
        if self.vector_service is not None:
            try:
                plan_text = f"{intent_type}: {canonical[:500]}"
                asyncio.create_task(
                    self.vector_service.queue_embedding_generation(
                        plan_id=plan_id,
                        plan_text=plan_text,
                    )
                )
                embedding_queued = True
            except Exception as e:
                logger.warning(
                    "Failed to queue embedding generation",
                    extra={
                        "plan_id": plan_id,
                        "error": str(e),
                        "component": "PlanLibrary",
                    },
                )

        latency_ms = (time.time() - start_time) * 1000
        logger.info(
            "Plan stored successfully",
            extra={
                "plan_id": plan_id,
                "intent_type": intent_type,
                "step_count": step_count,
                "storage_latency_ms": round(latency_ms, 2),
                "embedding_queued": embedding_queued,
                "component": "PlanLibrary",
                "operation": "store_plan",
            },
        )

        return StorePlanResponse(
            plan_id=plan_id,
            stored_at=now,
            embedding_queued=embedding_queued,
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
            "Plans queried by intent",
            extra={
                "intent_type": intent_type,
                "result_count": len(evidence_items),
                "latency_ms": round(latency_ms, 2),
                "component": "PlanLibrary",
                "operation": "get_plans_by_intent",
            },
        )

        return evidence_items

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
