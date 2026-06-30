"""
Tests for GLOBAL_SPEC §2.2 Evidence Item Contract Conformance

Tests that ProfileStore returns preferences in correct Evidence Item format.
Uses mocked adapters to test the service contract without a database.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from components.ProfileStore.adapters.schema_registry import SchemaRegistryAdapter
from components.ProfileStore.domain.models import PreferenceDB
from components.ProfileStore.service.preference_service import PreferenceService
from shared.schemas.evidence import EvidenceItem


@pytest.fixture
def preference_service():
    """Create PreferenceService with mocked dependencies for contract tests."""
    db_adapter = MagicMock()
    schema_registry = MagicMock(spec=SchemaRegistryAdapter)
    encryption_adapter = MagicMock()

    # Setup schema mock for known preferences
    schema_registry.get_schema.return_value = {"type": "integer", "minimum": 15, "maximum": 240}
    schema_registry.get_default_value.return_value = 30
    schema_registry.is_sensitive.return_value = False
    schema_registry.validate_value.return_value = True
    schema_registry.list_preference_keys.return_value = ["meeting_duration_min", "work_hours"]

    # Setup async db mock
    db_adapter.get_preference = AsyncMock(
        return_value=PreferenceDB(
            preference_id=uuid4(),
            user_id=uuid4(),
            key="meeting_duration_min",
            value=30,
            sensitive=False,
            updated_at=datetime.utcnow(),
            deleted_at=None,
        )
    )
    db_adapter.upsert_preference = AsyncMock()

    return PreferenceService(
        db_adapter=db_adapter,
        schema_registry=schema_registry,
        encryption_adapter=encryption_adapter,
    )


@pytest.fixture
def test_user_id():
    """Test user ID."""
    return uuid4()


@pytest.mark.asyncio
async def test_evidence_item_has_required_fields(preference_service, test_user_id):
    """Test that returned Evidence Item has all required fields."""
    evidence = await preference_service.get_preference(
        user_id=test_user_id, preference_key="meeting_duration_min", context_tier=2
    )

    # Verify all required fields present
    assert hasattr(evidence, "type")
    assert hasattr(evidence, "key")
    assert hasattr(evidence, "value")
    assert hasattr(evidence, "confidence")
    assert hasattr(evidence, "source_ref")
    assert hasattr(evidence, "tier")


@pytest.mark.asyncio
async def test_evidence_type_is_preference(preference_service, test_user_id):
    """Test that Evidence Item type is 'preference'."""
    evidence = await preference_service.get_preference(
        user_id=test_user_id, preference_key="meeting_duration_min", context_tier=2
    )

    assert evidence.type == "preference"


@pytest.mark.asyncio
async def test_evidence_confidence_is_1_0(preference_service, test_user_id):
    """Test that confidence is always 1.0 for stored preferences."""
    evidence = await preference_service.get_preference(
        user_id=test_user_id, preference_key="meeting_duration_min", context_tier=2
    )

    assert evidence.confidence == 1.0


@pytest.mark.asyncio
async def test_evidence_tier_is_2(preference_service, test_user_id):
    """Test that tier is always 2 for ProfileStore (Tier 2 data source)."""
    evidence = await preference_service.get_preference(
        user_id=test_user_id, preference_key="meeting_duration_min", context_tier=2
    )

    assert evidence.tier == 2


@pytest.mark.asyncio
async def test_source_ref_format_correct(preference_service, test_user_id):
    """Test that source_ref follows format: profilestore:prefs/{key}."""
    evidence = await preference_service.get_preference(
        user_id=test_user_id, preference_key="meeting_duration_min", context_tier=2
    )

    assert evidence.source_ref == "profilestore:prefs/meeting_duration_min"


@pytest.mark.asyncio
async def test_evidence_item_validates_against_schema(preference_service, test_user_id):
    """Test that Evidence Item can be validated by Pydantic model."""
    evidence = await preference_service.get_preference(
        user_id=test_user_id, preference_key="meeting_duration_min", context_tier=2
    )

    # Should be valid EvidenceItem instance
    assert isinstance(evidence, EvidenceItem)

    # Should serialize to JSON correctly
    json_str = evidence.model_dump_json()
    assert isinstance(json_str, str)
    assert "preference" in json_str
