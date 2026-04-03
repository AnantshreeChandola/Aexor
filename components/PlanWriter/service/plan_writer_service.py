"""
PlanWriter Service

Orchestrates writes to PlanLibrary, History, and VectorIndex after
plan execution. Returns composite PersistResult with status tracking.

Write ordering: PlanLibrary (fatal) -> History (partial) -> VectorIndex (optional)

Reference: LLD.md SS4.1, SS8
"""

import logging
import time
from typing import Any
from uuid import UUID

from components.PlanLibrary.domain.models import DuplicatePlanError
from shared.schemas.metrics import PlanMetrics
from shared.schemas.outcome import PlanOutcome
from shared.schemas.plan import Plan

from ..adapters.fact_deriver import derive_fact
from ..domain.models import (
    BulkPersistResult,
    FactDerivationError,
    PersistResult,
    PlanLibraryWriteError,
)

logger = logging.getLogger("planwriter")


class PlanWriterService:
    """Persists plan execution outcomes to Memory Layer."""

    def __init__(
        self,
        plan_service: Any,
        fact_service: Any,
        vector_index_service: Any | None,
    ) -> None:
        """Initialize with downstream Memory Layer services.

        Args:
            plan_service: PlanLibrary service for plan+outcome storage.
            fact_service: History service for fact storage.
            vector_index_service: VectorIndex service for embedding storage.
                May be None if VectorIndex is unavailable.
        """
        self._plan_service = plan_service
        self._fact_service = fact_service
        self._vector_index_service = vector_index_service

    async def persist_outcome(
        self,
        user_id: UUID,
        plan: Plan,
        outcome: PlanOutcome,
        metrics: PlanMetrics,
    ) -> PersistResult:
        """Persist a completed plan execution to all downstream stores.

        Execution order:
            1. PlanLibrary.store_plan() -- PRIMARY, must succeed
            2. History.store_fact()     -- SECONDARY, partial failure ok
            3. VectorIndex.store_embedding() -- OPTIONAL, graceful degradation

        Args:
            user_id: User UUID for user-scoped facts.
            plan: Typed Plan model.
            outcome: Typed PlanOutcome model.
            metrics: Typed PlanMetrics model.

        Returns:
            PersistResult with plan_id, fact_id, embedding_stored, status.

        Raises:
            PlanLibraryWriteError: If PlanLibrary write fails.
        """
        total_start = time.monotonic()

        plan_id = plan.plan_id
        plan_dict = plan.model_dump()
        signature_dict = {}
        outcome_dict = outcome.model_dump()
        metrics_dict = metrics.model_dump()

        errors: list[str] = []
        fact_id: UUID | None = None
        embedding_stored = False

        # Step 1: PlanLibrary write (PRIMARY -- fatal if fails)
        await self._do_plan_library_write(
            plan_dict,
            signature_dict,
            outcome_dict,
            metrics_dict,
            plan_id,
        )

        # Step 2: Fact derivation + History write (SECONDARY)
        fact_id = await self._write_to_history(
            user_id,
            plan,
            outcome,
            plan_id,
            errors,
        )

        # Step 3: VectorIndex write (OPTIONAL)
        embedding_stored = await self._write_to_vector_index(
            plan_id,
            plan_dict,
            errors,
        )

        # Build result
        status = "ok" if not errors else "partial"
        total_ms = (time.monotonic() - total_start) * 1000

        logger.info(
            "outcome_persisted",
            extra={
                "plan_id": plan_id,
                "fact_id": str(fact_id) if fact_id else None,
                "embedding_stored": embedding_stored,
                "status": status,
                "total_latency_ms": round(total_ms, 2),
                "component": "PlanWriter",
                "op": "persist_outcome",
            },
        )

        return PersistResult(
            plan_id=plan_id,
            fact_id=fact_id,
            embedding_stored=embedding_stored,
            status=status,
            errors=errors,
        )

    async def bulk_persist(
        self,
        user_id: UUID,
        outcomes: list[dict[str, Any]],
    ) -> BulkPersistResult:
        """Persist multiple plan outcomes (backfill/replay).

        Each outcome dict must contain keys: plan, outcome, metrics.
        Processes sequentially; collects individual PersistResults.

        Args:
            user_id: User UUID applied to all outcomes.
            outcomes: List of outcome dicts.

        Returns:
            BulkPersistResult with individual results and summary counts.

        Raises:
            ValueError: If outcomes list is empty.
        """
        if not outcomes:
            raise ValueError("outcomes list must not be empty")

        total_start = time.monotonic()
        results: list[PersistResult] = []
        succeeded = 0
        partial_count = 0
        failed = 0

        for item in outcomes:
            try:
                plan = Plan.model_validate(item.get("plan", {}))
                out = PlanOutcome.model_validate(item.get("outcome", {}))
                met = PlanMetrics.model_validate(item.get("metrics", {}))

                result = await self.persist_outcome(
                    user_id=user_id,
                    plan=plan,
                    outcome=out,
                    metrics=met,
                )
                results.append(result)
                if result.status == "ok":
                    succeeded += 1
                elif result.status == "partial":
                    partial_count += 1
            except PlanLibraryWriteError as exc:
                plan_id = item.get("plan", {}).get("plan_id", "unknown")
                results.append(
                    PersistResult(
                        plan_id=plan_id if len(plan_id) == 26 else "0" * 26,
                        status="error",
                        errors=[str(exc)],
                    )
                )
                failed += 1

        total_ms = (time.monotonic() - total_start) * 1000
        logger.info(
            "bulk_persist_completed",
            extra={
                "total": len(outcomes),
                "succeeded": succeeded,
                "partial": partial_count,
                "failed": failed,
                "total_latency_ms": round(total_ms, 2),
                "component": "PlanWriter",
                "op": "bulk_persist",
            },
        )

        return BulkPersistResult(
            results=results,
            total=len(outcomes),
            succeeded=succeeded,
            partial=partial_count,
            failed=failed,
        )

    # -- Private helpers (keep persist_outcome under 50 lines) --

    async def _do_plan_library_write(
        self,
        plan: dict,
        signature: dict,
        outcome: dict,
        metrics: dict,
        plan_id: str,
    ) -> None:
        """Execute PlanLibrary write. Fatal if fails (except duplicates).

        Raises:
            PlanLibraryWriteError: On non-duplicate failures.
        """
        try:
            await self._plan_service.store_plan(
                plan,
                signature,
                outcome,
                metrics,
            )
        except DuplicatePlanError:
            logger.info(
                "duplicate_plan_treated_as_success",
                extra={
                    "plan_id": plan_id,
                    "component": "PlanWriter",
                    "op": "persist_outcome",
                },
            )
        except Exception as exc:
            logger.error(
                "persist_failed",
                extra={
                    "plan_id": plan_id,
                    "error": str(exc),
                    "component": "PlanWriter",
                    "op": "persist_outcome",
                },
            )
            raise PlanLibraryWriteError(
                plan_id=plan_id,
                reason=str(exc),
            ) from exc

    async def _write_to_history(
        self,
        user_id: UUID,
        plan: Plan,
        outcome: PlanOutcome,
        plan_id: str,
        errors: list[str],
    ) -> UUID | None:
        """Derive fact and write to History. Non-fatal on failure."""
        fact_id: UUID | None = None

        # Derive fact
        try:
            fact_request = derive_fact(plan, outcome)
        except FactDerivationError as exc:
            logger.warning(
                "persist_partial_failure",
                extra={
                    "plan_id": plan_id,
                    "failed_step": "fact_derivation",
                    "error": str(exc),
                    "component": "PlanWriter",
                    "op": "persist_outcome",
                },
            )
            errors.append(f"Fact derivation failed: {exc}")
            return None

        # Store fact
        try:
            response = await self._fact_service.store_fact(
                user_id,
                fact_request,
            )
            fact_id = response.fact_id
        except Exception as exc:
            logger.warning(
                "persist_partial_failure",
                extra={
                    "plan_id": plan_id,
                    "failed_step": "history",
                    "error": str(exc),
                    "component": "PlanWriter",
                    "op": "persist_outcome",
                },
            )
            errors.append(f"History write failed: {exc}")

        return fact_id

    async def _write_to_vector_index(
        self,
        plan_id: str,
        plan: dict,
        errors: list[str],
    ) -> bool:
        """Write embedding to VectorIndex. Optional, graceful degradation."""
        if self._vector_index_service is None:
            logger.warning(
                "vectorindex_unavailable",
                extra={
                    "plan_id": plan_id,
                    "component": "PlanWriter",
                    "op": "persist_outcome",
                },
            )
            return False

        try:
            await self._vector_index_service.store_embedding(plan_id, plan)
            return True
        except Exception as exc:
            logger.warning(
                "persist_partial_failure",
                extra={
                    "plan_id": plan_id,
                    "failed_step": "vectorindex",
                    "error": str(exc),
                    "component": "PlanWriter",
                    "op": "persist_outcome",
                },
            )
            errors.append(f"VectorIndex write failed: {exc}")
            return False


def create_plan_writer_service(
    plan_service: Any,
    fact_service: Any,
    vector_index_service: Any | None,
) -> PlanWriterService:
    """Create PlanWriterService with downstream Memory Layer services.

    Called once during application lifespan startup in shared/app.py.

    Args:
        plan_service: Initialized PlanService from PlanLibrary.
        fact_service: Initialized FactService from History.
        vector_index_service: Initialized VectorIndexService, or None.

    Returns:
        Configured PlanWriterService.
    """
    logger.info(
        "plan_writer_service_created",
        extra={
            "vectorindex_available": vector_index_service is not None,
            "component": "PlanWriter",
        },
    )
    return PlanWriterService(
        plan_service=plan_service,
        fact_service=fact_service,
        vector_index_service=vector_index_service,
    )
