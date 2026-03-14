"""
Tests for Preference Schema Validation

Tests schema registry loading, validation, and default values.
These tests will FAIL until SchemaRegistry is implemented (TDD Red phase).
"""

import pytest

from components.ProfileStore.adapters.schema_registry import SchemaRegistry


@pytest.fixture
def schema_registry():
    """Create SchemaRegistry instance for tests."""
    return SchemaRegistry()


def test_valid_preference_passes_schema(schema_registry):
    """Test that valid preference value passes schema validation."""
    # Valid meeting duration (30 minutes, within 15-240 range)
    result = schema_registry.validate_value("meeting_duration_min", 30)
    assert result.is_valid is True
    assert result.errors == []


def test_invalid_preference_fails_schema(schema_registry):
    """Test that invalid preference value fails schema validation."""
    # Invalid meeting duration (5 minutes, below minimum of 15)
    result = schema_registry.validate_value("meeting_duration_min", 5)
    assert result.is_valid is False
    assert len(result.errors) > 0
    assert "minimum" in str(result.errors[0]).lower()


def test_default_value_returned_for_missing(schema_registry):
    """Test that default value is returned when preference not set."""
    # Get default for meeting_duration_min
    default = schema_registry.get_default("meeting_duration_min")
    assert default == 30  # From schema default


def test_unknown_preference_key_raises_error(schema_registry):
    """Test that unknown preference key raises error."""
    with pytest.raises(ValueError, match="Unknown preference key"):
        schema_registry.get_schema("unknown_preference_key")


def test_get_schema_returns_valid_json_schema(schema_registry):
    """Test that get_schema returns valid JSON Schema."""
    schema = schema_registry.get_schema("meeting_duration_min")
    assert schema is not None
    assert "$schema" in schema
    assert schema["type"] == "integer"
    assert schema["minimum"] == 15
    assert schema["maximum"] == 240
    assert schema["default"] == 30


def test_work_hours_pattern_validation(schema_registry):
    """Test work_hours pattern validation."""
    # Valid work hours
    result = schema_registry.validate_value("work_hours", "09:00-17:00")
    assert result.is_valid is True

    # Invalid work hours (wrong format)
    result = schema_registry.validate_value("work_hours", "9am-5pm")
    assert result.is_valid is False


def test_passport_number_pattern_validation(schema_registry):
    """Test passport_number pattern validation."""
    # Valid passport number
    result = schema_registry.validate_value("passport_number", "AB123456")
    assert result.is_valid is True

    # Invalid passport number (contains lowercase)
    result = schema_registry.validate_value("passport_number", "ab123456")
    assert result.is_valid is False
