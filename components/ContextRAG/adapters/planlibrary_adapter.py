"""
PlanLibrary Source Adapter

Thin wrapper around PlanService.get_plans_by_intent().
Returns list[EvidenceItem] with type="plan", tier=3.

Reference: LLD.md SS6.4
"""

import logging
from typing import Any

from components.PlanLibrary.domain.models import (
    InvalidQueryError as PlanInvalidQueryError,
)
from shared.database.error_handler import DatabaseConnectionError
from shared.schemas.evidence import EvidenceItem
from shared.schemas.intent import Intent

from ..domain.models import SourceQueryError

logger = logging.getLogger("contextrag")


class PlanLibraryAdapter:
    """Source adapter for PlanLibrary plan patterns."""

    source_name = "planlibrary"
    required_tier = 3
    default_timeout = 0.1

    def __init__(self, plan_service: Any) -> None:
        self._service = plan_service

    async def fetch_evidence(
        self,
        intent: Intent,
        _timeout_s: float = 0.1,
    ) -> list[EvidenceItem]:
        """Call PlanService.get_plans_by_intent().

        Returns list[EvidenceItem] already in correct format.
        All errors are converted to SourceQueryError.
        """
        try:
            items = await self._service.get_plans_by_intent(
                intent_type=intent.intent,
                success_threshold=0.7,
                limit=5,
                recency_days=90,
            )
            return items
        except PlanInvalidQueryError:
            raise SourceQueryError("planlibrary", "invalid_query")
        except DatabaseConnectionError:
            raise SourceQueryError("planlibrary", "connection_error")
        except SourceQueryError:
            raise
        except Exception as e:
            logger.warning(
                "planlibrary_unexpected_error",
                extra={"error_type": type(e).__name__},
            )
            raise SourceQueryError("planlibrary", f"unexpected: {type(e).__name__}")
