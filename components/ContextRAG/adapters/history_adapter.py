"""
History Source Adapter

Thin wrapper around FactService.get_facts_by_intent() and
PatternService.get_patterns(). Converts History responses to EvidenceItem.

Reference: LLD.md SS6.3
"""

import logging
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from components.History.domain.models import (
    ConsentRequiredError,
    InvalidQueryError,
    StorageError,
)
from shared.database.error_handler import DatabaseConnectionError
from shared.schemas.evidence import EvidenceItem
from shared.schemas.intent import Intent

from ..domain.models import SourceQueryError

logger = logging.getLogger("contextrag")


class HistoryAdapter:
    """Source adapter for History facts and patterns."""

    source_name = "history"
    required_tier = 3
    default_timeout = 0.1

    def __init__(self, fact_service: Any, pattern_service: Any) -> None:
        self._fact_service = fact_service
        self._pattern_service = pattern_service

    async def fetch_evidence(
        self,
        intent: Intent,
        _timeout_s: float = 0.1,
    ) -> list[EvidenceItem]:
        """Call FactService and PatternService, return combined EvidenceItem list.

        QueryFactsResponse.evidence dicts are validated via EvidenceItem.model_validate().
        Pattern dicts are manually wrapped into EvidenceItem.
        Invalid items are dropped with a warning log.
        """
        try:
            user_id = UUID(intent.user_id)
            evidence: list[EvidenceItem] = []

            # Fetch facts
            facts_response = await self._fact_service.get_facts_by_intent(
                user_id=user_id,
                intent_type=intent.intent,
                limit=20,
                recency_days=30,
            )
            for item in facts_response.evidence:
                try:
                    evidence.append(EvidenceItem.model_validate(item))
                except ValidationError:
                    logger.warning(
                        "history_invalid_fact_dropped",
                        extra={"intent_type": intent.intent},
                    )

            # Fetch patterns
            patterns_response = await self._pattern_service.get_patterns(
                user_id=user_id,
                intent_type=intent.intent,
                min_confidence=0.5,
            )
            for pattern in patterns_response.patterns:
                evidence.append(
                    EvidenceItem(
                        type="history",
                        key=pattern["pattern_key"],
                        value=pattern["pattern_description"],
                        confidence=pattern["confidence"],
                        source_ref=f"history:patterns/{pattern['pattern_id']}",
                        ttl_days=30,
                        tier=3,
                    )
                )

            return evidence

        except ConsentRequiredError:
            raise SourceQueryError("history", "consent_required")
        except StorageError:
            raise SourceQueryError("history", "storage_error")
        except InvalidQueryError:
            raise SourceQueryError("history", "invalid_query")
        except DatabaseConnectionError:
            raise SourceQueryError("history", "connection_error")
        except SourceQueryError:
            raise
        except Exception as e:
            logger.warning(
                "history_unexpected_error",
                extra={"error_type": type(e).__name__},
            )
            raise SourceQueryError("history", f"unexpected: {type(e).__name__}")
