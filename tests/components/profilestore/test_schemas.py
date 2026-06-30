"""
Tests for Preference Schema Validation

Tests schema registry loading, validation, and default values
using the actual SchemaRegistryAdapter API.
"""

import pytest

from components.ProfileStore.adapters.schema_registry import SchemaRegistryAdapter
from components.ProfileStore.domain.models import UnknownPreferenceError, ValidationError


@pytest.fixture
def schema_registry():
    """Create SchemaRegistryAdapter instance for tests."""
    return SchemaRegistryAdapter()


def test_valid_preference_passes_schema(schema_registry):
    """Test that valid preference value passes schema validation."""
    # Valid meeting duration (30 minutes, within 15-240 range)
    result = schema_registry.validate_value("meeting_duration_min", 30)
    assert result is True


def test_invalid_preference_fails_schema(schema_registry):
    """Test that invalid preference value fails schema validation."""
    # Invalid meeting duration (5 minutes, below minimum of 15)
    with pytest.raises(ValidationError) as exc_info:
        schema_registry.validate_value("meeting_duration_min", 5)

    assert exc_info.value.preference_key == "meeting_duration_min"
    assert "minimum" in str(exc_info.value.reason).lower()


def test_default_value_returned_for_missing(schema_registry):
    """Test that default value is returned when preference not set."""
    default = schema_registry.get_default_value("meeting_duration_min")
    assert default == 30  # From schema default


def test_unknown_preference_key_raises_error(schema_registry):
    """Test that unknown preference key raises error."""
    with pytest.raises(UnknownPreferenceError) as exc_info:
        schema_registry.get_schema("unknown_preference_key")

    assert exc_info.value.preference_key == "unknown_preference_key"


def test_get_schema_returns_valid_json_schema(schema_registry):
    """Test that get_schema returns valid JSON Schema."""
    schema = schema_registry.get_schema("meeting_duration_min")
    assert schema is not None
    assert schema["type"] == "integer"
    assert schema["minimum"] == 15
    assert schema["maximum"] == 240


def test_work_hours_pattern_validation(schema_registry):
    """Test work_hours pattern validation."""
    # Valid work hours
    result = schema_registry.validate_value("work_hours", "09:00-17:00")
    assert result is True

    # Invalid work hours (wrong format)
    with pytest.raises(ValidationError):
        schema_registry.validate_value("work_hours", "9am-5pm")


def test_passport_number_pattern_validation(schema_registry):
    """Test passport_number pattern validation."""
    # Valid passport number
    result = schema_registry.validate_value("passport_number", "AB123456")
    assert result is True

    # Invalid passport number (contains lowercase)
    with pytest.raises(ValidationError):
        schema_registry.validate_value("passport_number", "ab123456")
