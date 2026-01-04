"""
Database Adapter for PlanLibrary

Async SQLAlchemy 2.0 operations for plan tables.
Uses shared database utilities for connection management.
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict, Any
from uuid import UUID

from sqlalchemy import text, select, func, and_, desc
from sqlalchemy.exc import IntegrityError

from shared.database.adapter import get_database_adapter
from shared.database.models import (
    PlanTable, PlanOutcomeTable, PlanEmbeddingTable, PlanMetricsTable
)
from shared.database.error_handler import with_db_error_handling

from ..domain.models import (
    PlanDB, PlanOutcomeDB, PlanEmbeddingDB, PlanMetricsDB,
    PlanOutcome, PlanMetrics, PlanPattern
)

logger = logging.getLogger(__name__)


class DatabaseAdapter:
    """
    PlanLibrary database adapter.
    
    Uses shared database utilities for connection management.
    Provides CRUD operations for plans, outcomes, embeddings, and metrics.
    """
    
    def __init__(self):
        """Initialize database adapter using shared utilities."""
        self.shared_db = get_database_adapter()
        logger.info("PlanLibrary database adapter initialized")

    @with_db_error_handling
    async def store_plan_transaction(
        self,
        plan_db: PlanDB,
        outcome: PlanOutcome,
        metrics: PlanMetrics
    ) -> bool:
        """
        Store plan data in single atomic transaction.
        
        Stores plan, outcome, and metrics together to ensure
        data consistency across all related tables.
        
        Args:
            plan_db: Plan database model
            outcome: Plan execution outcome
            metrics: Plan performance metrics
            
        Returns:
            True if storage successful, False otherwise
            
        Raises:
            IntegrityError: If plan_id already exists
        """
        async with self.shared_db.get_session() as session:
            try:
                # Insert plan record
                plan_stmt = text("""
                    INSERT INTO plans (
                        plan_id, canonical_json, signature_data, intent_type,
                        step_count, plan_hash, size_bytes, created_at, stored_at
                    ) VALUES (
                        :plan_id, :canonical_json, :signature_data, :intent_type,
                        :step_count, :plan_hash, :size_bytes, :created_at, :stored_at
                    )
                """)
                
                await session.execute(plan_stmt, {
                    "plan_id": plan_db.plan_id,
                    "canonical_json": plan_db.canonical_json,
                    "signature_data": plan_db.signature_data,
                    "intent_type": plan_db.intent_type,
                    "step_count": plan_db.step_count,
                    "plan_hash": plan_db.plan_hash,
                    "size_bytes": plan_db.size_bytes,
                    "created_at": plan_db.created_at,
                    "stored_at": plan_db.stored_at
                })
                
                # Insert outcome record
                outcome_stmt = text("""
                    INSERT INTO plan_outcomes (
                        plan_id, success, error_type, error_details,
                        execution_start, execution_end, total_steps,
                        failed_step, context_data
                    ) VALUES (
                        :plan_id, :success, :error_type, :error_details,
                        :execution_start, :execution_end, :total_steps,
                        :failed_step, :context_data
                    )
                """)
                
                await session.execute(outcome_stmt, {
                    "plan_id": outcome.plan_id,
                    "success": outcome.success,
                    "error_type": outcome.error_type,
                    "error_details": outcome.error_details,
                    "execution_start": outcome.execution_start,
                    "execution_end": outcome.execution_end,
                    "total_steps": outcome.total_steps,
                    "failed_step": outcome.failed_step,
                    "context_data": outcome.context_data
                })
                
                # Insert metrics record
                metrics_stmt = text("""
                    INSERT INTO plan_metrics (
                        plan_id, preview_latency_ms, execute_latency_ms,
                        step_timings, resource_usage
                    ) VALUES (
                        :plan_id, :preview_latency_ms, :execute_latency_ms,
                        :step_timings, :resource_usage
                    )
                """)
                
                await session.execute(metrics_stmt, {
                    "plan_id": metrics.plan_id,
                    "preview_latency_ms": metrics.preview_latency_ms,
                    "execute_latency_ms": metrics.execute_latency_ms,
                    "step_timings": [t.model_dump() for t in metrics.step_timings],
                    "resource_usage": metrics.resource_usage.model_dump() if metrics.resource_usage else None
                })
                
                await session.commit()
                
                logger.debug(f"Plan {plan_db.plan_id} stored successfully with outcome and metrics")
                return True
                
            except IntegrityError as e:
                await session.rollback()
                logger.warning(f"Plan {plan_db.plan_id} already exists: {e}")
                # Re-raise as domain error will be handled by service layer
                raise
            except Exception as e:
                await session.rollback()
                logger.error(f"Failed to store plan {plan_db.plan_id}: {e}")
                raise

    @with_db_error_handling
    async def get_plan_by_id(self, plan_id: str) -> Optional[PlanDB]:
        """
        Retrieve specific plan by ID.
        
        Args:
            plan_id: ULID identifier for the plan
            
        Returns:
            PlanDB model if found, None if not found
        """
        async with self.shared_db.get_session() as session:
            stmt = select(PlanTable).where(PlanTable.plan_id == plan_id)
            result = await session.execute(stmt)
            plan = result.scalar_one_or_none()
            
            if plan is None:
                return None
            
            return PlanDB(
                plan_id=plan.plan_id,
                canonical_json=plan.canonical_json,
                signature_data=plan.signature_data,
                intent_type=plan.intent_type,
                step_count=plan.step_count,
                plan_hash=plan.plan_hash,
                size_bytes=plan.size_bytes,
                created_at=plan.created_at,
                stored_at=plan.stored_at
            )

    @with_db_error_handling
    async def get_plans_by_intent_with_success(
        self,
        intent_type: str,
        success_threshold: float = 0.7,
        limit: int = 50,
        recency_days: Optional[int] = None
    ) -> List[PlanPattern]:
        """
        Query plans by intent type with success rate filtering.
        
        Returns plans aggregated by plan_id with calculated success rates,
        sorted by success rate descending.
        
        Args:
            intent_type: Intent type to filter by
            success_threshold: Minimum success rate
            limit: Maximum number of results
            recency_days: Filter plans from last N days
            
        Returns:
            List of PlanPattern objects
        """
        async with self.shared_db.get_session() as session:
            # Build query with success rate calculation
            base_query = """
                SELECT 
                    p.plan_id,
                    p.intent_type,
                    COUNT(po.outcome_id) as total_executions,
                    COUNT(CASE WHEN po.success = true THEN 1 END) as successful_executions,
                    COUNT(CASE WHEN po.success = true THEN 1 END)::float / COUNT(po.outcome_id) as success_rate,
                    AVG(EXTRACT(EPOCH FROM (po.execution_end - po.execution_start)) * 1000) as avg_execution_time_ms,
                    p.step_count,
                    MAX(po.execution_start) as last_execution,
                    p.canonical_json
                FROM plans p
                INNER JOIN plan_outcomes po ON p.plan_id = po.plan_id
                WHERE p.intent_type = :intent_type
            """
            
            params = {"intent_type": intent_type}
            
            # Add recency filter if specified
            if recency_days is not None:
                cutoff_date = datetime.now(timezone.utc) - timedelta(days=recency_days)
                base_query += " AND po.execution_start >= :cutoff_date"
                params["cutoff_date"] = cutoff_date
            
            # Group by plan and filter by success threshold
            base_query += """
                GROUP BY p.plan_id, p.intent_type, p.step_count, p.canonical_json
                HAVING COUNT(CASE WHEN po.success = true THEN 1 END)::float / COUNT(po.outcome_id) >= :success_threshold
                ORDER BY success_rate DESC, total_executions DESC
                LIMIT :limit
            """
            
            params.update({
                "success_threshold": success_threshold,
                "limit": limit
            })
            
            result = await session.execute(text(base_query), params)
            rows = result.fetchall()
            
            # Convert to PlanPattern objects
            patterns = []
            for row in rows:
                # Generate pattern summary from plan operations
                pattern_summary = self._generate_pattern_summary(row.canonical_json)
                
                # Calculate confidence based on success rate and sample size
                confidence = min(1.0, row.success_rate * (min(row.total_executions, 10) / 10))
                
                pattern = PlanPattern(
                    plan_id=row.plan_id,
                    intent_type=row.intent_type,
                    success_rate=row.success_rate,
                    avg_execution_time_ms=row.avg_execution_time_ms,
                    steps_count=row.step_count,
                    pattern_summary=pattern_summary,
                    total_executions=row.total_executions,
                    last_execution=row.last_execution,
                    confidence=confidence
                )
                patterns.append(pattern)
            
            logger.debug(
                f"Retrieved {len(patterns)} plan patterns for intent {intent_type} "
                f"with success threshold {success_threshold}"
            )
            
            return patterns

    @with_db_error_handling
    async def get_success_rate_data(self, timeframe_days: int) -> List[Dict[str, Any]]:
        """
        Get success rate data by intent type for analytics.
        
        Args:
            timeframe_days: Analysis timeframe in days
            
        Returns:
            List of dictionaries with success rate data
        """
        async with self.shared_db.get_session() as session:
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=timeframe_days)
            
            query = """
                SELECT 
                    p.intent_type,
                    COUNT(po.outcome_id) as total_executions,
                    COUNT(CASE WHEN po.success = true THEN 1 END) as successful_executions,
                    AVG(pm.execute_latency_ms) as avg_execution_time_ms
                FROM plans p
                INNER JOIN plan_outcomes po ON p.plan_id = po.plan_id
                LEFT JOIN plan_metrics pm ON p.plan_id = pm.plan_id
                WHERE po.execution_start >= :cutoff_date
                GROUP BY p.intent_type
                HAVING COUNT(po.outcome_id) > 0
                ORDER BY total_executions DESC
            """
            
            result = await session.execute(text(query), {"cutoff_date": cutoff_date})
            rows = result.fetchall()
            
            return [
                {
                    "intent_type": row.intent_type,
                    "total_executions": row.total_executions,
                    "successful_executions": row.successful_executions,
                    "avg_execution_time_ms": row.avg_execution_time_ms or 0.0
                }
                for row in rows
            ]

    @with_db_error_handling
    async def get_performance_metrics(
        self,
        start_date: datetime,
        end_date: datetime,
        intent_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get performance metrics for a time period.
        
        Args:
            start_date: Start of time period
            end_date: End of time period
            intent_type: Specific intent to analyze (None for all)
            
        Returns:
            Dictionary with aggregated performance metrics
        """
        async with self.shared_db.get_session() as session:
            base_query = """
                SELECT 
                    COUNT(po.outcome_id) as total_executions,
                    COUNT(CASE WHEN po.success = true THEN 1 END) as successful_executions,
                    AVG(pm.execute_latency_ms) as avg_execution_time_ms,
                    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY pm.execute_latency_ms) as p95_execution_time_ms
                FROM plan_outcomes po
                INNER JOIN plans p ON po.plan_id = p.plan_id
                LEFT JOIN plan_metrics pm ON p.plan_id = pm.plan_id
                WHERE po.execution_start >= :start_date 
                  AND po.execution_start < :end_date
            """
            
            params = {
                "start_date": start_date,
                "end_date": end_date
            }
            
            if intent_type:
                base_query += " AND p.intent_type = :intent_type"
                params["intent_type"] = intent_type
            
            result = await session.execute(text(base_query), params)
            row = result.fetchone()
            
            if not row or row.total_executions == 0:
                return {
                    "total_executions": 0,
                    "success_rate": 0.0,
                    "avg_execution_time_ms": 0.0,
                    "p95_execution_time_ms": 0.0
                }
            
            return {
                "total_executions": row.total_executions,
                "success_rate": row.successful_executions / row.total_executions,
                "avg_execution_time_ms": row.avg_execution_time_ms or 0.0,
                "p95_execution_time_ms": row.p95_execution_time_ms or 0.0
            }

    @with_db_error_handling
    async def get_high_performing_patterns(
        self,
        min_executions: int,
        min_success_rate: float,
        timeframe_days: int
    ) -> List[Dict[str, Any]]:
        """
        Identify high-performing plan patterns.
        
        Args:
            min_executions: Minimum executions for inclusion
            min_success_rate: Minimum success rate threshold
            timeframe_days: Analysis timeframe
            
        Returns:
            List of high-performing pattern data
        """
        async with self.shared_db.get_session() as session:
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=timeframe_days)
            
            query = """
                SELECT 
                    p.plan_id,
                    p.intent_type,
                    COUNT(po.outcome_id) as total_executions,
                    COUNT(CASE WHEN po.success = true THEN 1 END) as successful_executions,
                    COUNT(CASE WHEN po.success = true THEN 1 END)::float / COUNT(po.outcome_id) as success_rate,
                    AVG(pm.execute_latency_ms) as avg_execution_time_ms,
                    p.step_count,
                    p.canonical_json
                FROM plans p
                INNER JOIN plan_outcomes po ON p.plan_id = po.plan_id
                LEFT JOIN plan_metrics pm ON p.plan_id = pm.plan_id
                WHERE po.execution_start >= :cutoff_date
                GROUP BY p.plan_id, p.intent_type, p.step_count, p.canonical_json
                HAVING COUNT(po.outcome_id) >= :min_executions
                   AND COUNT(CASE WHEN po.success = true THEN 1 END)::float / COUNT(po.outcome_id) >= :min_success_rate
                ORDER BY success_rate DESC, avg_execution_time_ms ASC
            """
            
            result = await session.execute(text(query), {
                "cutoff_date": cutoff_date,
                "min_executions": min_executions,
                "min_success_rate": min_success_rate
            })
            rows = result.fetchall()
            
            return [
                {
                    "plan_id": row.plan_id,
                    "intent_type": row.intent_type,
                    "total_executions": row.total_executions,
                    "success_rate": row.success_rate,
                    "avg_execution_time_ms": row.avg_execution_time_ms or 0.0,
                    "step_count": row.step_count,
                    "pattern_summary": self._generate_pattern_summary(row.canonical_json)
                }
                for row in rows
            ]

    def _generate_pattern_summary(self, canonical_json: Dict[str, Any]) -> str:
        """
        Generate human-readable summary of plan pattern.
        
        Args:
            canonical_json: Plan's canonical JSON representation
            
        Returns:
            Human-readable pattern summary
        """
        try:
            graph = canonical_json.get("graph", [])
            if not graph:
                return "Empty plan"
            
            # Extract operation names
            operations = [step.get("operation", "unknown") for step in graph]
            
            # Create summary with arrow notation
            if len(operations) <= 3:
                return " → ".join(operations)
            else:
                # Show first 2, last 1 with ellipsis
                return f"{operations[0]} → {operations[1]} → ... → {operations[-1]}"
                
        except Exception as e:
            logger.warning(f"Failed to generate pattern summary: {e}")
            return "Complex plan pattern"

    async def health_check(self) -> bool:
        """Check database connectivity using shared adapter."""
        return await self.shared_db.health_check()

    async def close(self):
        """Close database connections via shared adapter."""
        await self.shared_db.close()