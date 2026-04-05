"""
Audit Contract Tests

Schema conformance, table-model alignment, no PII/secrets,
consumer contracts. Uses FakeAuditDB -- no real database.

Reference: GLOBAL_SPEC conformance, schema validation, no PII
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
import ulid

from components.Audit.domain.models import (
    AuditEvent,
    AuditEventType,
    AuditQueryParams,
    AuditQueryResult,
)
from components.Audit.service.audit_service import AuditService, AuditServiceProtocol
from components.Audit.tests.conftest import (
    FakeAuditDB,
    make_event,
)

SCHEMAS_DIR = Path(__file__).parent.parent / "schemas"


def _load_schema(name: str) -> dict:
    """Load a JSON schema from the schemas directory."""
    schema_path = SCHEMAS_DIR / name
    with schema_path.open() as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Schema conformance tests (~8 tests)
# ---------------------------------------------------------------------------


class TestSchemaConformance:
    """Validate Pydantic models against JSON schemas."""

    def test_audit_event_validates_against_json_schema(self):
        """AuditEvent.model_dump() conforms to audit_event.schema.json."""
        schema = _load_schema("audit_event.schema.json")
        event = make_event(
            event_type=AuditEventType.EXECUTION_STARTED,
            event_data={"total_steps": 5},
        )
        data = event.model_dump(mode="json")
        jsonschema.validate(instance=data, schema=schema)

    def test_audit_query_result_validates_against_json_schema(self):
        """AuditQueryResult conforms to audit_query.schema.json."""
        schema = _load_schema("audit_query.schema.json")
        event = make_event()
        result = AuditQueryResult(
            events=[event],
            next_cursor=None,
            total_count=1,
        )
        data = result.model_dump(mode="json")
        # Resolve $ref manually for inline validation
        event_schema = _load_schema("audit_event.schema.json")
        resolver = jsonschema.RefResolver(
            base_uri=f"file://{SCHEMAS_DIR}/",
            referrer=schema,
            store={
                "audit_event.schema.json": event_schema,
            },
        )
        jsonschema.validate(
            instance=data,
            schema=schema,
            resolver=resolver,
        )

    def test_event_id_is_26_char_ulid(self):
        """event_id must be a 26-character ULID."""
        event = make_event()
        assert len(event.event_id) == 26
        # Verify it parses as a valid ULID
        parsed = ulid.parse(event.event_id)
        assert parsed is not None

    def test_event_type_is_valid_enum(self):
        """All 11 AuditEventType values are accepted."""
        assert len(AuditEventType) == 11
        for et in AuditEventType:
            event = make_event(event_type=et)
            assert event.event_type == et

    def test_invalid_event_type_rejected(self):
        """Invalid event_type raises Pydantic ValidationError."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            AuditEvent(
                event_id=ulid.new().str,
                event_type="not_a_valid_type",
                event_data={},
            )

    def test_limit_capped_at_200(self):
        """AuditQueryParams rejects limit > 200."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            AuditQueryParams(limit=201)

    def test_limit_minimum_1(self):
        """AuditQueryParams rejects limit < 1."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            AuditQueryParams(limit=0)

    def test_created_at_is_utc_iso8601(self):
        """created_at is timezone-aware UTC datetime."""
        event = make_event()
        assert event.created_at.tzinfo is not None
        data = event.model_dump(mode="json")
        # ISO 8601 format contains timezone info
        assert isinstance(data["created_at"], str)


# ---------------------------------------------------------------------------
# Table-model alignment tests (~5 tests)
# ---------------------------------------------------------------------------


class TestTableModelAlignment:
    """Verify AuditEventTable columns match AuditEvent fields."""

    def test_audit_event_table_columns_match_model(self):
        """AuditEventTable columns match AuditEvent fields."""
        from shared.database.models import AuditEventTable

        table_cols = {c.name for c in AuditEventTable.__table__.columns}
        model_fields = set(AuditEvent.model_fields.keys())
        assert model_fields == table_cols

    def test_audit_event_table_has_plan_id_index(self):
        """plan_id index exists on AuditEventTable."""
        from shared.database.models import AuditEventTable

        index_names = {idx.name for idx in AuditEventTable.__table__.indexes}
        assert "idx_audit_events_plan_id" in index_names

    def test_audit_event_table_has_user_id_index(self):
        """user_id index exists on AuditEventTable."""
        from shared.database.models import AuditEventTable

        index_names = {idx.name for idx in AuditEventTable.__table__.indexes}
        assert "idx_audit_events_user_id" in index_names

    def test_audit_event_table_has_trace_id_index(self):
        """trace_id index exists on AuditEventTable."""
        from shared.database.models import AuditEventTable

        index_names = {idx.name for idx in AuditEventTable.__table__.indexes}
        assert "idx_audit_events_trace_id" in index_names

    def test_audit_event_table_has_event_type_index(self):
        """event_type index exists on AuditEventTable."""
        from shared.database.models import AuditEventTable

        index_names = {idx.name for idx in AuditEventTable.__table__.indexes}
        assert "idx_audit_events_event_type" in index_names


