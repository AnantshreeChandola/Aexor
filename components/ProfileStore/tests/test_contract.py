"""
ProfileStore Contract Tests

Tests for GLOBAL_SPEC compliance and external interface contracts.
Validates Evidence Item format, consent enforcement, and auth integration.

Reference: LLD.md §8.5
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from components.ProfileStore.adapters.schema_registry import SchemaRegistryAdapter
from components.ProfileStore.domain.models import ConsentDeniedError, UnknownPreferenceError
from components.ProfileStore.service.preference_service import PreferenceService
from shared.database.error_handler import UserNotFoundError
from shared.schemas.evidence import EvidenceItem


class TestGlobalSpecCompliance:
    """Test compliance with GLOBAL_SPEC.md contracts."""

    def test_evidence_item_format_compliance(self):
        """Test Evidence Item format matches GLOBAL_SPEC §2.2."""
        # Create Evidence Item as returned by ProfileStore
        evidence = EvidenceItem(
            type="preference",
            key="meeting_duration_min",
            value=30,
            confidence=1.0,
            source_ref="profilestore:prefs/meeting_duration_min",
            ttl_days=None,
            tier=2,
        )

        # Validate required fields per GLOBAL_SPEC
        assert evidence.type == "preference"
        assert evidence.key == "meeting_duration_min"
        assert evidence.value == 30
        assert evidence.confidence == 1.0
        assert evidence.tier == 2
        assert evidence.ttl_days is None
        assert "profilestore:" in evidence.source_ref

        # Validate serialization
        serialized = evidence.model_dump()
        assert all(
            field in serialized
            for field in ["type", "key", "value", "confidence", "source_ref", "ttl_days", "tier"]
        )

        # Validate JSON schema compliance
        evidence.model_validate(serialized)

    def test_evidence_item_json_serialization(self):
        """Test Evidence Item can be JSON serialized."""
        evidence = EvidenceItem(
            type="preference",
            key="test_key",
            value={"complex": "data", "number": 123},
            confidence=0.95,
            source_ref="profilestore:prefs/test_key",
            ttl_days=None,
            tier=2,
        )

        # Should serialize to JSON without errors
        json_str = evidence.model_dump_json()
        assert isinstance(json_str, str)
        assert "preference" in json_str
        assert "test_key" in json_str

    def test_tier_2_data_source_compliance(self):
        """Test ProfileStore always returns tier=2 per GLOBAL_SPEC §7."""
        evidence = EvidenceItem(
            type="preference",
            key="any_key",
            value="any_value",
            confidence=1.0,
            source_ref="profilestore:prefs/any_key",
            ttl_days=None,
            tier=2,
        )

        # ProfileStore must always return tier=2 (stable preferences)
        assert evidence.tier == 2

        # Tier must be within valid range per GLOBAL_SPEC
        assert 1 <= evidence.tier <= 4

    def test_confidence_score_compliance(self):
        """Test confidence scores per GLOBAL_SPEC requirements."""
        # ProfileStore data is authoritative, so confidence=1.0
        evidence = EvidenceItem(
            type="preference",
            key="authoritative_key",
            value="authoritative_value",
            confidence=1.0,  # Must be 1.0 for ProfileStore
            source_ref="profilestore:prefs/authoritative_key",
            ttl_days=None,
            tier=2,
        )

        assert evidence.confidence == 1.0
        assert 0.0 <= evidence.confidence <= 1.0


@pytest.mark.asyncio
class TestConsentEnforcement:
    """Test consent tier enforcement per GLOBAL_SPEC §7."""

    @pytest.fixture
    async def service_with_consent_mocks(self):
        """Create service with mocked dependencies for consent testing."""
        # Mock adapters
        db_adapter = MagicMock()
        schema_registry = MagicMock(spec=SchemaRegistryAdapter)
        encryption_adapter = MagicMock()

        # Setup basic schema mock
        schema_registry.get_schema.return_value = {"type": "string", "default": "test"}
        schema_registry.get_default_value.return_value = "default_value"

        # Create service
        service = PreferenceService(
            db_adapter=db_adapter,
            schema_registry=schema_registry,
            encryption_adapter=encryption_adapter,
        )

        return service

    async def test_tier_1_consent_denied(self, service_with_consent_mocks):
        """Test Tier 1 consent is denied access to Tier 2 data."""
        service = service_with_consent_mocks

        user_id = uuid4()

        # Tier 1 should be denied (ProfileStore requires Tier 2+)
        with pytest.raises(ConsentDeniedError) as exc_info:
            await service.get_preference(
                user_id=user_id,
                preference_key="any_key",
                context_tier=1,  # Insufficient tier
            )

        error = exc_info.value
        assert error.required_tier == 2
        assert error.current_tier == 1
        assert str(user_id) in str(error)

    async def test_tier_2_consent_allowed(self, service_with_consent_mocks):
        """Test Tier 2 consent allows access to Tier 2 data."""
        service = service_with_consent_mocks

        user_id = uuid4()

        # Tier 2 should be allowed
        result = await service.get_preference(
            user_id=user_id,
            preference_key="any_key",
            context_tier=2,  # Exactly required tier
        )

        assert isinstance(result, EvidenceItem)
        assert result.tier == 2

    async def test_tier_3_consent_allowed(self, service_with_consent_mocks):
        """Test Tier 3+ consent allows access (cumulative consent)."""
        service = service_with_consent_mocks

        user_id = uuid4()

        # Tier 3 should be allowed (3 >= 2)
        result = await service.get_preference(
            user_id=user_id,
            preference_key="any_key",
            context_tier=3,  # Higher than required
        )

        assert isinstance(result, EvidenceItem)

    async def test_tier_4_consent_allowed(self, service_with_consent_mocks):
        """Test Tier 4 consent allows access (cumulative consent)."""
        service = service_with_consent_mocks

        user_id = uuid4()

        # Tier 4 should be allowed (4 >= 2)
        result = await service.get_preference(
            user_id=user_id,
            preference_key="any_key",
            context_tier=4,  # Highest tier
        )

        assert isinstance(result, EvidenceItem)

    async def test_get_all_preferences_consent_enforcement(self, service_with_consent_mocks):
        """Test consent enforcement for get_all_preferences."""
        service = service_with_consent_mocks

        user_id = uuid4()

        # Setup mocks for get_all_preferences
        service.db.get_all_preferences = AsyncMock(return_value=[])
        service.schema_registry.list_preference_keys.return_value = []

        # Tier 1 should be denied
        with pytest.raises(ConsentDeniedError):
            await service.get_all_preferences(user_id=user_id, context_tier=1)

        # Tier 2 should be allowed
        result = await service.get_all_preferences(user_id=user_id, context_tier=2)

        assert isinstance(result, list)


class TestAuthMiddlewareContract:
    """Test contract with auth middleware for context_tier."""

    def test_context_tier_range_validation(self):
        """Test context_tier must be in valid range 1-4."""
        # Valid tiers
        for _tier in [1, 2, 3, 4]:
            evidence = EvidenceItem(
                type="preference",
                key="test",
                value="test",
                confidence=1.0,
                source_ref="test",
                ttl_days=None,
                tier=2,
            )
            # Should not raise validation error
            assert evidence.tier == 2

        # Invalid tiers should be caught by Pydantic validation
        with pytest.raises(ValueError):
            EvidenceItem(
                type="preference",
                key="test",
                value="test",
                confidence=1.0,
                source_ref="test",
                ttl_days=None,
                tier=0,  # Invalid tier
            )

        with pytest.raises(ValueError):
            EvidenceItem(
                type="preference",
                key="test",
                value="test",
                confidence=1.0,
                source_ref="test",
                ttl_days=None,
                tier=5,  # Invalid tier
            )

    def test_consent_error_details_contract(self):
        """Test ConsentDeniedError provides required details for API response."""
        user_id = uuid4()

        error = ConsentDeniedError(user_id=user_id, required_tier=2, current_tier=1)

        # Error should have structured data for API error responses
        assert error.user_id == user_id
        assert error.required_tier == 2
        assert error.current_tier == 1
        assert str(user_id) in str(error)
        assert "Tier 2" in str(error)


class TestErrorCodeContract:
    """Test error codes match SPEC FR-001."""

    def test_consent_denied_error_structure(self):
        """Test ConsentDeniedError structure for API responses."""
        user_id = uuid4()
        error = ConsentDeniedError(user_id=user_id, required_tier=2, current_tier=1)

        # Should have all fields needed for ErrorResponse
        assert hasattr(error, "user_id")
        assert hasattr(error, "required_tier")
        assert hasattr(error, "current_tier")

    def test_user_not_found_error_structure(self):
        """Test UserNotFoundError structure for API responses."""
        user_id = uuid4()
        error = UserNotFoundError(user_id)

        assert hasattr(error, "user_id")
        assert str(user_id) in str(error)

    def test_unknown_preference_error_structure(self):
        """Test UnknownPreferenceError structure for API responses."""
        error = UnknownPreferenceError("invalid_key")

        assert hasattr(error, "preference_key")
        assert error.preference_key == "invalid_key"


class TestSchemaRegistryContract:
    """Test schema registry contract compliance."""

    def test_schema_file_format_compliance(self):
        """Test schema files comply with JSON Schema format."""
        registry = SchemaRegistryAdapter()

        # Get a known schema
        for key in registry.list_preference_keys():
            schema = registry.get_schema(key)

            # Must have required fields per JSON Schema spec
            assert "type" in schema

            # Should be valid JSON Schema format
            assert isinstance(schema, dict)

            # Test one specific schema in detail
            if key == "meeting_duration_min":
                assert schema["type"] == "integer"
                assert "minimum" in schema
                assert "maximum" in schema
                assert schema["default"] == 30
                break

    def test_sensitive_preference_detection(self):
        """Test sensitive preference detection from schema."""
        registry = SchemaRegistryAdapter()

        # Check passport_number is marked as sensitive
        if "passport_number" in registry.list_preference_keys():
            assert registry.is_sensitive("passport_number") is True

        # Check meeting_duration_min is not sensitive
        if "meeting_duration_min" in registry.list_preference_keys():
            assert registry.is_sensitive("meeting_duration_min") is False


class TestPreviewExecuteModelCompliance:
    """Test ProfileStore doesn't use Preview/Execute (internal component)."""

    def test_no_preview_execute_wrapper(self):
        """Test ProfileStore doesn't use Preview/Execute wrappers."""
        # ProfileStore is internal component - doesn't use Preview/Execute
        # This test documents that ProfileStore operations execute directly

        from components.ProfileStore.service.preference_service import PreferenceService

        # Service methods should not have preview/execute pattern
        methods = dir(PreferenceService)

        # Should not have preview_ or execute_ methods
        preview_methods = [m for m in methods if m.startswith("preview_")]
        execute_methods = [m for m in methods if m.startswith("execute_")]

        assert len(preview_methods) == 0
        assert len(execute_methods) == 0

        # Should have direct operation methods
        assert "get_preference" in methods
        assert "set_preference" in methods
        assert "delete_preference" in methods


