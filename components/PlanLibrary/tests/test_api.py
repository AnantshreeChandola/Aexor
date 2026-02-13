"""
PlanLibrary API Handler Tests

Tests for API routes as thin wrappers around service layer.
Uses mocked services following ProfileStore test patterns.

Reference: tasks.md T402
"""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from shared.schemas.evidence import EvidenceItem

from components.PlanLibrary.api.routes import router
from components.PlanLibrary.domain.models import (
    DuplicatePlanError,
    InvalidSignatureError,
    PlanDB,
    PlanNotFoundError,
    PlanTooLargeError,
    StorePlanResponse,
)


VALID_ULID = "01HX1234567890ABCDEFGHJKMN"


def _create_test_app():
    """Create FastAPI app with PlanLibrary routes for testing."""
    app = FastAPI()
    app.include_router(router)
    return app


def _make_store_request():
    """Create valid store plan request body."""
    return {
        "plan": {
            "plan_id": VALID_ULID,
            "graph": [{"step": 1}],
            "meta": {"intent_type": "test"},
        },
        "signature": {
            "algorithm": "ed25519",
            "public_key": "abc",
            "signature_hex": "def",
        },
        "outcome": {
            "success": True,
            "execution_start": "2025-01-01T00:00:00",
            "execution_end": "2025-01-01T00:01:00",
            "total_steps": 1,
        },
        "metrics": {
            "execute_latency_ms": 500,
        },
    }


class TestStorePlanEndpoint:
    """Tests for POST /plans endpoint."""

    @patch("components.PlanLibrary.api.routes.get_plan_service")
    def test_store_plan_success(self, mock_get_service):
        """POST /plans with valid data -- 200 success."""
        mock_service = MagicMock()
        mock_service.store_plan = AsyncMock(
            return_value=StorePlanResponse(
                plan_id=VALID_ULID,
                stored_at=datetime.utcnow(),
                embedding_queued=True,
            )
        )
        mock_get_service.return_value = mock_service

        app = _create_test_app()
        client = TestClient(app)

        response = client.post("/plans", json=_make_store_request())

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["plan_id"] == VALID_ULID

    @patch("components.PlanLibrary.api.routes.get_plan_service")
    def test_store_plan_invalid_signature(self, mock_get_service):
        """POST /plans with invalid signature -- 400."""
        mock_service = MagicMock()
        mock_service.store_plan = AsyncMock(
            side_effect=InvalidSignatureError(
                plan_id=VALID_ULID, reason="bad sig"
            )
        )
        mock_get_service.return_value = mock_service

        app = _create_test_app()
        client = TestClient(app)

        response = client.post("/plans", json=_make_store_request())

        assert response.status_code == 400
        data = response.json()
        assert data["error_code"] == "INVALID_SIGNATURE"

    @patch("components.PlanLibrary.api.routes.get_plan_service")
    def test_store_plan_duplicate(self, mock_get_service):
        """POST /plans with duplicate plan_id -- 409."""
        mock_service = MagicMock()
        mock_service.store_plan = AsyncMock(
            side_effect=DuplicatePlanError(plan_id=VALID_ULID)
        )
        mock_get_service.return_value = mock_service

        app = _create_test_app()
        client = TestClient(app)

        response = client.post("/plans", json=_make_store_request())

        assert response.status_code == 409
        data = response.json()
        assert data["error_code"] == "DUPLICATE_PLAN_ID"

    @patch("components.PlanLibrary.api.routes.get_plan_service")
    def test_store_plan_too_large(self, mock_get_service):
        """POST /plans with oversized plan -- 413."""
        mock_service = MagicMock()
        mock_service.store_plan = AsyncMock(
            side_effect=PlanTooLargeError(
                plan_id=VALID_ULID, reason="exceeds 100 steps"
            )
        )
        mock_get_service.return_value = mock_service

        app = _create_test_app()
        client = TestClient(app)

        response = client.post("/plans", json=_make_store_request())

        assert response.status_code == 413
        data = response.json()
        assert data["error_code"] == "PLAN_TOO_LARGE"


class TestGetPlansByIntentEndpoint:
    """Tests for GET /plans/by-intent/{intent_type} endpoint."""

    @patch("components.PlanLibrary.api.routes.get_plan_service")
    def test_get_plans_by_intent(self, mock_get_service):
        """GET /plans/by-intent/{intent_type} returns Evidence Items."""
        evidence = EvidenceItem(
            type="plan",
            key="test_pattern",
            value={"intent": "test"},
            confidence=0.85,
            source_ref=f"planlibrary:plans/{VALID_ULID}",
            ttl_days=None,
            tier=3,
        )
        mock_service = MagicMock()
        mock_service.get_plans_by_intent = AsyncMock(
            return_value=[evidence]
        )
        mock_get_service.return_value = mock_service

        app = _create_test_app()
        client = TestClient(app)

        response = client.get("/plans/by-intent/test")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert len(data["data"]) == 1


class TestGetPlanEndpoint:
    """Tests for GET /plans/{plan_id} endpoint."""

    @patch("components.PlanLibrary.api.routes.get_plan_service")
    def test_get_plan_found(self, mock_get_service):
        """GET /plans/{plan_id} returns plan data when found."""
        mock_service = MagicMock()
        mock_service.get_plan_by_id = AsyncMock(
            return_value=PlanDB(
                plan_id=VALID_ULID,
                canonical_json={},
                signature_data={},
                intent_type="test",
                step_count=3,
                plan_hash="a" * 64,
                size_bytes=100,
                created_at=datetime.utcnow(),
            )
        )
        mock_get_service.return_value = mock_service

        app = _create_test_app()
        client = TestClient(app)

        response = client.get(f"/plans/{VALID_ULID}")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

    @patch("components.PlanLibrary.api.routes.get_plan_service")
    def test_get_plan_not_found(self, mock_get_service):
        """GET /plans/{plan_id} returns 404 when not found."""
        mock_service = MagicMock()
        mock_service.get_plan_by_id = AsyncMock(return_value=None)
        mock_get_service.return_value = mock_service

        app = _create_test_app()
        client = TestClient(app)

        response = client.get(f"/plans/{VALID_ULID}")

        assert response.status_code == 404
        data = response.json()
        assert data["error_code"] == "PLAN_NOT_FOUND"


class TestHealthEndpoint:
    """Tests for GET /plans/health endpoint."""

    @patch("components.PlanLibrary.adapters.db.DatabaseAdapter")
    def test_health_check_healthy(self, mock_db_class):
        """GET /plans/health returns healthy status."""
        mock_db = MagicMock()
        mock_db.health_check = AsyncMock(return_value=True)
        mock_db_class.return_value = mock_db

        app = _create_test_app()
        client = TestClient(app)

        response = client.get("/plans/health")

        assert response.status_code == 200
        data = response.json()
        assert data["overall"] == "healthy"


class TestErrorResponseFormat:
    """Tests for error response format consistency."""

    @patch("components.PlanLibrary.api.routes.get_plan_service")
    def test_error_response_has_required_fields(self, mock_get_service):
        """All error responses match ErrorResponse schema."""
        mock_service = MagicMock()
        mock_service.store_plan = AsyncMock(
            side_effect=InvalidSignatureError(
                plan_id=VALID_ULID, reason="test"
            )
        )
        mock_get_service.return_value = mock_service

        app = _create_test_app()
        client = TestClient(app)

        response = client.post("/plans", json=_make_store_request())

        data = response.json()
        # ErrorResponse required fields
        assert "status" in data
        assert data["status"] == "error"
        assert "error_code" in data
        assert "message" in data


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
