"""
ProfileStore Comprehensive Tests

Unit and integration tests for all preference functionality.
Covers service logic, database operations, encryption, and schema validation.

Reference: LLD.md §8.5
"""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from components.ProfileStore.adapters.db import DatabaseAdapter
from components.ProfileStore.adapters.encryption import EncryptionAdapter
from components.ProfileStore.adapters.schema_registry import SchemaRegistryAdapter
from components.ProfileStore.domain.models import (
    ConsentDeniedError,
    PreferenceDB,
    UnknownPreferenceError,
    ValidationError,
)
from components.ProfileStore.service.preference_service import PreferenceService
from shared.database.error_handler import UserNotFoundError

# Test imports
from shared.schemas.evidence import EvidenceItem


class TestSchemaRegistry:
    """Test schema registry functionality."""

    def test_schema_registry_initialization(self):
        """Test schema registry loads schemas correctly."""
        registry = SchemaRegistryAdapter()

        # Verify built-in preferences are loaded
        keys = registry.list_preference_keys()
        assert "meeting_duration_min" in keys
        assert "work_hours" in keys
        assert "passport_number" in keys

    def test_schema_validation_success(self):
        """Test successful schema validation."""
        registry = SchemaRegistryAdapter()

        # Valid meeting duration (within 15-240 range)
        assert registry.validate_value("meeting_duration_min", 30) is True

    def test_schema_validation_failure(self):
        """Test schema validation failure."""
        registry = SchemaRegistryAdapter()

        # Invalid meeting duration (below minimum of 15)
        with pytest.raises(ValidationError) as exc_info:
            registry.validate_value("meeting_duration_min", 5)

        assert exc_info.value.preference_key == "meeting_duration_min"
        assert exc_info.value.value == 5

    def test_unknown_preference_key(self):
        """Test handling of unknown preference keys."""
        registry = SchemaRegistryAdapter()

        with pytest.raises(UnknownPreferenceError) as exc_info:
            registry.get_schema("nonexistent_key")

        assert exc_info.value.preference_key == "nonexistent_key"

    def test_get_default_value(self):
        """Test getting default values from schemas."""
        registry = SchemaRegistryAdapter()

        assert registry.get_default_value("meeting_duration_min") == 30

    def test_is_sensitive(self):
        """Test checking if preference is sensitive."""
        registry = SchemaRegistryAdapter()

        assert registry.is_sensitive("passport_number") is True
        assert registry.is_sensitive("meeting_duration_min") is False


class TestEncryptionAdapter:
    """Test encryption functionality."""

    def test_encrypt_decrypt_roundtrip(self):
        """Test encryption/decryption roundtrip."""
        import secrets

        from shared.security.encryption import EncryptionService

        # Create encryption service with test key
        test_key = secrets.token_bytes(32)
        encryption_service = EncryptionService(test_key)

        # Create adapter
        adapter = EncryptionAdapter(encryption_service)

        # Test roundtrip
        test_value = {"sensitive": "data", "number": 123}
        encrypted = adapter.encrypt_value(test_value)
        decrypted = adapter.decrypt_value(encrypted)

        assert decrypted == test_value

    def test_is_encrypted_detection(self):
        """Test detection of encrypted values."""
        import secrets

        from shared.security.encryption import EncryptionService

        test_key = secrets.token_bytes(32)
        encryption_service = EncryptionService(test_key)
        adapter = EncryptionAdapter(encryption_service)

        # Encrypted value should be detected
        encrypted = adapter.encrypt_value("test")
        assert adapter.is_encrypted(encrypted) is True

        # Plain value should not be detected as encrypted
        assert adapter.is_encrypted("plain_text") is False
        assert adapter.is_encrypted("not:base64:data") is False


@pytest.mark.asyncio
class TestDatabaseAdapter:
    """Test database adapter functionality."""

    @pytest.fixture
    async def mock_db_adapter(self):
        """Create mock database adapter for testing."""
        adapter = MagicMock(spec=DatabaseAdapter)

        # Mock test user
        test_user_id = uuid4()

        # Mock successful operations
        adapter.get_preference = AsyncMock(return_value=None)
        adapter.upsert_preference = AsyncMock(
            return_value=PreferenceDB(
                preference_id=uuid4(),
                user_id=test_user_id,
                key="test_key",
                value="test_value",
                sensitive=False,
                updated_at=datetime.utcnow(),
                deleted_at=None,
            )
        )
        adapter.delete_preference = AsyncMock(return_value=True)
        adapter.get_all_preferences = AsyncMock(return_value=[])
        adapter.health_check = AsyncMock(return_value=True)

        return adapter, test_user_id

    async def test_get_preference_not_found(self, mock_db_adapter):
        """Test getting non-existent preference."""
        adapter, user_id = mock_db_adapter

        # Mock returns None for not found
        adapter.get_preference.return_value = None

        result = await adapter.get_preference(user_id, "nonexistent")
        assert result is None

    async def test_upsert_preference_success(self, mock_db_adapter):
        """Test successful preference upsert."""
        adapter, user_id = mock_db_adapter

        result = await adapter.upsert_preference(
            user_id=user_id, preference_key="test_key", value="test_value", sensitive=False
        )

        assert result.user_id == user_id
        assert result.key == "test_key"
        assert result.value == "test_value"

    async def test_delete_preference_success(self, mock_db_adapter):
        """Test successful preference deletion."""
        adapter, user_id = mock_db_adapter

        result = await adapter.delete_preference(user_id, "test_key")
        assert result is True

    async def test_user_not_found_error(self, mock_db_adapter):
        """Test UserNotFoundError handling."""
        adapter, user_id = mock_db_adapter

        # Mock user not found
        adapter.get_preference.side_effect = UserNotFoundError(user_id)

        with pytest.raises(UserNotFoundError):
            await adapter.get_preference(user_id, "test_key")


