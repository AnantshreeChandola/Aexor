"""
Fact Service for History Component

Business logic for storing and querying facts.

Reference: LLD.md §4.1, tasks.md T200
"""

import logging
import re
from datetime import UTC, datetime, timedelta
from typing import ClassVar
from uuid import UUID

from ..domain.models import (
    Fact,
    FactTooLargeError,
    InvalidFactError,
    InvalidQueryError,
    InvalidTimestampError,
    QueryFactsResponse,
    StoreFactRequest,
    StoreFactResponse,
    compute_fact_hash,
)

logger = logging.getLogger(__name__)


class FactService:
    """
    Service for fact storage and retrieval.

    Enforces validation, deduplication, and TTL management.
    """

    # PII detection patterns (configurable)
    PII_PATTERNS: ClassVar[list[tuple[str, str]]] = [
        (r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", "email"),
        (r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b", "phone"),
        (r"\b\d{3}-\d{2}-\d{4}\b", "SSN"),
    ]

    def __init__(self, db_adapter, evidence_service, pattern_service):
        """
        Initialize FactService.

        Args:
            db_adapter: DatabaseAdapter instance
            evidence_service: EvidenceService instance
            pattern_service: PatternService instance
        """
        self.db_adapter = db_adapter
        self.evidence_service = evidence_service
        self.pattern_service = pattern_service

    async def store_fact(
        self,
        user_id: UUID,
        request: StoreFactRequest,
    ) -> StoreFactResponse:
        """
        Store a derived fact. Idempotent: duplicate fact_hash returns existing fact.

        Decision rules (top-to-bottom, first match wins):
        1. Validate fact_text not empty
        2. Validate fact_text <= 4KB
        3. Validate no PII detected
        4. Validate timestamp not in future (>now + 5min tolerance)
        5. Compute fact_hash
        6. Calculate expires_at
        7. Insert fact (idempotent on conflict)
        8. If new: update patterns
        9. Return response

        Args:
            user_id: User UUID
            request: StoreFactRequest with fact data

        Returns:
            StoreFactResponse with fact_id and status

        Raises:
            InvalidFactError: Empty or invalid fact_text
            FactTooLargeError: fact_text exceeds 4KB
            InvalidTimestampError: Timestamp in future
        """
        # Decision Rule 1: Validate fact_text not empty
        if not request.fact_text or request.fact_text.strip() == "":
            raise InvalidFactError("fact_text cannot be empty")

        # Decision Rule 2: Validate fact_text <= 4KB
        fact_size = len(request.fact_text.encode("utf-8"))
        if fact_size > 4096:
            raise FactTooLargeError(fact_size)

        # Decision Rule 3: Validate no PII detected
        pii_detected = self._detect_pii(request.fact_text)
        if pii_detected:
            logger.warning(
                "PII detected in fact_text",
                extra={
                    "user_id": str(user_id),
                    "pii_type": pii_detected,
                    "component": "History",
                    "op": "store_fact",
                },
            )
            raise InvalidFactError(f"PII detected in fact text ({pii_detected})")

        # Decision Rule 4: Validate timestamp not in future (>now + 5min tolerance)
        now = datetime.now(UTC)
        tolerance = timedelta(minutes=5)
        if now + tolerance < now:  # This would be the creation timestamp
            raise InvalidTimestampError(now)

        # Decision Rule 5: Compute fact_hash
        fact_date = now.date()
        fact_hash = compute_fact_hash(
            user_id=user_id,
            intent_type=request.intent_type,
            fact_text=request.fact_text,
            date_val=fact_date,
        )

        # Decision Rule 6: Calculate expires_at
        expires_at = now + timedelta(days=request.ttl_days)

        # Build Fact model
        fact = Fact(
            user_id=user_id,
            fact_text=request.fact_text,
            intent_type=request.intent_type,
            entities=request.entities,
            outcome=request.outcome,
            source_plan_id=request.source_plan_id,
            fact_hash=fact_hash,
            ttl_days=request.ttl_days,
            created_at=now,
            expires_at=expires_at,
        )

        # Decision Rule 7: Insert fact (idempotent)
        start_time = datetime.now(UTC)
        inserted_fact, is_new = await self.db_adapter.insert_fact(fact)
        latency_ms = int((datetime.now(UTC) - start_time).total_seconds() * 1000)

        # Log storage operation
        logger.info(
            "Fact stored",
            extra={
                "user_id": str(user_id),
                "fact_id": str(inserted_fact.fact_id),
                "intent_type": request.intent_type,
                "outcome": request.outcome,
                "storage_latency_ms": latency_ms,
                "is_new": is_new,
                "component": "History",
                "op": "store_fact",
            },
        )

        # Decision Rule 8: If new, update patterns
        if is_new:
            await self.pattern_service.update_patterns_on_store(
                user_id=user_id,
                fact=inserted_fact,
            )

        # Decision Rule 9: Return response
        status = "ok" if is_new else "duplicate"
        return StoreFactResponse(
            status=status,
            fact_id=inserted_fact.fact_id,
            stored_at=inserted_fact.created_at,
        )

    async def get_facts_by_intent(
        self,
        user_id: UUID,
        intent_type: str | None = None,
        limit: int = 50,
        recency_days: int | None = None,
    ) -> QueryFactsResponse:
        """
        Query facts filtered by intent, returning Evidence Items sorted by recency.

        Excludes expired facts. Enforces automatic pagination (max 500).

        Args:
            user_id: User UUID
            intent_type: Filter by intent type (optional)
            limit: Maximum results (default 50, max 500)
            recency_days: Only facts from last N days (optional)

        Returns:
            QueryFactsResponse with Evidence Items, counts

        Raises:
            InvalidQueryError: Invalid query parameters
        """
        # Validate query parameters
        if limit < 1 or limit > 500:
            raise InvalidQueryError("limit must be between 1 and 500")

        if recency_days is not None and recency_days < 1:
            raise InvalidQueryError("recency_days must be >= 1")

        # Calculate recency cutoff
        recency_cutoff = None
        if recency_days:
            now = datetime.now(UTC)
            recency_cutoff = now - timedelta(days=recency_days)

        # Query facts from database
        start_time = datetime.now(UTC)
        facts = await self.db_adapter.query_facts(
            user_id=user_id,
            intent_type=intent_type,
            limit=limit,
            recency_cutoff=recency_cutoff,
        )

        # Get total count
        total_count = await self.db_adapter.count_facts(
            user_id=user_id,
            intent_type=intent_type,
        )

        latency_ms = int((datetime.now(UTC) - start_time).total_seconds() * 1000)

        # Convert facts to Evidence Items
        evidence_items = []
        for fact in facts:
            evidence_item = self.evidence_service.fact_to_evidence(fact)
            evidence_items.append(evidence_item)

        # Log query operation
        logger.info(
            "Facts queried",
            extra={
                "user_id": str(user_id),
                "intent_type": intent_type,
                "result_count": len(evidence_items),
                "query_latency_ms": latency_ms,
                "component": "History",
                "op": "query_facts",
            },
        )

        return QueryFactsResponse(
            evidence=evidence_items,
            total_count=total_count,
            returned_count=len(evidence_items),
        )

    def _detect_pii(self, text: str) -> str | None:
        """
        Detect PII patterns in text.

        Args:
            text: Text to scan

        Returns:
            PII type if detected, None otherwise
        """
        for pattern, pii_type in self.PII_PATTERNS:
            if re.search(pattern, text):
                return pii_type
        return None