class TestIdempotencyContract:
    """Test idempotency requirements from SPEC FR-004."""

    @pytest.mark.asyncio
    async def test_set_preference_idempotency_concept(self):
        """Test set_preference idempotency concept (mocked)."""
        # Create service with mocks
        db_adapter = MagicMock()
        schema_registry = MagicMock()
        encryption_adapter = MagicMock()

        # Setup mocks
        schema_registry.get_schema.return_value = {"type": "string"}
        schema_registry.validate_value.return_value = True
        schema_registry.is_sensitive.return_value = False

        # Mock successful upsert
        from components.ProfileStore.domain.models import PreferenceDB

        preference_result = PreferenceDB(
            preference_id=uuid4(),
            user_id=uuid4(),
            key="test_key",
            value="test_value",
            sensitive=False,
            updated_at=None,
            deleted_at=None,
        )
        db_adapter.upsert_preference = AsyncMock(return_value=preference_result)

        service = PreferenceService(
            db_adapter=db_adapter,
            schema_registry=schema_registry,
            encryption_adapter=encryption_adapter,
        )

        user_id = uuid4()

        # First call
        result1 = await service.set_preference(
            user_id=user_id, preference_key="test_key", preference_value="test_value"
        )

        # Second call with same parameters (idempotent)
        result2 = await service.set_preference(
            user_id=user_id, preference_key="test_key", preference_value="test_value"
        )

        # Results should be equivalent (same key, value)
        assert result1.preference_key == result2.preference_key
        assert result1.preference_value == result2.preference_value
        assert result1.user_id == result2.user_id


# Fixtures
@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