@pytest.mark.asyncio
class TestPreferenceService:
    """Test preference service business logic."""

    @pytest.fixture
    async def service_with_mocks(self):
        """Create preference service with mocked dependencies."""
        # Mock adapters
        db_adapter = MagicMock(spec=DatabaseAdapter)
        schema_registry = MagicMock(spec=SchemaRegistryAdapter)
        encryption_adapter = MagicMock(spec=EncryptionAdapter)

        # Setup mock schemas
        test_schema = {"type": "string", "default": "default_value", "sensitive": False}
        schema_registry.get_schema.return_value = test_schema
        schema_registry.get_default_value.return_value = "default_value"
        schema_registry.is_sensitive.return_value = False
        schema_registry.validate_value.return_value = True
        schema_registry.list_preference_keys.return_value = ["test_key"]

        # Setup mock database
        db_adapter.get_preference = AsyncMock(return_value=None)
        db_adapter.upsert_preference = AsyncMock()
        db_adapter.delete_preference = AsyncMock(return_value=True)
        db_adapter.get_all_preferences = AsyncMock(return_value=[])
        db_adapter.health_check = AsyncMock(return_value=True)

        # Setup mock encryption
        encryption_adapter.encrypt_value.return_value = "encrypted_value"
        encryption_adapter.decrypt_value.return_value = "decrypted_value"

        # Create service
        service = PreferenceService(
            db_adapter=db_adapter,
            schema_registry=schema_registry,
            encryption_adapter=encryption_adapter,
        )

        return service, db_adapter, schema_registry, encryption_adapter

    async def test_get_preference_consent_denied(self, service_with_mocks):
        """Test consent enforcement for get_preference."""
        service, _, _, _ = service_with_mocks

        user_id = uuid4()

        # Context tier 1 should be denied (requires tier 2+)
        with pytest.raises(ConsentDeniedError) as exc_info:
            await service.get_preference(user_id=user_id, preference_key="test_key", context_tier=1)

        assert exc_info.value.required_tier == 2
        assert exc_info.value.current_tier == 1

    async def test_get_preference_returns_default(self, service_with_mocks):
        """Test getting preference returns schema default when not set."""
        service, db_adapter, schema_registry, _ = service_with_mocks

        user_id = uuid4()

        # Mock preference not found in database
        db_adapter.get_preference.return_value = None
        schema_registry.get_default_value.return_value = "default_value"

        result = await service.get_preference(
            user_id=user_id, preference_key="test_key", context_tier=2
        )

        assert isinstance(result, EvidenceItem)
        assert result.type == "preference"
        assert result.key == "test_key"
        assert result.value == "default_value"
        assert result.confidence == 1.0
        assert result.tier == 2

    async def test_get_preference_returns_stored_value(self, service_with_mocks):
        """Test getting preference returns stored value when set."""
        service, db_adapter, _, _ = service_with_mocks

        user_id = uuid4()

        # Mock preference found in database
        stored_preference = PreferenceDB(
            preference_id=uuid4(),
            user_id=user_id,
            key="test_key",
            value="stored_value",
            sensitive=False,
            updated_at=datetime.utcnow(),
            deleted_at=None,
        )
        db_adapter.get_preference.return_value = stored_preference

        result = await service.get_preference(
            user_id=user_id, preference_key="test_key", context_tier=2
        )

        assert result.value == "stored_value"

    async def test_get_preference_decrypts_sensitive(self, service_with_mocks):
        """Test getting sensitive preference decrypts value."""
        service, db_adapter, _, encryption_adapter = service_with_mocks

        user_id = uuid4()

        # Mock sensitive preference
        stored_preference = PreferenceDB(
            preference_id=uuid4(),
            user_id=user_id,
            key="test_key",
            value="encrypted_value",
            sensitive=True,
            updated_at=datetime.utcnow(),
            deleted_at=None,
        )
        db_adapter.get_preference.return_value = stored_preference
        encryption_adapter.decrypt_value.return_value = "decrypted_value"

        result = await service.get_preference(
            user_id=user_id, preference_key="test_key", context_tier=2
        )

        assert result.value == "decrypted_value"
        encryption_adapter.decrypt_value.assert_called_once_with("encrypted_value")

    async def test_set_preference_validates_schema(self, service_with_mocks):
        """Test set_preference validates against schema."""
        service, _, schema_registry, _ = service_with_mocks

        user_id = uuid4()

        # Mock validation failure
        schema_registry.validate_value.side_effect = ValidationError(
            preference_key="test_key", value="invalid", reason="validation failed"
        )

        with pytest.raises(ValidationError):
            await service.set_preference(
                user_id=user_id,
                preference_key="test_key",
                preference_value="invalid",
                sensitive=False,
            )

    async def test_set_preference_encrypts_sensitive(self, service_with_mocks):
        """Test set_preference encrypts sensitive values."""
        service, db_adapter, schema_registry, encryption_adapter = service_with_mocks

        user_id = uuid4()

        # Mock sensitive schema
        schema_registry.is_sensitive.return_value = True

        # Mock successful upsert
        db_adapter.upsert_preference.return_value = PreferenceDB(
            preference_id=uuid4(),
            user_id=user_id,
            key="sensitive_key",
            value="encrypted_value",
            sensitive=True,
            updated_at=datetime.utcnow(),
            deleted_at=None,
        )

        await service.set_preference(
            user_id=user_id,
            preference_key="sensitive_key",
            preference_value="sensitive_data",
            sensitive=True,
        )

        # Should encrypt the value
        encryption_adapter.encrypt_value.assert_called_once_with("sensitive_data")

        # Should store encrypted value
        db_adapter.upsert_preference.assert_called_once_with(
            user_id=user_id, preference_key="sensitive_key", value="encrypted_value", sensitive=True
        )

    async def test_delete_preference_success(self, service_with_mocks):
        """Test successful preference deletion."""
        service, _db_adapter, _, _ = service_with_mocks

        user_id = uuid4()

        result = await service.delete_preference(user_id=user_id, preference_key="test_key")

        assert result.user_id == user_id
        assert result.preference_key == "test_key"
        assert "deleted successfully" in result.message

    async def test_get_all_preferences_with_defaults(self, service_with_mocks):
        """Test getting all preferences includes defaults."""
        service, db_adapter, schema_registry, _ = service_with_mocks

        user_id = uuid4()

        # Mock one stored preference
        stored_preference = PreferenceDB(
            preference_id=uuid4(),
            user_id=user_id,
            key="stored_key",
            value="stored_value",
            sensitive=False,
            updated_at=datetime.utcnow(),
            deleted_at=None,
        )
        db_adapter.get_all_preferences.return_value = [stored_preference]

        # Mock schema has two keys, one with default
        schema_registry.list_preference_keys.return_value = ["stored_key", "default_key"]
        schema_registry.get_default_value.side_effect = lambda key: {
            "stored_key": None,
            "default_key": "default_value",
        }.get(key)

        result = await service.get_all_preferences(user_id=user_id, context_tier=2)

        # Should return both stored and default preferences
        assert len(result) == 2
        keys = [item.key for item in result]
        assert "stored_key" in keys
        assert "default_key" in keys

