"""
Database Adapter for History Component

Async SQLAlchemy 2.0 operations for history and fact_patterns tables.
Uses shared database utilities for connection management.

Reference: LLD.md §6, tasks.md T300
"""

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, select, text, update
from sqlalchemy.dialects.postgresql import insert

from shared.database.adapter import get_database_adapter
from shared.database.error_handler import with_db_error_handling, with_user_existence_check
from shared.database.models import FactPatternTable, HistoryTable

from ..domain.models import Fact, FactPattern

logger = logging.getLogger(__name__)


class DatabaseAdapter:
    """
    History component database adapter.

    Uses shared database utilities for connection management.
    Provides CRUD operations for facts and patterns with error handling.
    """

    def __init__(self):
        """Initialize database adapter using shared utilities."""
        self.shared_db = get_database_adapter()
        logger.info("History database adapter initialized")

    @with_user_existence_check()
    @with_db_error_handling
    async def insert_fact(self, fact: Fact) -> tuple[Fact, bool]:
        """
        Insert a fact. On conflict (duplicate fact_hash), return existing fact.

        Idempotent operation via UNIQUE INDEX on (user_id, fact_hash).

        Args:
            fact: Fact domain model

        Returns:
            Tuple of (fact, is_new) where is_new indicates if fact was inserted

        Raises:
            UserNotFoundError: If user_id doesn't exist
            DatabaseError: On database failure
        """
        async with self.shared_db.get_session() as session:
            # Build insert statement with ON CONFLICT
            stmt = insert(HistoryTable).values(
                fact_id=fact.fact_id,
                user_id=fact.user_id,
                fact_text=fact.fact_text,
                intent_type=fact.intent_type,
                entities=fact.entities,
                outcome=fact.outcome,
                source_plan_id=fact.source_plan_id,
                fact_hash=fact.fact_hash,
                ttl_days=fact.ttl_days,
                created_at=fact.created_at,
                expires_at=fact.expires_at,
                deleted_at=fact.deleted_at,
            )

            # On conflict, do nothing and return existing
            stmt = stmt.on_conflict_do_nothing(
                index_elements=["user_id", "fact_hash"],
                index_where=(HistoryTable.deleted_at.is_(None)),
            )

            result = await session.execute(stmt)
            await session.commit()

            # Check if row was inserted
            is_new = result.rowcount > 0

            if not is_new:
                # Fetch existing fact
                select_stmt = select(HistoryTable).where(
                    HistoryTable.user_id == fact.user_id,
                    HistoryTable.fact_hash == fact.fact_hash,
                    HistoryTable.deleted_at.is_(None),
                )
                result = await session.execute(select_stmt)
                row = result.scalar_one()

                # Convert to Fact model
                existing_fact = Fact(
                    fact_id=row.fact_id,
                    user_id=row.user_id,
                    fact_text=row.fact_text,
                    intent_type=row.intent_type,
                    entities=row.entities,
                    outcome=row.outcome,
                    source_plan_id=row.source_plan_id,
                    fact_hash=row.fact_hash,
                    ttl_days=row.ttl_days,
                    created_at=row.created_at,
                    expires_at=row.expires_at,
                    deleted_at=row.deleted_at,
                )
                return (existing_fact, False)

            return (fact, True)

    @with_user_existence_check()
    @with_db_error_handling
    async def query_facts(
        self,
        user_id: UUID,
        intent_type: str | None,
        limit: int,
        recency_cutoff: datetime | None,
    ) -> list[Fact]:
        """
        Query active, non-expired facts for a user.

        Sorted by created_at DESC (newest first).

        Args:
            user_id: User UUID
            intent_type: Filter by intent type (optional)
            limit: Maximum results
            recency_cutoff: Only facts after this timestamp (optional)

        Returns:
            List of Fact domain models
        """
        async with self.shared_db.get_session() as session:
            now = datetime.now(UTC)

            # Build query
            stmt = (
                select(HistoryTable)
                .where(
                    HistoryTable.user_id == user_id,
                    HistoryTable.deleted_at.is_(None),
                    HistoryTable.expires_at > now,
                )
                .order_by(HistoryTable.created_at.desc())
                .limit(limit)
            )

            # Optional intent_type filter
            if intent_type:
                stmt = stmt.where(HistoryTable.intent_type == intent_type)

            # Optional recency filter
            if recency_cutoff:
                stmt = stmt.where(HistoryTable.created_at >= recency_cutoff)

            result = await session.execute(stmt)
            rows = result.scalars().all()

            # Convert to Fact models
            facts = []
            for row in rows:
                fact = Fact(
                    fact_id=row.fact_id,
                    user_id=row.user_id,
                    fact_text=row.fact_text,
                    intent_type=row.intent_type,
                    entities=row.entities,
                    outcome=row.outcome,
                    source_plan_id=row.source_plan_id,
                    fact_hash=row.fact_hash,
                    ttl_days=row.ttl_days,
                    created_at=row.created_at,
                    expires_at=row.expires_at,
                    deleted_at=row.deleted_at,
                )
                facts.append(fact)

            return facts

    @with_user_existence_check()
    @with_db_error_handling
    async def count_facts(
        self,
        user_id: UUID,
        intent_type: str | None,
    ) -> int:
        """
        Count total facts matching filters.

        Args:
            user_id: User UUID
            intent_type: Filter by intent type (optional)

        Returns:
            Total count of matching facts
        """
        async with self.shared_db.get_session() as session:
            now = datetime.now(UTC)

            # Build count query
            stmt = (
                select(text("COUNT(*)"))
                .select_from(HistoryTable)
                .where(
                    HistoryTable.user_id == user_id,
                    HistoryTable.deleted_at.is_(None),
                    HistoryTable.expires_at > now,
                )
            )

            # Optional intent_type filter
            if intent_type:
                stmt = stmt.where(HistoryTable.intent_type == intent_type)

            result = await session.execute(stmt)
            count = result.scalar_one()

            return count

    @with_db_error_handling
    async def upsert_pattern(self, pattern: FactPattern) -> None:
        """
        Upsert a pattern (increment count, update last_seen).

        Uses ON CONFLICT on unique constraint (user_id, intent_type, pattern_key).

        Args:
            pattern: FactPattern domain model

        Raises:
            DatabaseError: On database failure
        """
        async with self.shared_db.get_session() as session:
            # Build insert statement
            stmt = insert(FactPatternTable).values(
                pattern_id=pattern.pattern_id,
                user_id=pattern.user_id,
                intent_type=pattern.intent_type,
                pattern_key=pattern.pattern_key,
                pattern_description=pattern.pattern_description,
                entity_pattern=pattern.entity_pattern,
                occurrence_count=pattern.occurrence_count,
                last_seen=pattern.last_seen,
                confidence=pattern.confidence,
            )

            # On conflict, update count and last_seen
            stmt = stmt.on_conflict_do_update(
                constraint="uq_fact_patterns_user_intent_key",
                set_={
                    "occurrence_count": pattern.occurrence_count,
                    "last_seen": pattern.last_seen,
                    "confidence": pattern.confidence,
                    "pattern_description": pattern.pattern_description,
                    "entity_pattern": pattern.entity_pattern,
                },
            )

            await session.execute(stmt)
            await session.commit()

    @with_user_existence_check()
    @with_db_error_handling
    async def query_patterns(
        self,
        user_id: UUID,
        intent_type: str | None,
        min_confidence: float,
    ) -> list[FactPattern]:
        """
        Query patterns above confidence threshold.

        Sorted by confidence DESC, last_seen DESC.

        Args:
            user_id: User UUID
            intent_type: Filter by intent type (optional)
            min_confidence: Minimum confidence threshold

        Returns:
            List of FactPattern domain models
        """
        async with self.shared_db.get_session() as session:
            # Build query
            stmt = (
                select(FactPatternTable)
                .where(
                    FactPatternTable.user_id == user_id,
                    FactPatternTable.confidence >= min_confidence,
                )
                .order_by(
                    FactPatternTable.confidence.desc(),
                    FactPatternTable.last_seen.desc(),
                )
            )

            # Optional intent_type filter
            if intent_type:
                stmt = stmt.where(FactPatternTable.intent_type == intent_type)

            result = await session.execute(stmt)
            rows = result.scalars().all()

            # Convert to FactPattern models
            patterns = []
            for row in rows:
                pattern = FactPattern(
                    pattern_id=row.pattern_id,
                    user_id=row.user_id,
                    intent_type=row.intent_type,
                    pattern_key=row.pattern_key,
                    pattern_description=row.pattern_description,
                    entity_pattern=row.entity_pattern,
                    occurrence_count=row.occurrence_count,
                    last_seen=row.last_seen,
                    confidence=row.confidence,
                )
                patterns.append(pattern)

            return patterns

    @with_db_error_handling
    async def cleanup_expired_facts(self, batch_size: int = 500) -> int:  # noqa: ARG002
        """
        Soft-delete facts past expires_at.

        Args:
            batch_size: Maximum rows to update per call

        Returns:
            Count of soft-deleted rows
        """
        async with self.shared_db.get_session() as session:
            now = datetime.now(UTC)

            # Build update statement
            # Note: SQLAlchemy update() doesn't support limit()
            # For batch processing, caller should invoke multiple times
            stmt = (
                update(HistoryTable)
                .where(
                    HistoryTable.expires_at < now,
                    HistoryTable.deleted_at.is_(None),
                )
                .values(deleted_at=now)
                .execution_options(synchronize_session=False)
            )

            result = await session.execute(stmt)
            await session.commit()

            return result.rowcount

    @with_db_error_handling
    async def hard_delete_old_facts(
        self,
        days_after_expiry: int = 90,
        batch_size: int = 500,  # noqa: ARG002
    ) -> int:
        """
        Hard-delete facts soft-deleted more than N days ago.

        Args:
            days_after_expiry: Days after soft-delete to hard-delete
            batch_size: Maximum rows to delete per call

        Returns:
            Count of hard-deleted rows
        """
        async with self.shared_db.get_session() as session:
            now = datetime.now(UTC)
            cutoff = now - timedelta(days=days_after_expiry)

            # Build delete statement
            # Note: SQLAlchemy delete() doesn't support limit()
            # For batch processing, caller should invoke multiple times
            stmt = (
                delete(HistoryTable)
                .where(
                    HistoryTable.deleted_at.isnot(None),
                    HistoryTable.deleted_at < cutoff,
                )
                .execution_options(synchronize_session=False)
            )

            result = await session.execute(stmt)
            await session.commit()

            return result.rowcount

    async def health_check(self) -> bool:
        """
        Check database connectivity.

        Returns:
            True if database is accessible, False otherwise
        """
        return await self.shared_db.health_check()
