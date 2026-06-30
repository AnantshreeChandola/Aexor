"""
WorkflowRegistry tests — frozen dataclasses, workflow definitions,
helper functions, decomposition, and composition.
"""

from __future__ import annotations

import pytest

from components.Planner.adapters.workflow_registry import (
    EntityDefinition,
    WorkflowDefinition,
    compose_workflows,
    decompose_intent,
    get_action_map,
    get_all_intents,
    get_entity_map,
    get_provider_map,
    get_workflow,
    has_workflow,
    merge_entity_requirements,
)

# ===========================
# T700: Workflow retrieval
# ===========================


class TestWorkflowRetrieval:
    """All 32 intents present and retrievable (26 provider-specific + 6 generic)."""

    EXPECTED_INTENTS = (
        # Provider-specific intents
        "send_email_gmail",
        "schedule_meeting_google_calendar",
        "create_event_google_calendar",
        "draft_email_gmail",
        "read_email_gmail",
        "list_email_gmail",
        "search_email_gmail",
        "list_meetings_google_calendar",
        "check_calendar_google_calendar",
        # Google Docs
        "create_document_google_docs",
        "edit_document_google_docs",
        # Google Drive
        "upload_file_google_drive",
        "download_file_google_drive",
        "search_files_google_drive",
        "list_files_google_drive",
        # Notion
        "create_page_notion",
        "create_task_notion",
        "search_notion",
        "list_tasks_notion",
        # GitHub
        "create_issue_github",
        "list_issues_github",
        "create_pr_github",
        "list_prs_github",
        # Slack
        "send_message_slack",
        "search_messages_slack",
        "list_channels_slack",
        # Generic intents (provider-agnostic, tool="" for API steps)
        "send_email",
        "list_email",
        "schedule_meeting",
        "list_meetings",
        "create_task",
        "search_files",
    )

    @pytest.mark.parametrize("intent", EXPECTED_INTENTS)
    def test_get_workflow_returns_definition(self, intent: str):
        wf = get_workflow(intent)
        assert wf is not None
        assert wf.intent == intent

    @pytest.mark.parametrize("intent", EXPECTED_INTENTS)
    def test_has_workflow_true(self, intent: str):
        assert has_workflow(intent) is True

    def test_has_workflow_false_for_unknown(self):
        assert has_workflow("analyze_stocks") is False

    def test_get_workflow_none_for_unknown(self):
        assert get_workflow("analyze_stocks") is None

    def test_get_all_intents_contains_all_32(self):
        all_intents = get_all_intents()
        for intent in self.EXPECTED_INTENTS:
            assert intent in all_intents
        assert len(all_intents) == 32


# ===========================
# T701: Workflow structure validation
# ===========================


