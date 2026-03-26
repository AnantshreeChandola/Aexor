"""
ProfileStore Source Adapter

Thin wrapper around PreferenceService.get_all_preferences().
Returns list[EvidenceItem] with type="preference", tier=2, confidence=1.0.

Reference: LLD.md SS6.2
"""

import logging
from typing import Any
from uuid import UUID

from components.ProfileStore.domain.models import ConsentDeniedError
from shared.database.error_handler import DatabaseConnectionError, UserNotFoundError
from shared.schemas.evidence import EvidenceItem
from shared.schemas.intent import Intent

from ..domain.models import SourceQueryError

logger = logging.getLogger("contextrag")


class ProfileStoreAdapter:
    """Source adapter for ProfileStore preferences."""

    source_name = "profilestore"
    required_tier = 2
    default_timeout = 0.1

    def __init__(self, preference_service: Any) -> None:
        self._service = preference_service

    async def fetch_evidence(
        self,
        intent: Intent,
        _timeout_s: float = 0.1,
    ) -> list[EvidenceItem]:
        """Call PreferenceService.get_all_preferences().

        Returns list[EvidenceItem] with type="preference", tier=2.
        All errors are converted to SourceQueryError.
        """
        try:
            effective_tier = intent.context_budget or 3
            items = await self._service.get_all_preferences(
                user_id=UUID(intent.user_id),
                context_tier=effective_tier,
            )
            return items
        except ConsentDeniedError:
            raise SourceQueryError("profilestore", "consent_denied")
        except UserNotFoundError:
            raise SourceQueryError("profilestore", "user_not_found")
        except DatabaseConnectionError:
            raise SourceQueryError("profilestore", "connection_error")
        except SourceQueryError:
            raise
        except Exception as e:
            logger.warning(
                "profilestore_unexpected_error",
                extra={"error_type": type(e).__name__},
            )
            raise SourceQueryError("profilestore", f"unexpected: {type(e).__name__}")
