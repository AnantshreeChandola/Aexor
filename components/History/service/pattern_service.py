"""
Pattern Service for History Component

Detects and manages recurring behavioral patterns.

Reference: LLD.md §4.2, tasks.md T201
"""

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from ..domain.models import Fact, FactPattern, PatternsResponse

logger = logging.getLogger(__name__)


class PatternService:
    """
    Service for pattern detection and management.

    Patterns are updated incrementally on fact storage (on-write).
    """

    def __init__(self, db_adapter):
        """
        Initialize PatternService.

        Args:
            db_adapter: DatabaseAdapter instance
        """
        self.db_adapter = db_adapter

    async def get_patterns(
        self,
        user_id: UUID,
        intent_type: str | None = None,
        min_confidence: float = 0.5,
    ) -> PatternsResponse:
        """
        Get recurring patterns for a user.

        Filters out stale patterns (last_seen > 30 days ago).

        Args:
            user_id: User UUID
            intent_type: Filter by intent type (optional)
            min_confidence: Minimum confidence threshold (0.0-1.0)

        Returns:
            PatternsResponse with filtered patterns
        """
        # Query patterns from database
        patterns = await self.db_adapter.query_patterns(
            user_id=user_id,
            intent_type=intent_type,
            min_confidence=min_confidence,
        )

        # Filter out stale patterns (>30 days)
        now = datetime.now(UTC)
        stale_threshold = now - timedelta(days=30)

        active_patterns = []
        for pattern in patterns:
            if pattern.last_seen >= stale_threshold:
                active_patterns.append(pattern.model_dump())

        return PatternsResponse(
            patterns=active_patterns,
            total_count=len(active_patterns),
        )

    async def update_patterns_on_store(self, user_id: UUID, fact: Fact) -> None:
        """
        Incrementally update patterns when a new fact is stored.

        Pattern key format: {intent_type}:{entity_key}:{day_of_week}
        Example: schedule_meeting:person:Alice:Tuesday

        Confidence formula: min(1.0, occurrence_count / 5)

        Args:
            user_id: User UUID
            fact: Newly stored fact
        """
        # Extract day of week from fact timestamp
        day_of_week = fact.created_at.strftime("%A")

        # Extract primary entity for pattern key
        entity_key = self._extract_entity_key(fact.entities)
        if not entity_key:
            # No recognizable entity pattern
            logger.debug(
                "No entity pattern found for fact",
                extra={
                    "user_id": str(user_id),
                    "fact_id": str(fact.fact_id),
                    "intent_type": fact.intent_type,
                },
            )
            return

        # Build pattern key
        pattern_key = f"{fact.intent_type}:{entity_key}:{day_of_week}"

        # Generate pattern description
        pattern_description = (
            f"{fact.intent_type.replace('_', ' ').title()} with {entity_key} on {day_of_week}s"
        )

        # Build entity pattern (for filtering)
        entity_pattern = {
            "day_of_week": day_of_week,
            **fact.entities,
        }

        # Query existing pattern
        existing = await self.db_adapter.query_patterns(
            user_id=user_id,
            intent_type=fact.intent_type,
            min_confidence=0.0,  # Get all to check for this specific pattern
        )

        # Find matching pattern by pattern_key
        matching_pattern = None
        for p in existing:
            if p.pattern_key == pattern_key:
                matching_pattern = p
                break

        if matching_pattern:
            # Update existing pattern
            new_count = matching_pattern.occurrence_count + 1
            confidence = min(1.0, new_count / 5.0)

            updated_pattern = FactPattern(
                pattern_id=matching_pattern.pattern_id,
                user_id=user_id,
                intent_type=fact.intent_type,
                pattern_key=pattern_key,
                pattern_description=pattern_description,
                entity_pattern=entity_pattern,
                occurrence_count=new_count,
                last_seen=fact.created_at,
                confidence=confidence,
            )
        else:
            # Create new pattern
            confidence = min(1.0, 1 / 5.0)

            updated_pattern = FactPattern(
                user_id=user_id,
                intent_type=fact.intent_type,
                pattern_key=pattern_key,
                pattern_description=pattern_description,
                entity_pattern=entity_pattern,
                occurrence_count=1,
                last_seen=fact.created_at,
                confidence=confidence,
            )

        # Upsert pattern
        await self.db_adapter.upsert_pattern(updated_pattern)

        logger.info(
            "Pattern updated",
            extra={
                "user_id": str(user_id),
                "pattern_key": pattern_key,
                "occurrence_count": updated_pattern.occurrence_count,
                "confidence": updated_pattern.confidence,
                "component": "History",
                "op": "update_pattern",
            },
        )

    def _extract_entity_key(self, entities: dict) -> str | None:
        """
        Extract primary entity key for pattern detection.

        Prioritizes: person > location > other entity types.

        Args:
            entities: Entity dictionary from fact

        Returns:
            Entity key string or None if no recognizable pattern
        """
        if not entities:
            return None

        # Priority order for entity types
        priority_keys = ["person", "location", "project", "category"]

        for key in priority_keys:
            if key in entities:
                return f"{key}:{entities[key]}"

        # Use first available entity
        if entities:
            first_key = next(iter(entities))
            return f"{first_key}:{entities[first_key]}"

        return None