class TestWorkflowStructure:
    """Verify DAG patterns match plan rules."""

    WRITE_INTENTS = (
        "send_email_gmail", "schedule_meeting_google_calendar", "create_event_google_calendar",
        "edit_document_google_docs", "create_page_notion", "create_task_notion",
        "create_issue_github", "create_pr_github", "send_message_slack",
    )
    READ_INTENTS = (
        "read_email_gmail", "list_email_gmail", "search_email_gmail",
        "list_meetings_google_calendar", "check_calendar_google_calendar",
        "download_file_google_drive", "search_files_google_drive", "list_files_google_drive",
        "search_notion", "list_tasks_notion",
        "list_issues_github", "list_prs_github",
        "search_messages_slack", "list_channels_slack",
    )
    LIGHT_WRITE_INTENTS = ("draft_email_gmail", "create_document_google_docs", "upload_file_google_drive")

    @pytest.mark.parametrize("intent", WRITE_INTENTS)
    def test_write_intents_have_reasoner_with_can_spawn(self, intent: str):
        wf = get_workflow(intent)
        assert wf is not None
        reasoner_steps = [s for s in wf.steps if s.role == "Reasoner"]
        assert len(reasoner_steps) >= 1
        for rs in reasoner_steps:
            assert rs.can_spawn is True
            assert rs.reasoning_config is not None
            assert rs.policy_ref is not None

    @pytest.mark.parametrize("intent", WRITE_INTENTS)
    def test_write_intents_have_booker_with_gate(self, intent: str):
        wf = get_workflow(intent)
        assert wf is not None
        booker_steps = [s for s in wf.steps if s.role == "Booker"]
        assert len(booker_steps) >= 1
        for bs in booker_steps:
            assert bs.gate_id is not None

    @pytest.mark.parametrize("intent", READ_INTENTS)
    def test_read_intents_have_fetcher_reasoner_pattern(self, intent: str):
        wf = get_workflow(intent)
        assert wf is not None
        assert len(wf.steps) == 2
        assert wf.steps[0].role == "Fetcher"
        assert wf.steps[1].role == "Reasoner"
        # No Booker in read-only intents
        assert all(s.role != "Booker" for s in wf.steps)

    @pytest.mark.parametrize("intent", READ_INTENTS)
    def test_read_intents_reasoner_has_config(self, intent: str):
        wf = get_workflow(intent)
        assert wf is not None
        reasoner = wf.steps[1]
        assert reasoner.policy_ref is not None
        assert reasoner.reasoning_config is not None

    def test_context_from_chains_valid(self):
        """No forward references in context_from."""
        for intent_name in get_all_intents():
            wf = get_workflow(intent_name)
            assert wf is not None
            step_nums = {s.step for s in wf.steps}
            for s in wf.steps:
                for ref in s.context_from:
                    assert ref < s.step, (
                        f"{intent_name}: step {s.step} has forward context_from ref {ref}"
                    )
                    assert ref in step_nums

    def test_after_chains_valid(self):
        """No forward references in after."""
        for intent_name in get_all_intents():
            wf = get_workflow(intent_name)
            assert wf is not None
            for s in wf.steps:
                for ref in s.after:
                    assert ref < s.step, (
                        f"{intent_name}: step {s.step} has forward after ref {ref}"
                    )

    @pytest.mark.parametrize("intent", LIGHT_WRITE_INTENTS)
    def test_light_write_is_single_booker(self, intent: str):
        """Light-write intents have single Booker step with gate_id."""
        wf = get_workflow(intent)
        assert wf is not None
        assert len(wf.steps) == 1
        assert wf.steps[0].role == "Booker"
        assert wf.steps[0].gate_id is not None

    def test_edit_document_is_full_write(self):
        """edit_document_google_docs has Fetcher→Reasoner→Resolver→Booker (4 steps)."""
        wf = get_workflow("edit_document_google_docs")
        assert wf is not None
        assert len(wf.steps) == 4
        roles = [s.role for s in wf.steps]
        assert roles == ["Fetcher", "Reasoner", "Resolver", "Booker"]


# ===========================
# T702: Frozen immutability
# ===========================


class TestFrozenDataclasses:
    def test_workflow_definition_is_frozen(self):
        wf = get_workflow("send_email_gmail")
        assert wf is not None
        with pytest.raises(AttributeError):
            wf.intent = "hacked"  # type: ignore[misc]

    def test_step_template_is_frozen(self):
        wf = get_workflow("send_email_gmail")
        assert wf is not None
        with pytest.raises(AttributeError):
            wf.steps[0].role = "hacked"  # type: ignore[misc]

    def test_entity_definition_is_frozen(self):
        wf = get_workflow("send_email_gmail")
        assert wf is not None
        with pytest.raises(AttributeError):
            wf.entities[0].name = "hacked"  # type: ignore[misc]


# ===========================
# T703: Helper functions
# ===========================


class TestHelperFunctions:
    def test_get_entity_map_produces_correct_format(self):
        entity_map = get_entity_map()
        for intent_name in get_all_intents():
            assert intent_name in entity_map
            entry = entity_map[intent_name]
            assert "tools" in entry
            assert "entities" in entry
            assert isinstance(entry["tools"], list)
            assert isinstance(entry["entities"], list)

    def test_get_entity_map_aliases_are_lists(self):
        entity_map = get_entity_map()
        for entry in entity_map.values():
            for e in entry["entities"]:
                assert isinstance(e["aliases"], list)

    def test_get_provider_map_returns_tuples(self):
        provider_map = get_provider_map()
        assert "send_email_gmail" in provider_map
        assert isinstance(provider_map["send_email_gmail"], tuple)
        assert "gmail" in provider_map["send_email_gmail"]

    def test_get_action_map_returns_tuples(self):
        action_map = get_action_map()
        assert "schedule_meeting_google_calendar" in action_map
        assert isinstance(action_map["schedule_meeting_google_calendar"], tuple)
        assert "CREATE_EVENT" in action_map["schedule_meeting_google_calendar"]


# ===========================
# T704: Decomposition
# ===========================


