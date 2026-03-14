"""
Tests for GLOBAL_SPEC §2.2 Evidence Item Contract Conformance

Tests that ProfileStore returns preferences in correct Evidence Item format.
These tests will FAIL until PreferenceService is implemented (TDD Red phase).
"""

from uuid import uuid4

import pytest

from components.ProfileStore.service.preference_service import PreferenceService
from shared.schemas.evidence import EvidenceItem


@pytest.fixture
def preference_service():
    """Create PreferenceService instance for contract tests."""
    # Will be implemented in Phase 6
    return PreferenceService()


@pytest.fixture
def test_user_id():
    """Test user ID."""
    return uuid4()


def test_evidence_item_has_required_fields(preference_service, test_user_id):
    """Test that returned Evidence Item has all required fields."""
    # Set a preference
    preference_service.set_preference(
        user_id=test_user_id, context_tier=2, key="meeting_duration_min", value=30, sensitive=False
    )

    # Get preference as Evidence Item
    evidence = preference_service.get_preference(
        user_id=test_user_id, context_tier=2, key="meeting_duration_min"
    )

    # Verify all required fields present
    assert hasattr(evidence, "type")
    assert hasattr(evidence, "key")
    assert hasattr(evidence, "value")
    assert hasattr(evidence, "confidence")
    assert hasattr(evidence, "source_ref")
    assert hasattr(evidence, "tier")


def test_evidence_type_is_preference(preference_service, test_user_id):
    """Test that Evidence Item type is 'preference'."""
    preference_service.set_preference(
        user_id=test_user_id, context_tier=2, key="meeting_duration_min", value=30, sensitive=False
    )

    evidence = preference_service.get_preference(
        user_id=test_user_id, context_tier=2, key="meeting_duration_min"
    )

    assert evidence.type == "preference"


def test_evidence_confidence_is_1_0(preference_service, test_user_id):
    """Test that confidence is always 1.0 for stored preferences."""
    preference_service.set_preference(
        user_id=test_user_id, context_tier=2, key="work_hours", value="09:00-17:00", sensitive=False
    )

    evidence = preference_service.get_preference(
        user_id=test_user_id, context_tier=2, key="work_hours"
    )

    assert evidence.confidence == 1.0


def test_evidence_tier_is_2(preference_service, test_user_id):
    """Test that tier is always 2 for ProfileStore (Tier 2 data source)."""
    preference_service.set_preference(
        user_id=test_user_id, context_tier=2, key="meeting_duration_min", value=60, sensitive=False
    )

    evidence = preference_service.get_preference(
        user_id=test_user_id, context_tier=2, key="meeting_duration_min"
    )

    assert evidence.tier == 2


def test_source_ref_format_correct(preference_service, test_user_id):
    """Test that source_ref follows format: profilestore:prefs/{key}."""
    preference_service.set_preference(
        user_id=test_user_id, context_tier=2, key="work_hours", value="10:00-18:00", sensitive=False
    )

    evidence = preference_service.get_preference(
        user_id=test_user_id, context_tier=2, key="work_hours"
    )

    assert evidence.source_ref == "profilestore:prefs/work_hours"


def test_evidence_item_validates_against_schema(preference_service, test_user_id):
    """Test that Evidence Item can be validated by Pydantic model."""
    preference_service.set_preference(
        user_id=test_user_id, context_tier=2, key="meeting_duration_min", value=45, sensitive=False
    )

    evidence = preference_service.get_preference(
        user_id=test_user_id, context_tier=2, key="meeting_duration_min"
    )

    # Should be valid EvidenceItem instance
    assert isinstance(evidence, EvidenceItem)

    # Should serialize to JSON correctly
    json_str = evidence.model_dump_json()
    assert isinstance(json_str, str)
    assert "preference" in json_str
