"""
Integration-style tests for POST /orchestrate/rerun

Validates the rerun endpoint returns correct responses for valid
and invalid requests using mocked service dependencies.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from components.PlanLibrary.domain.models import PlanNotFoundError
from shared.api.orchestrate_routes import router
from shared.schemas.intent import Intent
from shared.schemas.plan import Plan, PlanConstraints, PlanMeta, PlanStep

VALID_ULID = "01HX1234567890ABCDEFGHJKMN"
NEW_ULID = "01JX9876543210ZYXWVTSRQPNM"


def _make_mock_plan(plan_id=NEW_ULID):
    """Build a minimal Plan object for test responses."""
    return Plan(
        plan_id=plan_id,
        intent=Intent(
            intent="schedule_meeting",
            entities={"title": "Rerun meeting", "date": "2025-07-15"},
            constraints={},
            user_id="user-test",
        ),
        graph=[
            PlanStep(
                step=1,
                mode="interactive",
                role="Fetcher",
                uses="google.calendar",
                call="list_events",
                args={},
                status="pending",
            ),
        ],
        constraints=PlanConstraints(),
        meta=PlanMeta(
            created_at="2025-07-15T00:00:00+00:00",
            canonical_hash="b" * 64,
        ),
    )


def _make_test_app(plan_service_mock, preview_service_mock=None):
    """Create a FastAPI app with mocked services."""
    app = FastAPI()

    # Set up app state for dependencies
    app.state.plan_service = plan_service_mock
    app.state.preview_service = preview_service_mock
    app.state.planner_service = None
    app.state.approval_service = None
    app.state.execute_service = None

    # Add auth middleware that sets request.state
    @app.middleware("http")
    async def mock_auth(request, call_next):
        request.state.user_id = "user-test"
        request.state.context_tier = 2
        request.state.email = "test@example.com"
        return await call_next(request)

    app.include_router(router)
    return app


class TestOrchestrateRerun:

    def test_rerun_returns_plan_and_preview(self):
        """POST /orchestrate/rerun with valid source returns plan + preview."""
        mock_plan = _make_mock_plan()

        plan_service = MagicMock()
        plan_service.clone_plan_for_rerun = AsyncMock(return_value=mock_plan)

        preview_service = MagicMock()
        preview_result = MagicMock()
        preview_result.model_dump.return_value = {"step_results": []}
        preview_service.preview = AsyncMock(return_value=preview_result)

        app = _make_test_app(plan_service, preview_service)
        client = TestClient(app)

        response = client.post(
            "/orchestrate/rerun",
            json={
                "source_plan_id": VALID_ULID,
                "entities": {"title": "Rerun meeting", "date": "2025-07-15"},
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert "plan" in data
        assert "preview" in data
        assert "plan_id" in data
        assert data["plan_id"] == NEW_ULID

    def test_rerun_invalid_plan_id_returns_404(self):
        """POST /orchestrate/rerun with nonexistent source returns 404."""
        plan_service = MagicMock()
        plan_service.clone_plan_for_rerun = AsyncMock(
            side_effect=PlanNotFoundError(plan_id=VALID_ULID)
        )

        app = _make_test_app(plan_service)
        client = TestClient(app)

        response = client.post(
            "/orchestrate/rerun",
            json={
                "source_plan_id": VALID_ULID,
                "entities": {"title": "test"},
            },
        )

        assert response.status_code == 404
        data = response.json()
        assert data["error_code"] == "PLAN_NOT_FOUND"

    def test_rerun_missing_entities_returns_422(self):
        """POST /orchestrate/rerun without entities returns 422 validation error."""
        plan_service = MagicMock()

        app = _make_test_app(plan_service)
        client = TestClient(app)

        response = client.post(
            "/orchestrate/rerun",
            json={"source_plan_id": VALID_ULID},
        )

        assert response.status_code == 422

    def test_rerun_short_plan_id_returns_422(self):
        """POST /orchestrate/rerun with too-short plan_id returns 422."""
        plan_service = MagicMock()

        app = _make_test_app(plan_service)
        client = TestClient(app)

        response = client.post(
            "/orchestrate/rerun",
            json={"source_plan_id": "short", "entities": {"x": "y"}},
        )

        assert response.status_code == 422

    def test_rerun_without_preview_service(self):
        """POST /orchestrate/rerun works even when preview service is None."""
        mock_plan = _make_mock_plan()
        plan_service = MagicMock()
        plan_service.clone_plan_for_rerun = AsyncMock(return_value=mock_plan)

        app = _make_test_app(plan_service, preview_service_mock=None)
        client = TestClient(app)

        response = client.post(
            "/orchestrate/rerun",
            json={
                "source_plan_id": VALID_ULID,
                "entities": {"title": "test"},
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["preview"] is None
        assert data["plan_id"] == NEW_ULID


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
