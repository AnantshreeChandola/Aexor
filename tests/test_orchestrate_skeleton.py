"""
Integration-style tests for POST /orchestrate/skeleton

Validates the skeleton endpoint returns correct responses for valid
and invalid requests using mocked service dependencies.
"""

from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from components.Intake.domain.models import ParseResult
from shared.api.orchestrate_routes import router
from shared.schemas.skeleton import PlanSkeleton, SkeletonEntityField, SkeletonStep


def _make_test_app(intake_service_mock, planner_service_mock, preference_service_mock=None):
    """Create a FastAPI app with mocked services."""
    app = FastAPI()

    app.state.intake_service = intake_service_mock
    app.state.planner_service = planner_service_mock
    app.state.preference_service = preference_service_mock
    app.state.plan_service = None
    app.state.preview_service = None
    app.state.approval_service = None
    app.state.execute_service = None

    @app.middleware("http")
    async def mock_auth(request, call_next):
        request.state.user_id = "550e8400-e29b-41d4-a716-446655440000"
        request.state.context_tier = 2
        request.state.email = "test@example.com"
        return await call_next(request)

    app.include_router(router)
    return app


def _make_meeting_skeleton():
    """Build a schedule_meeting PlanSkeleton for tests."""
    return PlanSkeleton(
        intent="schedule_meeting",
        intent_source="registry",
        steps=[
            SkeletonStep(step=1, role="Fetcher", type="api", tool="GOOGLECALENDAR_FIND_EVENT",
                         call="GOOGLECALENDAR_FIND_EVENT", after=[], description="Gathering data via Googlecalendar"),
            SkeletonStep(step=2, role="Reasoner", type="llm_reasoning", tool="calendar_conflict_resolver",
                         call="calendar_conflict_resolver", after=[1], description="Analyzing and deciding"),
            SkeletonStep(step=3, role="Resolver", type="api", tool="system.confirm",
                         call="system.confirm", after=[2], gate_id="gate-confirm", description="Confirming with you"),
            SkeletonStep(step=4, role="Booker", type="api", tool="GOOGLECALENDAR_CREATE_EVENT",
                         call="GOOGLECALENDAR_CREATE_EVENT", after=[3], gate_id="gate-execute",
                         entity_refs=["attendee", "duration", "timezone", "title"], description="Executing the action via Googlecalendar"),
        ],
        entities=[
            SkeletonEntityField(name="attendee", description="Who should attend the meeting", required=True,
                                used_by_steps=[4]),
            SkeletonEntityField(name="date_time", description="When to schedule the meeting (date and time)", required=True),
            SkeletonEntityField(name="title", description="What the meeting is about", required=True, used_by_steps=[4]),
            SkeletonEntityField(name="duration", description="How long the meeting should be", required=False,
                                default_value=30, default_source="profile", used_by_steps=[4]),
            SkeletonEntityField(name="timezone", description="Timezone for the meeting", required=True,
                                used_by_steps=[4], unit="IANA timezone", example="Asia/Kolkata"),
        ],
        dag_levels=[[1], [2], [3], [4]],
    )