class TestDecomposition:
    def test_single_known_intent(self):
        result = decompose_intent("schedule_meeting")
        assert result is not None
        assert len(result) == 1
        assert result[0].intent == "schedule_meeting"

    def test_compound_intent(self):
        result = decompose_intent("schedule_meeting_and_send_email")
        assert result is not None
        assert len(result) == 2
        intents = [wf.intent for wf in result]
        assert "schedule_meeting" in intents
        assert "send_email" in intents

    def test_compound_intent_order(self):
        """Order matches position in intent string."""
        result = decompose_intent("schedule_meeting_and_send_email")
        assert result is not None
        assert result[0].intent == "schedule_meeting"
        assert result[1].intent == "send_email"

    def test_two_read_workflows(self):
        result = decompose_intent("read_email_gmail_and_check_calendar_google_calendar")
        assert result is not None
        assert len(result) == 2
        intents = [wf.intent for wf in result]
        assert "read_email_gmail" in intents
        assert "check_calendar_google_calendar" in intents

    def test_unknown_intent_returns_none(self):
        result = decompose_intent("analyze_stocks")
        assert result is None

    def test_exact_match_prioritized(self):
        result = decompose_intent("send_email")
        assert result is not None
        assert len(result) == 1
        assert result[0].intent == "send_email"


# ===========================
# T705: Composition
# ===========================


class TestComposition:
    def test_compose_two_workflows(self):
        schedule_wf = get_workflow("schedule_meeting_google_calendar")
        email_wf = get_workflow("send_email_gmail")
        assert schedule_wf is not None and email_wf is not None

        steps, _tools = compose_workflows([schedule_wf, email_wf])

        # Total steps = schedule (4) + email (3)
        assert len(steps) == 7

        # Steps are renumbered sequentially
        step_nums = [s.step for s in steps]
        assert step_nums == [1, 2, 3, 4, 5, 6, 7]

    def test_compose_inter_workflow_dependency(self):
        """First step of second workflow depends on last step of first."""
        schedule_wf = get_workflow("schedule_meeting_google_calendar")
        email_wf = get_workflow("send_email_gmail")
        assert schedule_wf is not None and email_wf is not None

        steps, _ = compose_workflows([schedule_wf, email_wf])

        # Step 5 (first of email workflow) should depend on step 4 (last of schedule)
        step_5 = steps[4]
        assert 4 in step_5.after

    def test_compose_internal_refs_updated(self):
        """Internal after/context_from references use new step numbers."""
        schedule_wf = get_workflow("schedule_meeting_google_calendar")
        email_wf = get_workflow("send_email_gmail")
        assert schedule_wf is not None and email_wf is not None

        steps, _ = compose_workflows([schedule_wf, email_wf])

        # Step 6 (Resolver in email, originally step 2) should reference step 5 (originally step 1)
        step_6 = steps[5]
        assert 5 in step_6.after or 5 in step_6.context_from

    def test_compose_tools_deduplicated(self):
        """Tool list has no duplicates."""
        schedule_wf = get_workflow("schedule_meeting_google_calendar")
        email_wf = get_workflow("send_email_gmail")
        assert schedule_wf is not None and email_wf is not None

        _, tools = compose_workflows([schedule_wf, email_wf])

        assert len(tools) == len(set(tools))

    def test_merge_entity_requirements_deduplicates(self):
        """Shared entity names are de-duplicated."""
        schedule_wf = get_workflow("schedule_meeting_google_calendar")
        email_wf = get_workflow("send_email_gmail")
        assert schedule_wf is not None and email_wf is not None

        merged = merge_entity_requirements([schedule_wf, email_wf])

        names = [e.name for e in merged]
        assert len(names) == len(set(names))

    def test_merge_entity_requirements_required_wins(self):
        """If one workflow has required=True and another has required=False, True wins."""
        wf1 = WorkflowDefinition(
            intent="test1",
            provider="test",
            steps=(),
            entities=(
                EntityDefinition(name="shared", description="test", required=False),
            ),
        )
        wf2 = WorkflowDefinition(
            intent="test2",
            provider="test",
            steps=(),
            entities=(
                EntityDefinition(name="shared", description="test", required=True),
            ),
        )
        merged = merge_entity_requirements([wf1, wf2])
        assert len(merged) == 1
        assert merged[0].required is True

    def test_compose_single_workflow(self):
        """Composing a single workflow just renumbers from 1."""
        wf = get_workflow("read_email_gmail")
        assert wf is not None

        steps, _tools = compose_workflows([wf])
        assert len(steps) == 2
        assert steps[0].step == 1
        assert steps[1].step == 2