# ---------------------------------------------------------------------------
# No PII/secrets tests (~7 tests)
# ---------------------------------------------------------------------------


class TestNoPII:
    """Verify PII/secret sanitization."""

    def _make_service(self) -> tuple[AuditService, FakeAuditDB]:
        db = FakeAuditDB()
        svc = AuditService(db_adapter=db)
        return svc, db

    @pytest.mark.asyncio
    async def test_sanitize_strips_password_field(self):
        """password key removed from event_data."""
        svc, _db = self._make_service()
        event = make_event(event_data={"password": "x", "role": "a"})
        await svc.record(event)
        assert "password" not in event.event_data

    @pytest.mark.asyncio
    async def test_sanitize_strips_secret_field(self):
        """secret key removed from event_data."""
        svc, _ = self._make_service()
        event = make_event(event_data={"secret": "x"})
        await svc.record(event)
        assert "secret" not in event.event_data

    @pytest.mark.asyncio
    async def test_sanitize_strips_token_field(self):
        """token key removed from event_data."""
        svc, _ = self._make_service()
        event = make_event(event_data={"token": "x"})
        await svc.record(event)
        assert "token" not in event.event_data

    @pytest.mark.asyncio
    async def test_sanitize_strips_credential_field(self):
        """credential key removed from event_data."""
        svc, _ = self._make_service()
        event = make_event(event_data={"credential": "x"})
        await svc.record(event)
        assert "credential" not in event.event_data

    @pytest.mark.asyncio
    async def test_sanitize_strips_api_key_field(self):
        """api_key key removed from event_data."""
        svc, _ = self._make_service()
        event = make_event(event_data={"api_key": "x"})
        await svc.record(event)
        assert "api_key" not in event.event_data

    @pytest.mark.asyncio
    async def test_sanitize_case_insensitive(self):
        """Password, SECRET, Token all stripped (case-insensitive)."""
        svc, _ = self._make_service()
        event = make_event(
            event_data={
                "Password": "x",
                "SECRET": "y",
                "Token": "z",
                "role": "kept",
            },
        )
        await svc.record(event)
        assert "Password" not in event.event_data
        assert "SECRET" not in event.event_data
        assert "Token" not in event.event_data
        assert event.event_data["role"] == "kept"

    @pytest.mark.asyncio
    async def test_sanitize_preserves_non_sensitive_fields(self):
        """role, status, latency_ms kept after sanitization."""
        svc, _ = self._make_service()
        event = make_event(
            event_data={
                "role": "Fetcher",
                "status": "success",
                "latency_ms": 100,
                "password": "leaked",
            },
        )
        await svc.record(event)
        assert event.event_data["role"] == "Fetcher"
        assert event.event_data["status"] == "success"
        assert event.event_data["latency_ms"] == 100
        assert "password" not in event.event_data


# ---------------------------------------------------------------------------
# Consumer contract tests (~5 tests)
# ---------------------------------------------------------------------------


class TestConsumerContracts:
    """Verify AuditService implements AuditServiceProtocol."""

    def test_record_matches_audit_service_protocol(self):
        """AuditService implements AuditServiceProtocol."""
        db = FakeAuditDB()
        svc = AuditService(db_adapter=db)
        assert isinstance(svc, AuditServiceProtocol)

    def test_query_matches_audit_service_protocol(self):
        """query method signature matches protocol."""
        assert hasattr(AuditService, "query")

    def test_flush_matches_audit_service_protocol(self):
        """flush method exists on AuditService."""
        assert hasattr(AuditService, "flush")

    @pytest.mark.asyncio
    async def test_record_is_fire_and_forget(self):
        """record() returns None, never raises."""
        db = FakeAuditDB()
        db.set_should_fail(True)
        svc = AuditService(db_adapter=db, flush_threshold=1)
        result = await svc.record(make_event())
        assert result is None

    @pytest.mark.asyncio
    async def test_all_11_event_types_recordable(self):
        """Each AuditEventType can be recorded without error."""
        db = FakeAuditDB()
        svc = AuditService(db_adapter=db, flush_threshold=100)
        for et in AuditEventType:
            await svc.record(make_event(event_type=et))
        assert svc.buffer_size == 11