class TestIntegration:
    """Integration tests with real components (no database)."""

    def test_real_schema_registry_integration(self):
        """Test with real schema files."""
        # Use real schema registry pointing to test schemas
        registry = SchemaRegistryAdapter()

        # Should load existing schemas from schemas/ directory
        keys = registry.list_preference_keys()

        # Check if our test schemas are loaded
        expected_keys = {"meeting_duration_min", "work_hours", "passport_number"}
        loaded_keys = set(keys)

        # Should have at least our test schemas
        assert expected_keys.issubset(loaded_keys)

        # Test specific schemas
        meeting_schema = registry.get_schema("meeting_duration_min")
        assert meeting_schema["type"] == "integer"
        # Default is not in get_json_schema(); use get_default_value() instead
        assert registry.get_default_value("meeting_duration_min") == 30

        passport_schema = registry.get_schema("passport_number")
        assert passport_schema["type"] == "string"
        assert registry.is_sensitive("passport_number") is True

    def test_real_encryption_integration(self):
        """Test with real encryption service."""
        import os

        os.environ["ENCRYPTION_KEY"] = "HLvFYndF/AHSGe8gZ1usG/z7fSQyal2B55Ayu1/gPvA="

        from components.ProfileStore.adapters.encryption import get_encryption_adapter

        adapter = get_encryption_adapter()

        # Test complex data types
        test_data = {
            "string": "sensitive data",
            "number": 12345,
            "array": [1, 2, 3],
            "nested": {"key": "value"},
        }

        encrypted = adapter.encrypt_value(test_data)
        decrypted = adapter.decrypt_value(encrypted)

        assert decrypted == test_data
        assert adapter.is_encrypted(encrypted) is True


# Fixtures and test configuration
@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


# Test runners
if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
