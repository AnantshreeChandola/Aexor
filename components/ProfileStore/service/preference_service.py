"""
Preference Service - Core Business Logic for ProfileStore

Coordinates database, encryption, and schema validation operations.
Implements consent enforcement and Evidence Item formatting.

Reference: LLD.md §3.2
"""

import json
import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from shared.schemas.evidence import EvidenceItem

from ..adapters.db import DatabaseAdapter
from ..adapters.encryption import EncryptionAdapter
from ..adapters.schema_registry import SchemaRegistryAdapter
from ..domain.models import (
    ConsentDeniedError,
    DeleteResponse,
    ExtractedPreference,
    PreferenceImportResponse,
    PreferenceResponse,
    ValidationError,
)

logger = logging.getLogger(__name__)


class PreferenceService:
    """
    Core business logic for ProfileStore preferences.

    Coordinates all adapters and implements consent enforcement.
    Returns preferences in Evidence Item format for ContextRAG integration.
    """

    def __init__(
        self,
        db_adapter: DatabaseAdapter,
        schema_registry: SchemaRegistryAdapter,
        encryption_adapter: EncryptionAdapter,
    ):
        """
        Initialize preference service with adapters.

        Args:
            db_adapter: Database operations
            schema_registry: Schema validation
            encryption_adapter: Encryption for sensitive data
        """
        self.db = db_adapter
        self.schema_registry = schema_registry
        self.encryption = encryption_adapter
        logger.info("Preference service initialized")

    async def get_preference(
        self, user_id: UUID, preference_key: str, context_tier: int, plan_id: str | None = None
    ) -> EvidenceItem:
        """
        Retrieve a preference value for a user.

        Args:
            user_id: User UUID
            preference_key: Preference key to retrieve
            context_tier: User's consent tier from auth context
            plan_id: Optional plan ID for correlation logging

        Returns:
            EvidenceItem (GLOBAL_SPEC §2.2 format)

        Raises:
            ConsentDeniedError: If context_tier < 2
            UserNotFoundError: If user_id does not exist
        """
        # Consent enforcement: ProfileStore requires Tier 2+
        if context_tier < 2:
            logger.warning(f"Consent denied for user {user_id}: required=2, current={context_tier}")
            raise ConsentDeniedError(user_id, required_tier=2, current_tier=context_tier)

        # Try to get preference from database
        preference = await self.db.get_preference(user_id, preference_key)

        if preference is not None:
            # Preference exists - decrypt if sensitive and return
            value = preference.value
            if preference.sensitive:
                value = self.encryption.decrypt_value(value)

            logger.info(
                f"Retrieved preference: user={user_id}, key={preference_key}, "
                f"sensitive={preference.sensitive}, plan_id={plan_id}"
            )

        else:
            # Preference not found - return default from schema
            value = self.schema_registry.get_default_value(preference_key)

            logger.info(
                f"Using default preference: user={user_id}, key={preference_key}, "
                f"default={value}, plan_id={plan_id}"
            )

        # Build Evidence Item (GLOBAL_SPEC §2.2)
        evidence = EvidenceItem(
            type="preference",
            key=preference_key,
            value=value,
            confidence=1.0,  # ProfileStore data is authoritative
            source_ref=f"profilestore:prefs/{preference_key}",
            ttl_days=None,  # Preferences don't expire
            tier=2,  # ProfileStore is Tier 2 data source
        )

        return evidence

    async def set_preference(
        self,
        user_id: UUID,
        preference_key: str,
        preference_value: Any,
        sensitive: bool = False,
        plan_id: str | None = None,
    ) -> PreferenceResponse:
        """
        Create or update a preference (upsert).

        Args:
            user_id: User UUID
            preference_key: Preference key
            preference_value: Value to store
            sensitive: Whether to encrypt the value
            plan_id: Optional plan ID for correlation logging

        Returns:
            PreferenceResponse with preference metadata

        Raises:
            UserNotFoundError: If user_id does not exist
            ValidationError: If preference_value fails schema validation
        """
        # Check if preference should be sensitive based on schema
        schema_sensitive = self.schema_registry.is_sensitive(preference_key)
        if schema_sensitive and not sensitive:
            logger.warning(
                f"Preference {preference_key} marked as sensitive in schema "
                f"but sensitive=False in request"
            )
            sensitive = True  # Override to ensure sensitive data is encrypted

        # Validate value against schema
        try:
            self.schema_registry.validate_value(preference_key, preference_value)
        except ValidationError as e:
            logger.warning(f"Schema validation failed for {preference_key}: {e.reason}")
            raise

        # Encrypt value if sensitive
        storage_value = preference_value
        if sensitive:
            storage_value = self.encryption.encrypt_value(preference_value)

        # Store in database (upsert)
        preference = await self.db.upsert_preference(
            user_id=user_id, preference_key=preference_key, value=storage_value, sensitive=sensitive
        )

        logger.info(
            f"Set preference: user={user_id}, key={preference_key}, "
            f"sensitive={sensitive}, plan_id={plan_id}"
        )

        # Return response (don't include encrypted value)
        return PreferenceResponse(
            preference_id=preference.preference_id,
            user_id=preference.user_id,
            preference_key=preference.key,
            preference_value=preference_value,  # Return original, not encrypted
            updated_at=preference.updated_at,
            sensitive=sensitive,
        )

    async def delete_preference(
        self, user_id: UUID, preference_key: str, plan_id: str | None = None
    ) -> DeleteResponse:
        """
        Delete a preference (reset to schema default).

        Args:
            user_id: User UUID
            preference_key: Preference key to delete
            plan_id: Optional plan ID for correlation logging

        Returns:
            DeleteResponse with deletion confirmation

        Raises:
            UserNotFoundError: If user_id does not exist
        """
        # Delete from database (soft delete)
        deleted = await self.db.delete_preference(user_id, preference_key)

        if deleted:
            logger.info(
                f"Deleted preference: user={user_id}, key={preference_key}, plan_id={plan_id}"
            )
        else:
            logger.info(
                f"Preference not found for deletion: user={user_id}, "
                f"key={preference_key}, plan_id={plan_id}"
            )

        return DeleteResponse(
            user_id=user_id,
            preference_key=preference_key,
            deleted_at=datetime.utcnow(),
            message="Preference deleted successfully" if deleted else "Preference not found",
        )

    async def get_all_preferences(
        self,
        user_id: UUID,
        context_tier: int,
        plan_id: str | None = None,
        include_defaults: bool = True,
    ) -> list[EvidenceItem]:
        """
        Get all preferences for a user as Evidence Items.

        Args:
            user_id: User UUID
            context_tier: User's consent tier from auth context
            plan_id: Optional plan ID for correlation logging
            include_defaults: Whether to include schema defaults for unset keys

        Returns:
            List of Evidence Items for all user preferences

        Raises:
            ConsentDeniedError: If context_tier < 2
            UserNotFoundError: If user_id does not exist
        """
        # Consent enforcement
        if context_tier < 2:
            logger.warning(f"Consent denied for user {user_id}: required=2, current={context_tier}")
            raise ConsentDeniedError(user_id, required_tier=2, current_tier=context_tier)

        # Get all preferences from database
        preferences = await self.db.get_all_preferences(user_id)

        evidence_items = []
        for preference in preferences:
            # Decrypt if sensitive
            value = preference.value
            if preference.sensitive:
                value = self.encryption.decrypt_value(value)

            # Build Evidence Item
            evidence = EvidenceItem(
                type="preference",
                key=preference.key,
                value=value,
                confidence=1.0,
                source_ref=f"profilestore:prefs/{preference.key}",
                ttl_days=None,
                tier=2,
            )
            evidence_items.append(evidence)

        # Also include defaults for preferences not explicitly set
        if include_defaults:
            all_schema_keys = self.schema_registry.list_preference_keys()
            set_keys = {pref.key for pref in preferences}

            for key in all_schema_keys:
                if key not in set_keys:
                    default_value = self.schema_registry.get_default_value(key)
                    if default_value is not None:
                        evidence = EvidenceItem(
                            type="preference",
                            key=key,
                            value=default_value,
                            confidence=1.0,
                            source_ref=f"profilestore:prefs/{key}",
                            ttl_days=None,
                            tier=2,
                        )
                        evidence_items.append(evidence)

        logger.info(
            f"Retrieved {len(evidence_items)} preferences for user {user_id}, plan_id={plan_id}"
        )

        return evidence_items

    async def import_preferences(
        self,
        user_id: UUID,
        content: str,
        llm_adapter: Any | None = None,
    ) -> PreferenceImportResponse:
        """
        Extract preferences from JSON or free-text content.

        JSON input is parsed and mapped to known keys directly.
        Free-text input is sent to the LLM-based PreferenceExtractor.

        Args:
            user_id: User UUID (for logging)
            content: Raw text or JSON string
            llm_adapter: LLMAdapter instance (required for free-text)

        Returns:
            PreferenceImportResponse with extracted preferences for review
        """
        content = content.strip()
        warnings: list[str] = []

        logger.info("Import preferences request for user %s", user_id)

        # Try JSON path first
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                return self._import_from_json(parsed, warnings)
        except json.JSONDecodeError:
            pass

        # Free-text path — requires LLM
        if llm_adapter is None:
            raise ValueError(
                "LLM adapter is not available. Free-text import requires an LLM. "
                "Please paste valid JSON instead, or configure an LLM provider."
            )

        return await self._import_from_freetext(content, llm_adapter, warnings)

    def _import_from_json(
        self, data: dict[str, Any], warnings: list[str]
    ) -> PreferenceImportResponse:
        """Extract preferences from a parsed JSON dict."""
        known_keys = set(self.schema_registry.list_preference_keys())
        extracted: list[ExtractedPreference] = []

        for raw_key, value in data.items():
            # Try exact match first
            matched_key = raw_key if raw_key in known_keys else None

            # Fuzzy match: lowercase, strip common suffixes
            if matched_key is None:
                normalized = raw_key.lower().replace("-", "_").replace(" ", "_")
                for k in known_keys:
                    if k.lower() == normalized:
                        matched_key = k
                        break

            # Use original key if no schema match (user-defined preference)
            final_key = matched_key or raw_key

            # Validate against schema
            sensitive = self.schema_registry.is_sensitive(final_key)
            try:
                self.schema_registry.validate_value(final_key, value)
            except (ValidationError, Exception) as e:
                warnings.append(f"{final_key}: {e}")

            extracted.append(
                ExtractedPreference(
                    preference_key=final_key,
                    preference_value=value,
                    sensitive=sensitive,
                    confidence=1.0,
                    source_text=json.dumps({raw_key: value}),
                )
            )

        logger.info("JSON import: extracted %d preferences", len(extracted))
        return PreferenceImportResponse(
            extracted=extracted,
            raw_input_type="json",
            warnings=warnings,
        )

    async def _import_from_freetext(
        self, text: str, llm_adapter: Any, warnings: list[str]
    ) -> PreferenceImportResponse:
        """Extract preferences from free text using the LLM."""
        from ..adapters.preference_extractor import PreferenceExtractor

        extractor = PreferenceExtractor(llm_adapter)

        # Build known keys info for the LLM prompt
        known_keys_info: list[dict[str, Any]] = []
        for key in self.schema_registry.list_preference_keys():
            info = self.schema_registry.get_preference_info(key)
            known_keys_info.append(info)

        raw_results = await extractor.extract(text, known_keys_info)

        extracted: list[ExtractedPreference] = []
        for item in raw_results:
            key = item.get("key", "")
            value = item.get("value")
            confidence = float(item.get("confidence", 0.5))
            source_text = item.get("source_text")

            # Validate against schema
            sensitive = self.schema_registry.is_sensitive(key)
            try:
                self.schema_registry.validate_value(key, value)
            except (ValidationError, Exception) as e:
                warnings.append(f"{key}: {e}")

            extracted.append(
                ExtractedPreference(
                    preference_key=key,
                    preference_value=value,
                    sensitive=sensitive,
                    confidence=confidence,
                    source_text=source_text,
                )
            )

        logger.info("Free-text import: extracted %d preferences", len(extracted))
        return PreferenceImportResponse(
            extracted=extracted,
            raw_input_type="freetext",
            warnings=warnings,
        )
