"""
Database Adapter for PlanLibrary

Async SQLAlchemy 2.0 operations for plans, outcomes, and metrics tables.
Uses shared database utilities for connection management.

Reference: LLD.md, tasks.md T300
"""

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, text

from shared.database.adapter import get_database_adapter
from shared.database.error_handler import with_db_error_handling
from shared.database.models import (
    PlanMetricsTable,
    PlanOutcomeTable,
    PlanTable,
)

from ..domain.models import PlanDB, PlanMetricsDB, PlanOutcomeDB

logger = logging.getLogger(__name__)


class DatabaseAdapter:
    """
    PlanLibrary database adapter.

    Uses shared database utilities for connection management.
    Provides storage and query operations for plans with error handling.
    """

    def __init__(self) -> None:
        """Initialize database adapter using shared utilities."""
        self.shared_db = get_database_adapter()
        logger.info(
            "PlanLibrary database adapter initialized",
            extra={"component": "PlanLibrary"},
        )

    @with_db_error_handling
    async def store_plan_transaction(
        self,
        plan: PlanDB,
        outcome: PlanOutcomeDB,
        metrics: PlanMetricsDB,
    ) -> bool:
        """
        Store plan data in single atomic transaction.

        Args:
            plan: Plan domain model
            outcome: Outcome domain model
            metrics: Metrics domain model

        Returns:
            True if storage succeeded

        Raises:
            DatabaseIntegrityError: On duplicate plan_id
        """
        async with self.shared_db.get_session() as session:
            async with session.begin():
                # Insert plan first (FK parent) and flush to satisfy constraints
                session.add(
                    PlanTable(
                        plan_id=plan.plan_id,
                        canonical_json=plan.canonical_json,
                        signature_data=plan.signature_data,
                        intent_type=plan.intent_type,
                        step_count=plan.step_count,
                        plan_hash=plan.plan_hash,
                        size_bytes=plan.size_bytes,
                        created_at=plan.created_at,
                        stored_at=plan.stored_at,
                    )
                )
                await session.flush()

                # Insert outcome and metrics (FK children)
                session.add(
                    PlanOutcomeTable(
                        outcome_id=outcome.outcome_id,
                        plan_id=outcome.plan_id,
                        success=outcome.success,
                        error_type=outcome.error_type,
                        error_details=outcome.error_details,
                        execution_start=outcome.execution_start,
                        execution_end=outcome.execution_end,
                        total_steps=outcome.total_steps,
                        failed_step=outcome.failed_step,
                        context_data=outcome.context_data,
                        final_graph_json=outcome.final_graph_json,
                        plan_revision=outcome.plan_revision,
                    )
                )

                session.add(
                    PlanMetricsTable(
                        metrics_id=metrics.metrics_id,
                        plan_id=metrics.plan_id,
                        preview_latency_ms=metrics.preview_latency_ms,
                        execute_latency_ms=metrics.execute_latency_ms,
                        step_timings=metrics.step_timings,
                        resource_usage=metrics.resource_usage,
                    )
                )

            logger.info(
                "Plan stored successfully",
                extra={
                    "plan_id": plan.plan_id,
                    "intent_type": plan.intent_type,
                    "component": "PlanLibrary",
                    "operation": "store_plan_transaction",
                },
            )
            return True

    @with_db_error_handling
    async def get_plan_by_id(self, plan_id: str) -> PlanDB | None:
        """
        Retrieve specific plan by ID.

        Args:
            plan_id: ULID plan identifier

        Returns:
            PlanDB if found, None if not found
        """
        async with self.shared_db.get_session() as session:
            stmt = select(PlanTable).where(PlanTable.plan_id == plan_id)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()

            if row is None:
                return None

            return PlanDB(
                plan_id=row.plan_id,
                canonical_json=row.canonical_json,
                signature_data=row.signature_data,
                intent_type=row.intent_type,
                step_count=row.step_count,
                plan_hash=row.plan_hash,
                size_bytes=row.size_bytes,
                created_at=row.created_at,
                stored_at=row.stored_at,
            )

    @with_db_error_handling
    async def get_plan_by_hash(self, plan_hash: str) -> dict[str, Any] | None:
        """
        Retrieve the most recent successful plan matching a signature hash.

        Uses the existing idx_plans_hash index. Returns the plan data dict
        with a ``success`` flag if found, None otherwise.
        """
        async with self.shared_db.get_session() as session:
            query = text("""
                SELECT
                    p.plan_id,
                    p.canonical_json,
                    p.intent_type,
                    p.step_count,
                    p.plan_hash,
                    p.created_at,
                    o.success
                FROM plans p
                JOIN plan_outcomes o ON p.plan_id = o.plan_id
                WHERE p.plan_hash = :plan_hash
                  AND o.success = true
                ORDER BY p.stored_at DESC
                LIMIT 1
            """)
            result = await session.execute(query, {"plan_hash": plan_hash})
            row = result.first()
            if row is None:
                return None
            return {
                "plan_id": row.plan_id,
                "canonical_json": row.canonical_json,
                "intent_type": row.intent_type,
                "step_count": row.step_count,
                "plan_hash": row.plan_hash,
                "created_at": row.created_at,
                "success": row.success,
            }

    @with_db_error_handling
    async def get_plans_by_intent(
        self,
        intent_type: str,
        success_threshold: float = 0.7,
        limit: int = 50,
        recency_days: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        Query plans by intent type with success rate filtering.

        Args:
            intent_type: Intent type to filter by
            success_threshold: Minimum success rate
            limit: Maximum results to return
            recency_days: Optional filter for recent plans

        Returns:
            List of plan data dicts with success rate info
        """
        async with self.shared_db.get_session() as session:
            # Build query with success rate subquery
            query = text("""
                SELECT
                    p.plan_id,
                    p.canonical_json,
                    p.intent_type,
                    p.step_count,
                    p.plan_hash,
                    p.size_bytes,
                    p.created_at,
                    p.stored_at,
                    COALESCE(
                        AVG(CASE WHEN o.success THEN 1.0 ELSE 0.0 END), 0.0
                    ) as success_rate,
                    COUNT(o.outcome_id) as total_executions,
                    COALESCE(
                        AVG(m.execute_latency_ms), 0
                    ) as avg_execution_time_ms
                FROM plans p
                LEFT JOIN plan_outcomes o ON p.plan_id = o.plan_id
                LEFT JOIN plan_metrics m ON p.plan_id = m.plan_id
                WHERE p.intent_type = :intent_type
                GROUP BY p.plan_id
                HAVING COALESCE(
                    AVG(CASE WHEN o.success THEN 1.0 ELSE 0.0 END), 0.0
                ) >= :success_threshold
                ORDER BY success_rate DESC, total_executions DESC
                LIMIT :limit
            """)

            params: dict[str, Any] = {
                "intent_type": intent_type,
                "success_threshold": success_threshold,
                "limit": limit,
            }

            # Add recency filter if specified
            if recency_days is not None:
                query = text("""
                    SELECT
                        p.plan_id,
                        p.canonical_json,
                        p.intent_type,
                        p.step_count,
                        p.plan_hash,
                        p.size_bytes,
                        p.created_at,
                        p.stored_at,
                        COALESCE(
                            AVG(CASE WHEN o.success THEN 1.0 ELSE 0.0 END),
                            0.0
                        ) as success_rate,
                        COUNT(o.outcome_id) as total_executions,
                        COALESCE(
                            AVG(m.execute_latency_ms), 0
                        ) as avg_execution_time_ms
                    FROM plans p
                    LEFT JOIN plan_outcomes o ON p.plan_id = o.plan_id
                    LEFT JOIN plan_metrics m ON p.plan_id = m.plan_id
                    WHERE p.intent_type = :intent_type
                      AND p.stored_at >= :cutoff_date
                    GROUP BY p.plan_id
                    HAVING COALESCE(
                        AVG(CASE WHEN o.success THEN 1.0 ELSE 0.0 END),
                        0.0
                    ) >= :success_threshold
                    ORDER BY success_rate DESC, total_executions DESC
                    LIMIT :limit
                """)
                cutoff = datetime.utcnow() - timedelta(days=recency_days)
                params["cutoff_date"] = cutoff

            result = await session.execute(query, params)
            rows = result.fetchall()

            return [
                {
                    "plan_id": row.plan_id,
                    "canonical_json": row.canonical_json,
                    "intent_type": row.intent_type,
                    "step_count": row.step_count,
                    "plan_hash": row.plan_hash,
                    "size_bytes": row.size_bytes,
                    "created_at": row.created_at,
                    "stored_at": row.stored_at,
                    "success_rate": float(row.success_rate),
                    "total_executions": row.total_executions,
                    "avg_execution_time_ms": float(row.avg_execution_time_ms),
                }
                for row in rows
            ]

    @with_db_error_handling
    async def get_plan_outcomes(self, plan_id: str) -> list[PlanOutcomeDB]:
        """
        Get all outcomes for a specific plan.

        Args:
            plan_id: ULID plan identifier

        Returns:
            List of PlanOutcomeDB models
        """
        async with self.shared_db.get_session() as session:
            stmt = (
                select(PlanOutcomeTable)
                .where(PlanOutcomeTable.plan_id == plan_id)
                .order_by(PlanOutcomeTable.execution_start.desc())
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()

            return [
                PlanOutcomeDB(
                    outcome_id=row.outcome_id,
                    plan_id=row.plan_id,
                    success=row.success,
                    error_type=row.error_type,
                    error_details=row.error_details,
                    execution_start=row.execution_start,
                    execution_end=row.execution_end,
                    total_steps=row.total_steps,
                    failed_step=row.failed_step,
                    context_data=row.context_data,
                    final_graph_json=row.final_graph_json,
                    plan_revision=row.plan_revision,
                )
                for row in rows
            ]

    @with_db_error_handling
    async def get_success_rates(self, timeframe_days: int = 30) -> dict[str, float]:
        """
        Calculate success rates grouped by intent type.

        Args:
            timeframe_days: Number of days to consider

        Returns:
            Dict mapping intent_type -> success_rate
        """
        async with self.shared_db.get_session() as session:
            cutoff = datetime.utcnow() - timedelta(days=timeframe_days)
            query = text("""
                SELECT
                    p.intent_type,
                    AVG(CASE WHEN o.success THEN 1.0 ELSE 0.0 END)
                        as success_rate
                FROM plans p
                JOIN plan_outcomes o ON p.plan_id = o.plan_id
                WHERE o.execution_start >= :cutoff
                GROUP BY p.intent_type
                ORDER BY p.intent_type
            """)
            result = await session.execute(query, {"cutoff": cutoff})
            rows = result.fetchall()

            return {row.intent_type: float(row.success_rate) for row in rows}

    @with_db_error_handling
    async def get_all_plans(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return all plans with their latest outcome, ordered by stored_at DESC."""
        async with self.shared_db.get_session() as session:
            query = text("""
                SELECT p.plan_id, p.intent_type, p.step_count, p.stored_at,
                       p.canonical_json -> 'intent' -> 'intent' AS intent_name,
                       p.canonical_json -> 'intent' -> 'entities' AS intent_entities,
                       o.success, o.error_type, o.execution_start, o.execution_end,
                       o.total_steps, o.failed_step, o.context_data
                FROM plans p
                LEFT JOIN LATERAL (
                    SELECT * FROM plan_outcomes po
                    WHERE po.plan_id = p.plan_id
                    ORDER BY po.execution_start DESC LIMIT 1
                ) o ON true
                ORDER BY p.stored_at DESC
                LIMIT :limit
            """)
            result = await session.execute(query, {"limit": limit})
            rows = result.fetchall()
            return [dict(row._mapping) for row in rows]

    @with_db_error_handling
    async def get_plans_by_user(
        self,
        user_id: str,
        limit: int = 50,
        success_only: bool = False,
    ) -> list[dict[str, Any]]:
        """Return plans for a specific user, identified via canonical_json->'intent'->>'user_id'.

        Args:
            user_id: User identifier stored inside the plan intent
            limit: Maximum results to return
            success_only: When True, only return plans whose latest outcome was successful

        Returns:
            List of plan dicts with latest outcome info
        """
        async with self.shared_db.get_session() as session:
            base_query = """
                SELECT p.plan_id, p.intent_type, p.step_count, p.stored_at,
                       p.canonical_json -> 'intent' -> 'intent' AS intent_name,
                       p.canonical_json -> 'intent' -> 'entities' AS intent_entities,
                       o.success, o.error_type, o.execution_start, o.execution_end,
                       o.total_steps, o.failed_step, o.context_data
                FROM plans p
                LEFT JOIN LATERAL (
                    SELECT * FROM plan_outcomes po
                    WHERE po.plan_id = p.plan_id
                    ORDER BY po.execution_start DESC LIMIT 1
                ) o ON true
                WHERE p.canonical_json -> 'intent' ->> 'user_id' = :user_id
            """
            if success_only:
                base_query += " AND o.success = true"
            base_query += " ORDER BY p.stored_at DESC LIMIT :limit"

            result = await session.execute(
                text(base_query), {"user_id": user_id, "limit": limit}
            )
            rows = result.fetchall()
            return [dict(row._mapping) for row in rows]

    async def health_check(self) -> bool:
        """Check database connectivity using shared adapter."""
        return await self.shared_db.health_check()

    async def close(self) -> None:
        """Close database connections via shared adapter."""
        await self.shared_db.close()