class TestOrchestrateSkeleton:

    def test_skeleton_known_intent_returns_registry_steps(self):
        """POST /orchestrate/skeleton with known intent returns registry-based skeleton."""
        parse_result = ParseResult(
            intent="schedule_meeting",
            entities={"attendee": "Alice", "date_time": "tomorrow 2pm"},
        )
        intake = MagicMock()
        intake.parse_once = AsyncMock(return_value=parse_result)

        skeleton = _make_meeting_skeleton()
        planner = MagicMock()
        planner.build_skeleton = AsyncMock(return_value=skeleton)

        app = _make_test_app(intake, planner)
        client = TestClient(app)

        resp = client.post("/orchestrate/skeleton", json={"message": "schedule a meeting tomorrow with Alice"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["skeleton"]["intent"] == "schedule_meeting"
        assert data["skeleton"]["intent_source"] == "registry"
        assert len(data["skeleton"]["steps"]) == 4
        assert len(data["skeleton"]["entities"]) == 5

    def test_skeleton_extracts_partial_entities(self):
        """POST /orchestrate/skeleton returns partial entities from parse."""
        parse_result = ParseResult(
            intent="schedule_meeting",
            entities={"attendee": "Alice", "date_time": "tomorrow"},
        )
        intake = MagicMock()
        intake.parse_once = AsyncMock(return_value=parse_result)

        skeleton = _make_meeting_skeleton()
        planner = MagicMock()
        planner.build_skeleton = AsyncMock(return_value=skeleton)

        app = _make_test_app(intake, planner)
        client = TestClient(app)

        resp = client.post("/orchestrate/skeleton", json={"message": "meet Alice tomorrow"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["partial_entities"]["attendee"] == "Alice"
        assert data["partial_entities"]["date_time"] == "tomorrow"

    def test_skeleton_includes_profile_defaults(self):
        """POST /orchestrate/skeleton passes preference_service to build_skeleton."""
        parse_result = ParseResult(
            intent="schedule_meeting",
            entities={"attendee": "Alice"},
        )
        intake = MagicMock()
        intake.parse_once = AsyncMock(return_value=parse_result)

        skeleton = _make_meeting_skeleton()
        planner = MagicMock()
        planner.build_skeleton = AsyncMock(return_value=skeleton)

        pref_service = MagicMock()

        app = _make_test_app(intake, planner, pref_service)
        client = TestClient(app)

        resp = client.post("/orchestrate/skeleton", json={"message": "schedule a meeting with Alice"})
        assert resp.status_code == 200
        # Verify build_skeleton was called with preference_service
        call_kwargs = planner.build_skeleton.call_args
        assert call_kwargs.kwargs.get("preference_service") is pref_service

    def test_skeleton_dag_levels_correct(self):
        """Sequential meeting workflow has dag_levels [[1],[2],[3],[4]]."""
        parse_result = ParseResult(
            intent="schedule_meeting",
            entities={"attendee": "Alice"},
        )
        intake = MagicMock()
        intake.parse_once = AsyncMock(return_value=parse_result)

        skeleton = _make_meeting_skeleton()
        planner = MagicMock()
        planner.build_skeleton = AsyncMock(return_value=skeleton)

        app = _make_test_app(intake, planner)
        client = TestClient(app)

        resp = client.post("/orchestrate/skeleton", json={"message": "schedule a meeting"})
        data = resp.json()
        assert data["skeleton"]["dag_levels"] == [[1], [2], [3], [4]]

    def test_skeleton_entity_refs_populated(self):
        """Booker step has entity_refs from args_template."""
        parse_result = ParseResult(
            intent="schedule_meeting",
            entities={},
        )
        intake = MagicMock()
        intake.parse_once = AsyncMock(return_value=parse_result)

        skeleton = _make_meeting_skeleton()
        planner = MagicMock()
        planner.build_skeleton = AsyncMock(return_value=skeleton)

        app = _make_test_app(intake, planner)
        client = TestClient(app)

        resp = client.post("/orchestrate/skeleton", json={"message": "schedule meeting"})
        data = resp.json()
        booker_step = next(s for s in data["skeleton"]["steps"] if s["role"] == "Booker")
        assert "attendee" in booker_step["entity_refs"]
        assert "title" in booker_step["entity_refs"]

    def test_skeleton_unknown_intent_returns_llm(self):
        """Unknown intent returns skeleton with intent_source='llm'."""
        parse_result = ParseResult(
            intent="do_something_custom",
            entities={"target": "value"},
        )
        intake = MagicMock()
        intake.parse_once = AsyncMock(return_value=parse_result)

        llm_skeleton = PlanSkeleton(
            intent="do_something_custom",
            intent_source="llm",
            steps=[
                SkeletonStep(step=1, role="Fetcher", type="api", tool="system.echo",
                             call="system.echo", after=[], description="Gathering relevant data"),
                SkeletonStep(step=2, role="Reasoner", type="llm_reasoning", tool="system.echo",
                             call="system.echo", after=[1], description="Analyzing options"),
                SkeletonStep(step=3, role="Resolver", type="api", tool="system.echo",
                             call="system.echo", after=[2], gate_id="gate-confirm", description="Confirming selection"),
                SkeletonStep(step=4, role="Booker", type="api", tool="system.echo",
                             call="system.echo", after=[3], description="Executing action"),
            ],
            entities=[
                SkeletonEntityField(name="target", description="Target value", required=True),
            ],
            dag_levels=[[1], [2], [3], [4]],
        )
        planner = MagicMock()
        planner.build_skeleton = AsyncMock(return_value=llm_skeleton)

        app = _make_test_app(intake, planner)
        client = TestClient(app)

        resp = client.post("/orchestrate/skeleton", json={"message": "do something custom"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["skeleton"]["intent_source"] == "llm"

    def test_skeleton_no_intent_returns_400(self):
        """Gibberish message with no detected intent returns 400."""
        parse_result = ParseResult(intent=None, entities={})
        intake = MagicMock()
        intake.parse_once = AsyncMock(return_value=parse_result)

        planner = MagicMock()

        app = _make_test_app(intake, planner)
        client = TestClient(app)

        resp = client.post("/orchestrate/skeleton", json={"message": "asdfghjkl"})
        assert resp.status_code == 400
        data = resp.json()
        assert data["error_code"] == "NO_INTENT_DETECTED"

    def test_skeleton_empty_message_no_intent_returns_400(self):
        """Empty message with no intent_type returns 400 MISSING_MESSAGE."""
        intake = MagicMock()
        planner = MagicMock()

        app = _make_test_app(intake, planner)
        client = TestClient(app)

        resp = client.post("/orchestrate/skeleton", json={"message": ""})
        assert resp.status_code == 400
        assert resp.json()["error_code"] == "MISSING_MESSAGE"

    def test_skeleton_returns_session_id(self):
        """Response includes a session_id."""
        parse_result = ParseResult(
            intent="send_email",
            entities={"recipient": "bob@test.com"},
        )
        intake = MagicMock()
        intake.parse_once = AsyncMock(return_value=parse_result)

        skeleton = PlanSkeleton(
            intent="send_email",
            intent_source="registry",
            steps=[],
            entities=[],
            dag_levels=[],
        )
        planner = MagicMock()
        planner.build_skeleton = AsyncMock(return_value=skeleton)

        app = _make_test_app(intake, planner)
        client = TestClient(app)

        resp = client.post("/orchestrate/skeleton", json={"message": "send email to bob"})
        assert resp.status_code == 200
        assert "session_id" in resp.json()
        assert resp.json()["session_id"].startswith("skel_")

    def test_skeleton_intent_type_skips_llm_parse(self):
        """POST /orchestrate/skeleton with intent_type skips LLM parse (rerun flow)."""
        intake = MagicMock()
        intake.parse_once = AsyncMock()  # should NOT be called

        skeleton = _make_meeting_skeleton()
        planner = MagicMock()
        planner.build_skeleton = AsyncMock(return_value=skeleton)

        app = _make_test_app(intake, planner)
        client = TestClient(app)

        resp = client.post("/orchestrate/skeleton", json={
            "intent_type": "schedule_meeting",
            "entities": {"attendee": "Alice", "date_time": "tomorrow 2pm"},
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["skeleton"]["intent"] == "schedule_meeting"
        assert data["skeleton"]["intent_source"] == "registry"
        assert len(data["skeleton"]["entities"]) == 5
        # Verify LLM parse was NOT called
        intake.parse_once.assert_not_called()
        # Verify entities were passed through
        assert data["partial_entities"]["attendee"] == "Alice"

    def test_skeleton_intent_type_includes_full_metadata(self):
        """Rerun flow returns full entity metadata (descriptions, units, defaults)."""
        intake = MagicMock()
        skeleton = _make_meeting_skeleton()
        planner = MagicMock()
        planner.build_skeleton = AsyncMock(return_value=skeleton)

        app = _make_test_app(intake, planner)
        client = TestClient(app)

        resp = client.post("/orchestrate/skeleton", json={
            "intent_type": "schedule_meeting",
            "entities": {"attendee": "Bob"},
        })
        assert resp.status_code == 200
        data = resp.json()
        entities = data["skeleton"]["entities"]
        # Entities should have full metadata, not just names
        tz_entity = next(e for e in entities if e["name"] == "timezone")
        assert tz_entity["unit"] == "IANA timezone"
        assert tz_entity["example"] == "Asia/Kolkata"
        duration_entity = next(e for e in entities if e["name"] == "duration")
        assert duration_entity["required"] is False
