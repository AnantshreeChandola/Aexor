"""
WorkflowRegistry — frozen workflow definitions for known intents.

Provides deterministic multi-step DAG templates that are indistinguishable
from LLM-generated plans at execution time. Same Reasoner steps, same HITL
gates, same template references.

For known intents, this eliminates LLM calls for both entity inference and
plan generation.

Reference: LLD §6.7
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EntityDefinition:
    """A single entity required by a workflow."""

    name: str
    description: str
    required: bool = True
    aliases: tuple[str, ...] = ()
    default_preference_key: str | None = None
    tool_param: str | None = None
    unit: str = ""
    example: str = ""


@dataclass(frozen=True)
class StepTemplate:
    """A single step in a workflow DAG template."""

    step: int
    role: str
    type: str = "api"
    tool: str = ""
    call: str = ""
    timeout_s: int = 30
    gate_id: str | None = None
    context_from: tuple[int, ...] = ()
    after: tuple[int, ...] = ()
    can_spawn: bool = False
    max_spawned_steps: int | None = None
    trust_level: str | None = None
    policy_ref: str | None = None
    reasoning_config: dict | None = None
    args_template: dict | None = None


@dataclass(frozen=True)
class WorkflowDefinition:
    """Complete workflow definition for a known intent."""

    intent: str
    provider: str
    steps: tuple[StepTemplate, ...]
    entities: tuple[EntityDefinition, ...]
    related_actions: tuple[str, ...] = ()
    related_providers: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Workflow definitions — 26 known intents
# ---------------------------------------------------------------------------

_WORKFLOWS: tuple[WorkflowDefinition, ...] = (
    # ── Write intents ─────────────────────────────────────────────────
    WorkflowDefinition(
        intent="send_email_gmail",
        provider="gmail",
        steps=(
            StepTemplate(
                step=1,
                role="Reasoner",
                type="llm_reasoning",
                tool="email_validator",
                call="email_validator",
                timeout_s=30,
                can_spawn=True,
                max_spawned_steps=3,
                trust_level="trusted",
                policy_ref="policy-email",
                reasoning_config={
                    "model": "claude-sonnet-4-5-20250929",
                    "temperature": 0.0,
                    "max_tokens": 1024,
                    "system_prompt_ref": "Validate email content and recipients before sending.",
                },
            ),
            StepTemplate(
                step=2,
                role="Resolver",
                type="api",
                tool="system.confirm",
                call="system.confirm",
                after=(1,),
                context_from=(1,),
                gate_id="gate-confirm",
            ),
            StepTemplate(
                step=3,
                role="Booker",
                type="api",
                tool="GMAIL_SEND_EMAIL",
                call="GMAIL_SEND_EMAIL",
                after=(2,),
                gate_id="gate-execute",
                timeout_s=60,
                args_template={
                    "to": "{{entities.recipient}}",
                    "subject": "{{entities.subject}}",
                    "body": "{{entities.body}}",
                },
            ),
        ),
        entities=(
            EntityDefinition(
                name="recipient",
                description="Who to send the email to",
                required=True,
                aliases=("to", "attendee", "attendee_email"),
                tool_param="to",
                unit="email address",
                example="alice@company.com",
            ),
            EntityDefinition(
                name="subject",
                description="Email subject line",
                required=True,
                aliases=("email_subject",),
                tool_param="subject",
                example="Quick sync on project timeline",
            ),
            EntityDefinition(
                name="body",
                description="Email body content",
                required=True,
                aliases=("content", "message", "email_body"),
                tool_param="body",
                example="Hi, just wanted to follow up on...",
            ),
        ),
        related_actions=("SEND_EMAIL", "CREATE_DRAFT"),
        related_providers=("gmail",),
    ),
    WorkflowDefinition(
        intent="schedule_meeting_google_calendar",
        provider="googlecalendar",
        steps=(
            StepTemplate(
                step=1,
                role="Fetcher",
                type="api",
                tool="GOOGLECALENDAR_FIND_EVENT",
                call="GOOGLECALENDAR_FIND_EVENT",
                timeout_s=30,
                args_template={"search_term": "{{entities.title}}"},
            ),
            StepTemplate(
                step=2,
                role="Reasoner",
                type="llm_reasoning",
                tool="calendar_conflict_resolver",
                call="calendar_conflict_resolver",
                after=(1,),
                context_from=(1,),
                can_spawn=True,
                max_spawned_steps=3,
                trust_level="trusted",
                policy_ref="policy-scheduling",
                reasoning_config={
                    "model": "claude-sonnet-4-5-20250929",
                    "temperature": 0.0,
                    "max_tokens": 1024,
                    "system_prompt_ref": "Analyze calendar events for conflicts and recommend optimal meeting time.",
                },
            ),
            StepTemplate(
                step=3,
                role="Resolver",
                type="api",
                tool="system.confirm",
                call="system.confirm",
                after=(2,),
                context_from=(2,),
                gate_id="gate-confirm",
            ),
            StepTemplate(
                step=4,
                role="Booker",
                type="api",
                tool="GOOGLECALENDAR_CREATE_EVENT",
                call="GOOGLECALENDAR_CREATE_EVENT",
                after=(3,),
                gate_id="gate-execute",
                timeout_s=60,
                args_template={
                    "start_datetime": "{{step_2.result.recommended_time}}",
                    "attendees": "{{entities.attendee}}",
                    "summary": "{{entities.title}}",
                    "duration": "{{entities.duration}}",
                    "timezone": "{{entities.timezone}}",
                },
            ),
        ),
        entities=(
            EntityDefinition(
                name="attendee",
                description="Who should attend the meeting",
                required=True,
                aliases=("attendees", "participant", "with"),
                tool_param="attendees",
                unit="name or email",
                example="alice@company.com",
            ),
            EntityDefinition(
                name="date_time",
                description="When to schedule the meeting",
                required=True,
                aliases=(
                    "start_time", "time", "when", "date",
                    "meeting_date", "meeting_time", "start",
                    "schedule_date", "start_date",
                ),
                tool_param="start_time",
                unit="date and time",
                example="2026-05-02T14:00",
            ),
            EntityDefinition(
                name="title",
                description="What the meeting is about",
                required=True,
                aliases=("subject", "summary", "description", "topic", "agenda", "meeting_title"),
                tool_param="summary",
                example="Weekly sync",
            ),
            EntityDefinition(
                name="duration",
                description="How long the meeting should be",
                required=False,
                aliases=("duration_minutes", "length", "meeting_duration", "minutes"),
                default_preference_key="meeting_duration_min",
                tool_param="duration",
                unit="minutes",
                example="30",
            ),
            EntityDefinition(
                name="timezone",
                description="Timezone for the meeting",
                required=True,
                aliases=("tz", "time_zone"),
                tool_param="timezone",
                default_preference_key="timezone",
                unit="IANA timezone",
                example="Asia/Kolkata",
            ),
        ),
        related_actions=("CREATE_EVENT", "FIND_EVENT", "FIND_FREE_SLOTS", "LIST_EVENTS"),
        related_providers=("googlecalendar", "gmail"),
    ),
    WorkflowDefinition(
        intent="create_event_google_calendar",
        provider="googlecalendar",
        steps=(
            StepTemplate(
                step=1,
                role="Fetcher",
                type="api",
                tool="GOOGLECALENDAR_FIND_EVENT",
                call="GOOGLECALENDAR_FIND_EVENT",
                timeout_s=30,
                args_template={"search_term": "{{entities.title}}"},
            ),
            StepTemplate(
                step=2,
                role="Reasoner",
                type="llm_reasoning",
                tool="calendar_conflict_resolver",
                call="calendar_conflict_resolver",
                after=(1,),
                context_from=(1,),
                can_spawn=True,
                max_spawned_steps=3,
                trust_level="trusted",
                policy_ref="policy-scheduling",
                reasoning_config={
                    "model": "claude-sonnet-4-5-20250929",
                    "temperature": 0.0,
                    "max_tokens": 1024,
                    "system_prompt_ref": "Analyze calendar events for conflicts and recommend optimal event time.",
                },
            ),
            StepTemplate(
                step=3,
                role="Resolver",
                type="api",
                tool="system.confirm",
                call="system.confirm",
                after=(2,),
                context_from=(2,),
                gate_id="gate-confirm",
            ),
            StepTemplate(
                step=4,
                role="Booker",
                type="api",
                tool="GOOGLECALENDAR_CREATE_EVENT",
                call="GOOGLECALENDAR_CREATE_EVENT",
                after=(3,),
                gate_id="gate-execute",
                timeout_s=60,
                args_template={
                    "start_datetime": "{{step_2.result.recommended_time}}",
                    "summary": "{{entities.title}}",
                    "duration": "{{entities.duration}}",
                    "timezone": "{{entities.timezone}}",
                },
            ),
        ),
        entities=(
            EntityDefinition(
                name="title",
                description="Event title",
                required=True,
                aliases=("name", "event_name", "summary"),
                tool_param="summary",
                example="Team offsite",
            ),
            EntityDefinition(
                name="date_time",
                description="When the event takes place",
                required=True,
                aliases=(
                    "start_time", "time", "when", "date",
                    "event_date", "start_date", "start",
                ),
                tool_param="start_time",
                unit="date and time",
                example="2026-05-02T15:00",
            ),
            EntityDefinition(
                name="duration",
                description="How long the event should be",
                required=False,
                aliases=("duration_minutes", "length"),
                tool_param="duration",
                unit="minutes",
                example="60",
            ),
            EntityDefinition(
                name="timezone",
                description="Timezone for the event",
                required=True,
                aliases=("tz", "time_zone"),
                tool_param="timezone",
                default_preference_key="timezone",
                unit="IANA timezone",
                example="Asia/Kolkata",
            ),
        ),
        related_actions=("CREATE_EVENT", "FIND_EVENT", "FIND_FREE_SLOTS"),
        related_providers=("googlecalendar",),
    ),
    WorkflowDefinition(
        intent="draft_email_gmail",
        provider="gmail",
        steps=(
            StepTemplate(
                step=1,
                role="Booker",
                type="api",
                tool="GMAIL_CREATE_DRAFT",
                call="GMAIL_CREATE_DRAFT",
                gate_id="gate-execute",
                timeout_s=60,
                args_template={
                    "to": "{{entities.recipient}}",
                    "subject": "{{entities.subject}}",
                    "body": "{{entities.body}}",
                },
            ),
        ),
        entities=(
            EntityDefinition(
                name="recipient",
                description="Who the draft is addressed to",
                required=False,
                aliases=("to", "attendee_email"),
                tool_param="to",
                unit="email address",
                example="bob@example.com",
            ),
            EntityDefinition(
                name="subject",
                description="Draft email subject line",
                required=True,
                aliases=("email_subject",),
                tool_param="subject",
                example="Follow up on proposal",
            ),
            EntityDefinition(
                name="body",
                description="Draft email body content",
                required=True,
                aliases=("content", "message", "email_body"),
                tool_param="body",
                example="Hi, I wanted to follow up on...",
            ),
        ),
        related_actions=("CREATE_DRAFT", "SEND_EMAIL"),
        related_providers=("gmail",),
    ),
    # ── Read-only intents ─────────────────────────────────────────────
    WorkflowDefinition(
        intent="read_email_gmail",
        provider="gmail",
        steps=(
            StepTemplate(
                step=1,
                role="Fetcher",
                type="api",
                tool="GMAIL_FETCH_EMAILS",
                call="GMAIL_FETCH_EMAILS",
                timeout_s=30,
            ),
            StepTemplate(
                step=2,
                role="Reasoner",
                type="llm_reasoning",
                tool="email_summarizer",
                call="email_summarizer",
                after=(1,),
                context_from=(1,),
                policy_ref="policy-readonly",
                reasoning_config={
                    "model": "claude-sonnet-4-5-20250929",
                    "temperature": 0.0,
                    "max_tokens": 1024,
                    "system_prompt_ref": "Summarize the fetched emails for the user.",
                },
            ),
        ),
        entities=(
            EntityDefinition(
                name="query",
                description="Email search query or filter",
                required=False,
                aliases=("search", "filter"),
                tool_param="query",
                example="from:alice subject:invoice",
            ),
            EntityDefinition(
                name="limit",
                description="Maximum number of emails to fetch",
                required=False,
                aliases=("max_results", "count"),
                tool_param="max_results",
                unit="count",
                example="10",
            ),
        ),
        related_actions=("GET_EMAIL", "LIST_EMAILS", "FETCH_EMAILS"),
        related_providers=("gmail",),
    ),
    WorkflowDefinition(
        intent="list_email_gmail",
        provider="gmail",
        steps=(
            StepTemplate(
                step=1,
                role="Fetcher",
                type="api",
                tool="GMAIL_FETCH_EMAILS",
                call="GMAIL_FETCH_EMAILS",
                timeout_s=30,
            ),
            StepTemplate(
                step=2,
                role="Reasoner",
                type="llm_reasoning",
                tool="email_summarizer",
                call="email_summarizer",
                after=(1,),
                context_from=(1,),
                policy_ref="policy-readonly",
                reasoning_config={
                    "model": "claude-sonnet-4-5-20250929",
                    "temperature": 0.0,
                    "max_tokens": 1024,
                    "system_prompt_ref": "Summarize the email list for the user.",
                },
            ),
        ),
        entities=(
            EntityDefinition(
                name="query",
                description="Email search query or filter",
                required=False,
                aliases=("search", "filter"),
                tool_param="query",
                example="is:unread",
            ),
            EntityDefinition(
                name="limit",
                description="Maximum number of emails to list",
                required=False,
                aliases=("max_results", "count"),
                tool_param="maxResults",
                unit="count",
                example="10",
            ),
        ),
        related_actions=("LIST_EMAILS", "FETCH_EMAILS"),
        related_providers=("gmail",),
    ),
    WorkflowDefinition(
        intent="search_email_gmail",
        provider="gmail",
        steps=(
            StepTemplate(
                step=1,
                role="Fetcher",
                type="api",
                tool="GMAIL_FETCH_EMAILS",
                call="GMAIL_FETCH_EMAILS",
                timeout_s=30,
            ),
            StepTemplate(
                step=2,
                role="Reasoner",
                type="llm_reasoning",
                tool="email_summarizer",
                call="email_summarizer",
                after=(1,),
                context_from=(1,),
                policy_ref="policy-readonly",
                reasoning_config={
                    "model": "claude-sonnet-4-5-20250929",
                    "temperature": 0.0,
                    "max_tokens": 1024,
                    "system_prompt_ref": "Summarize the email search results for the user.",
                },
            ),
        ),
        entities=(
            EntityDefinition(
                name="query",
                description="Email search query",
                required=True,
                aliases=("search", "filter", "search_query"),
                tool_param="query",
                example="budget report Q4",
            ),
            EntityDefinition(
                name="limit",
                description="Maximum number of emails to return",
                required=False,
                aliases=("max_results", "count"),
                tool_param="maxResults",
                unit="count",
                example="10",
            ),
        ),
        related_actions=("LIST_EMAILS", "FETCH_EMAILS"),
        related_providers=("gmail",),
    ),
    WorkflowDefinition(
        intent="list_meetings_google_calendar",
        provider="googlecalendar",
        steps=(
            StepTemplate(
                step=1,
                role="Fetcher",
                type="api",
                tool="GOOGLECALENDAR_LIST_EVENTS",
                call="GOOGLECALENDAR_LIST_EVENTS",
                timeout_s=30,
            ),
            StepTemplate(
                step=2,
                role="Reasoner",
                type="llm_reasoning",
                tool="calendar_summarizer",
                call="calendar_summarizer",
                after=(1,),
                context_from=(1,),
                policy_ref="policy-readonly",
                reasoning_config={
                    "model": "claude-sonnet-4-5-20250929",
                    "temperature": 0.0,
                    "max_tokens": 1024,
                    "system_prompt_ref": "Summarize the calendar events for the user.",
                },
            ),
        ),
        entities=(
            EntityDefinition(
                name="date_range",
                description="Time range to list meetings for",
                required=False,
                aliases=("date", "when", "period", "timeframe"),
                tool_param="timeMin",
                example="this week",
            ),
        ),
        related_actions=("FIND_EVENT", "LIST_EVENTS", "LIST_ALL_CALENDARS"),
        related_providers=("googlecalendar",),
    ),
    WorkflowDefinition(
        intent="check_calendar_google_calendar",
        provider="googlecalendar",
        steps=(
            StepTemplate(
                step=1,
                role="Fetcher",
                type="api",
                tool="GOOGLECALENDAR_LIST_EVENTS",
                call="GOOGLECALENDAR_LIST_EVENTS",
                timeout_s=30,
            ),
            StepTemplate(
                step=2,
                role="Reasoner",
                type="llm_reasoning",
                tool="calendar_summarizer",
                call="calendar_summarizer",
                after=(1,),
                context_from=(1,),
                policy_ref="policy-readonly",
                reasoning_config={
                    "model": "claude-sonnet-4-5-20250929",
                    "temperature": 0.0,
                    "max_tokens": 1024,
                    "system_prompt_ref": "Summarize the calendar availability for the user.",
                },
            ),
        ),
        entities=(
            EntityDefinition(
                name="date_range",
                description="Time range to check availability for",
                required=False,
                aliases=("date", "when", "period", "timeframe"),
                tool_param="timeMin",
                example="next Monday",
            ),
        ),
        related_actions=("FIND_EVENT", "LIST_EVENTS", "LIST_ALL_CALENDARS"),
        related_providers=("googlecalendar",),
    ),
    # ── Google Docs intents ───────────────────────────────────────────
    WorkflowDefinition(
        intent="create_document_google_docs",
        provider="googledocs",
        steps=(
            StepTemplate(
                step=1,
                role="Booker",
                type="api",
                tool="GOOGLEDOCS_CREATE_DOCUMENT_FROM_TEXT",
                call="GOOGLEDOCS_CREATE_DOCUMENT_FROM_TEXT",
                gate_id="gate-execute",
                timeout_s=60,
                args_template={
                    "title": "{{entities.title}}",
                    "content": "{{entities.content}}",
                },
            ),
        ),
        entities=(
            EntityDefinition(
                name="title",
                description="Document title",
                required=True,
                tool_param="title",
                example="Project Proposal",
            ),
            EntityDefinition(
                name="content",
                description="Document content",
                required=True,
                aliases=("body", "text"),
                tool_param="content",
                example="Introduction to the project...",
            ),
        ),
        related_actions=("CREATE_DOCUMENT", "CREATE_DOCUMENT_FROM_TEXT"),
        related_providers=("googledocs",),
    ),
    WorkflowDefinition(
        intent="edit_document_google_docs",
        provider="googledocs",
        steps=(
            StepTemplate(
                step=1,
                role="Fetcher",
                type="api",
                tool="GOOGLEDOCS_GET_DOCUMENT",
                call="GOOGLEDOCS_GET_DOCUMENT",
                timeout_s=30,
                args_template={"document_id": "{{entities.document_id}}"},
            ),
            StepTemplate(
                step=2,
                role="Reasoner",
                type="llm_reasoning",
                tool="document_editor",
                call="document_editor",
                after=(1,),
                context_from=(1,),
                can_spawn=True,
                max_spawned_steps=3,
                trust_level="trusted",
                policy_ref="policy-docs",
                reasoning_config={
                    "model": "claude-sonnet-4-5-20250929",
                    "temperature": 0.0,
                    "max_tokens": 1024,
                    "system_prompt_ref": "Analyze document and determine edits to apply.",
                },
            ),
            StepTemplate(
                step=3,
                role="Resolver",
                type="api",
                tool="system.confirm",
                call="system.confirm",
                after=(2,),
                context_from=(2,),
                gate_id="gate-confirm",
            ),
            StepTemplate(
                step=4,
                role="Booker",
                type="api",
                tool="GOOGLEDOCS_APPEND_TEXT",
                call="GOOGLEDOCS_APPEND_TEXT",
                after=(3,),
                gate_id="gate-execute",
                timeout_s=60,
                args_template={
                    "document_id": "{{entities.document_id}}",
                    "text": "{{entities.content}}",
                },
            ),
        ),
        entities=(
            EntityDefinition(
                name="document_id",
                description="ID of the document to edit",
                required=True,
                aliases=("doc_id", "file_id"),
                tool_param="document_id",
                unit="document ID",
                example="1BxiMVs0XRA5nFMdKvBdBZjgmUii3vhG7",
            ),
            EntityDefinition(
                name="content",
                description="Content to add or replace",
                required=True,
                aliases=("text", "body"),
                tool_param="text",
                example="Updated section content...",
            ),
            EntityDefinition(
                name="action",
                description="Type of edit (append/replace)",
                required=False,
                aliases=("edit_type",),
                example="append",
            ),
        ),
        related_actions=("APPEND_TEXT", "GET_DOCUMENT", "UPDATE_DOCUMENT"),
        related_providers=("googledocs",),
    ),
    # ── Google Drive intents ──────────────────────────────────────────
    WorkflowDefinition(
        intent="upload_file_google_drive",
        provider="googledrive",
        steps=(
            StepTemplate(
                step=1,
                role="Booker",
                type="api",
                tool="GOOGLEDRIVE_UPLOAD_FILE",
                call="GOOGLEDRIVE_UPLOAD_FILE",
                gate_id="gate-execute",
                timeout_s=60,
                args_template={
                    "file_path": "{{entities.file_path}}",
                    "folder": "{{entities.folder}}",
                },
            ),
        ),
        entities=(
            EntityDefinition(
                name="file_path",
                description="Path to the file to upload",
                required=True,
                aliases=("path", "file"),
                tool_param="file_path",
                unit="file path",
                example="/documents/report.pdf",
            ),
            EntityDefinition(
                name="folder",
                description="Destination folder",
                required=False,
                aliases=("destination", "folder_id"),
                tool_param="folder",
                unit="folder name or ID",
                example="Shared/Reports",
            ),
        ),
        related_actions=("UPLOAD_FILE",),
        related_providers=("googledrive",),
    ),
    WorkflowDefinition(
        intent="download_file_google_drive",
        provider="googledrive",
        steps=(
            StepTemplate(
                step=1,
                role="Fetcher",
                type="api",
                tool="GOOGLEDRIVE_FIND_FILE",
                call="GOOGLEDRIVE_FIND_FILE",
                timeout_s=30,
            ),
            StepTemplate(
                step=2,
                role="Reasoner",
                type="llm_reasoning",
                tool="file_summarizer",
                call="file_summarizer",
                after=(1,),
                context_from=(1,),
                policy_ref="policy-readonly",
                reasoning_config={
                    "model": "claude-sonnet-4-5-20250929",
                    "temperature": 0.0,
                    "max_tokens": 1024,
                    "system_prompt_ref": "Confirm the correct file was found for download.",
                },
            ),
        ),
        entities=(
            EntityDefinition(
                name="file_name",
                description="Name of the file to download",
                required=True,
                aliases=("filename", "name"),
                tool_param="file_name",
                example="quarterly-report.pdf",
            ),
            EntityDefinition(
                name="query",
                description="Search query for finding the file",
                required=False,
                tool_param="query",
                example="quarterly report 2024",
            ),
        ),
        related_actions=("FIND_FILE", "DOWNLOAD_FILE"),
        related_providers=("googledrive",),
    ),
    WorkflowDefinition(
        intent="search_files_google_drive",
        provider="googledrive",
        steps=(
            StepTemplate(
                step=1,
                role="Fetcher",
                type="api",
                tool="GOOGLEDRIVE_SEARCH_FILE",
                call="GOOGLEDRIVE_SEARCH_FILE",
                timeout_s=30,
            ),
            StepTemplate(
                step=2,
                role="Reasoner",
                type="llm_reasoning",
                tool="file_summarizer",
                call="file_summarizer",
                after=(1,),
                context_from=(1,),
                policy_ref="policy-readonly",
                reasoning_config={
                    "model": "claude-sonnet-4-5-20250929",
                    "temperature": 0.0,
                    "max_tokens": 1024,
                    "system_prompt_ref": "Summarize the file search results for the user.",
                },
            ),
        ),
        entities=(
            EntityDefinition(
                name="query",
                description="Search query for files",
                required=True,
                aliases=("search", "keyword"),
                tool_param="query",
                example="meeting notes",
            ),
        ),
        related_actions=("SEARCH_FILE", "FIND_FILE", "LIST_FILES"),
        related_providers=("googledrive",),
    ),
    WorkflowDefinition(
        intent="list_files_google_drive",
        provider="googledrive",
        steps=(
            StepTemplate(
                step=1,
                role="Fetcher",
                type="api",
                tool="GOOGLEDRIVE_LIST_FILES",
                call="GOOGLEDRIVE_LIST_FILES",
                timeout_s=30,
            ),
            StepTemplate(
                step=2,
                role="Reasoner",
                type="llm_reasoning",
                tool="file_summarizer",
                call="file_summarizer",
                after=(1,),
                context_from=(1,),
                policy_ref="policy-readonly",
                reasoning_config={
                    "model": "claude-sonnet-4-5-20250929",
                    "temperature": 0.0,
                    "max_tokens": 1024,
                    "system_prompt_ref": "Summarize the file listing for the user.",
                },
            ),
        ),
        entities=(
            EntityDefinition(
                name="folder",
                description="Folder to list files from",
                required=False,
                aliases=("directory", "folder_id"),
                tool_param="folder",
                unit="folder name or ID",
                example="Shared/Projects",
            ),
            EntityDefinition(
                name="limit",
                description="Maximum number of files to list",
                required=False,
                tool_param="max_results",
                unit="count",
                example="20",
            ),
        ),
        related_actions=("LIST_FILES", "SEARCH_FILE"),
        related_providers=("googledrive",),
    ),
    # ── Notion intents ────────────────────────────────────────────────
    WorkflowDefinition(
        intent="create_page_notion",
        provider="notion",
        steps=(
            StepTemplate(
                step=1,
                role="Fetcher",
                type="api",
                tool="NOTION_SEARCH_NOTION",
                call="NOTION_SEARCH_NOTION",
                timeout_s=30,
                args_template={"query": "{{entities.title}}"},
            ),
            StepTemplate(
                step=2,
                role="Reasoner",
                type="llm_reasoning",
                tool="notion_validator",
                call="notion_validator",
                after=(1,),
                context_from=(1,),
                can_spawn=True,
                max_spawned_steps=3,
                trust_level="trusted",
                policy_ref="policy-notion",
                reasoning_config={
                    "model": "claude-sonnet-4-5-20250929",
                    "temperature": 0.0,
                    "max_tokens": 1024,
                    "system_prompt_ref": "Validate Notion page creation parameters.",
                },
            ),
            StepTemplate(
                step=3,
                role="Resolver",
                type="api",
                tool="system.confirm",
                call="system.confirm",
                after=(2,),
                context_from=(2,),
                gate_id="gate-confirm",
            ),
            StepTemplate(
                step=4,
                role="Booker",
                type="api",
                tool="NOTION_CREATE_A_NEW_PAGE",
                call="NOTION_CREATE_A_NEW_PAGE",
                after=(3,),
                gate_id="gate-execute",
                timeout_s=60,
                args_template={
                    "title": "{{entities.title}}",
                    "content": "{{entities.content}}",
                    "parent": "{{entities.parent}}",
                },
            ),
        ),
        entities=(
            EntityDefinition(
                name="title",
                description="Page title",
                required=True,
                tool_param="title",
                example="Sprint Planning Notes",
            ),
            EntityDefinition(
                name="content",
                description="Page content",
                required=False,
                aliases=("body", "text"),
                tool_param="content",
                example="Key discussion points...",
            ),
            EntityDefinition(
                name="parent",
                description="Parent page or database ID",
                required=False,
                aliases=("database", "database_id"),
                tool_param="parent",
                unit="page or database ID",
                example="My Workspace",
            ),
        ),
        related_actions=("CREATE_PAGE", "CREATE_A_NEW_PAGE"),
        related_providers=("notion",),
    ),
    WorkflowDefinition(
        intent="create_task_notion",
        provider="notion",
        steps=(
            StepTemplate(
                step=1,
                role="Fetcher",
                type="api",
                tool="NOTION_SEARCH_NOTION",
                call="NOTION_SEARCH_NOTION",
                timeout_s=30,
                args_template={"query": "{{entities.title}}"},
            ),
            StepTemplate(
                step=2,
                role="Reasoner",
                type="llm_reasoning",
                tool="notion_validator",
                call="notion_validator",
                after=(1,),
                context_from=(1,),
                can_spawn=True,
                max_spawned_steps=3,
                trust_level="trusted",
                policy_ref="policy-notion",
                reasoning_config={
                    "model": "claude-sonnet-4-5-20250929",
                    "temperature": 0.0,
                    "max_tokens": 1024,
                    "system_prompt_ref": "Validate Notion task creation parameters.",
                },
            ),
            StepTemplate(
                step=3,
                role="Resolver",
                type="api",
                tool="system.confirm",
                call="system.confirm",
                after=(2,),
                context_from=(2,),
                gate_id="gate-confirm",
            ),
            StepTemplate(
                step=4,
                role="Booker",
                type="api",
                tool="NOTION_CREATE_A_NEW_PAGE",
                call="NOTION_CREATE_A_NEW_PAGE",
                after=(3,),
                gate_id="gate-execute",
                timeout_s=60,
                args_template={
                    "title": "{{entities.title}}",
                    "status": "{{entities.status}}",
                    "due_date": "{{entities.due_date}}",
                    "assignee": "{{entities.assignee}}",
                    "parent": "{{entities.parent}}",
                },
            ),
        ),
        entities=(
            EntityDefinition(
                name="title",
                description="Task name",
                required=True,
                aliases=("task_name", "name"),
                tool_param="title",
                example="Review PR #42",
            ),
            EntityDefinition(
                name="status",
                description="Task status",
                required=False,
                tool_param="status",
                example="To Do",
            ),
            EntityDefinition(
                name="due_date",
                description="Task deadline",
                required=False,
                aliases=("deadline",),
                tool_param="due_date",
                example="next Friday",
            ),
            EntityDefinition(
                name="assignee",
                description="Person assigned to the task",
                required=False,
                tool_param="assignee",
                unit="name or email",
                example="Alice",
            ),
            EntityDefinition(
                name="parent",
                description="Parent database or project",
                required=False,
                aliases=("database", "database_id", "project"),
                tool_param="parent",
                unit="database name or ID",
                example="Tasks Board",
            ),
        ),
        related_actions=("CREATE_PAGE", "CREATE_A_NEW_PAGE", "CREATE_BLOCK"),
        related_providers=("notion",),
    ),
    WorkflowDefinition(
        intent="search_notion",
        provider="notion",
        steps=(
            StepTemplate(
                step=1,
                role="Fetcher",
                type="api",
                tool="NOTION_SEARCH_NOTION",
                call="NOTION_SEARCH_NOTION",
                timeout_s=30,
            ),
            StepTemplate(
                step=2,
                role="Reasoner",
                type="llm_reasoning",
                tool="notion_summarizer",
                call="notion_summarizer",
                after=(1,),
                context_from=(1,),
                policy_ref="policy-readonly",
                reasoning_config={
                    "model": "claude-sonnet-4-5-20250929",
                    "temperature": 0.0,
                    "max_tokens": 1024,
                    "system_prompt_ref": "Summarize Notion search results for the user.",
                },
            ),
        ),
        entities=(
            EntityDefinition(
                name="query",
                description="Search query",
                required=True,
                aliases=("search", "keyword"),
                tool_param="query",
                example="sprint planning",
            ),
        ),
        related_actions=("SEARCH_NOTION", "FETCH_PAGE"),
        related_providers=("notion",),
    ),
    WorkflowDefinition(
        intent="list_tasks_notion",
        provider="notion",
        steps=(
            StepTemplate(
                step=1,
                role="Fetcher",
                type="api",
                tool="NOTION_FETCH_DATABASE",
                call="NOTION_FETCH_DATABASE",
                timeout_s=30,
            ),
            StepTemplate(
                step=2,
                role="Reasoner",
                type="llm_reasoning",
                tool="notion_summarizer",
                call="notion_summarizer",
                after=(1,),
                context_from=(1,),
                policy_ref="policy-readonly",
                reasoning_config={
                    "model": "claude-sonnet-4-5-20250929",
                    "temperature": 0.0,
                    "max_tokens": 1024,
                    "system_prompt_ref": "Summarize the task list from the Notion database.",
                },
            ),
        ),
        entities=(
            EntityDefinition(
                name="database_id",
                description="Notion database to list tasks from",
                required=True,
                aliases=("database", "project"),
                tool_param="database_id",
                unit="database name or ID",
                example="Tasks Board",
            ),
            EntityDefinition(
                name="status",
                description="Filter by task status",
                required=False,
                aliases=("filter",),
                tool_param="status",
                example="In Progress",
            ),
        ),
        related_actions=("FETCH_DATABASE", "LIST_PAGES", "FETCH_PAGE"),
        related_providers=("notion",),
    ),
    # ── GitHub intents ────────────────────────────────────────────────
    WorkflowDefinition(
        intent="create_issue_github",
        provider="github",
        steps=(
            StepTemplate(
                step=1,
                role="Reasoner",
                type="llm_reasoning",
                tool="github_validator",
                call="github_validator",
                can_spawn=True,
                max_spawned_steps=3,
                trust_level="trusted",
                policy_ref="policy-github",
                reasoning_config={
                    "model": "claude-sonnet-4-5-20250929",
                    "temperature": 0.0,
                    "max_tokens": 1024,
                    "system_prompt_ref": "Validate GitHub issue creation parameters.",
                },
            ),
            StepTemplate(
                step=2,
                role="Resolver",
                type="api",
                tool="system.confirm",
                call="system.confirm",
                after=(1,),
                context_from=(1,),
                gate_id="gate-confirm",
            ),
            StepTemplate(
                step=3,
                role="Booker",
                type="api",
                tool="GITHUB_ISSUES_CREATE",
                call="GITHUB_ISSUES_CREATE",
                after=(2,),
                gate_id="gate-execute",
                timeout_s=60,
                args_template={
                    "title": "{{entities.title}}",
                    "body": "{{entities.body}}",
                    "repo": "{{entities.repo}}",
                    "labels": "{{entities.labels}}",
                    "assignees": "{{entities.assignees}}",
                },
            ),
        ),
        entities=(
            EntityDefinition(
                name="title",
                description="Issue title",
                required=True,
                tool_param="title",
                example="Fix login button on mobile",
            ),
            EntityDefinition(
                name="body",
                description="Issue description",
                required=False,
                aliases=("description", "content"),
                tool_param="body",
                example="The login button is not visible on iOS Safari...",
            ),
            EntityDefinition(
                name="repo",
                description="Repository (owner/name)",
                required=True,
                aliases=("repository",),
                tool_param="repo",
                unit="owner/repo",
                example="acme/web-app",
            ),
            EntityDefinition(
                name="labels",
                description="Issue labels",
                required=False,
                tool_param="labels",
                example="bug, priority:high",
            ),
            EntityDefinition(
                name="assignees",
                description="Issue assignees",
                required=False,
                tool_param="assignees",
                unit="GitHub usernames",
                example="alice, bob",
            ),
        ),
        related_actions=("CREATE_ISSUE", "ISSUES_CREATE"),
        related_providers=("github",),
    ),
    WorkflowDefinition(
        intent="list_issues_github",
        provider="github",
        steps=(
            StepTemplate(
                step=1,
                role="Fetcher",
                type="api",
                tool="GITHUB_ISSUES_LIST",
                call="GITHUB_ISSUES_LIST",
                timeout_s=30,
            ),
            StepTemplate(
                step=2,
                role="Reasoner",
                type="llm_reasoning",
                tool="github_summarizer",
                call="github_summarizer",
                after=(1,),
                context_from=(1,),
                policy_ref="policy-readonly",
                reasoning_config={
                    "model": "claude-sonnet-4-5-20250929",
                    "temperature": 0.0,
                    "max_tokens": 1024,
                    "system_prompt_ref": "Summarize the GitHub issues for the user.",
                },
            ),
        ),
        entities=(
            EntityDefinition(
                name="repo",
                description="Repository (owner/name)",
                required=True,
                aliases=("repository",),
                tool_param="repo",
                unit="owner/repo",
                example="acme/web-app",
            ),
            EntityDefinition(
                name="state",
                description="Issue state filter (open/closed)",
                required=False,
                aliases=("status",),
                tool_param="state",
                example="open",
            ),
            EntityDefinition(
                name="labels",
                description="Filter by labels",
                required=False,
                tool_param="labels",
                example="bug",
            ),
        ),
        related_actions=("ISSUES_LIST", "LIST_ISSUES"),
        related_providers=("github",),
    ),
    WorkflowDefinition(
        intent="create_pr_github",
        provider="github",
        steps=(
            StepTemplate(
                step=1,
                role="Reasoner",
                type="llm_reasoning",
                tool="github_validator",
                call="github_validator",
                can_spawn=True,
                max_spawned_steps=3,
                trust_level="trusted",
                policy_ref="policy-github",
                reasoning_config={
                    "model": "claude-sonnet-4-5-20250929",
                    "temperature": 0.0,
                    "max_tokens": 1024,
                    "system_prompt_ref": "Validate GitHub pull request creation parameters.",
                },
            ),
            StepTemplate(
                step=2,
                role="Resolver",
                type="api",
                tool="system.confirm",
                call="system.confirm",
                after=(1,),
                context_from=(1,),
                gate_id="gate-confirm",
            ),
            StepTemplate(
                step=3,
                role="Booker",
                type="api",
                tool="GITHUB_PULLS_CREATE",
                call="GITHUB_PULLS_CREATE",
                after=(2,),
                gate_id="gate-execute",
                timeout_s=60,
                args_template={
                    "title": "{{entities.title}}",
                    "body": "{{entities.body}}",
                    "repo": "{{entities.repo}}",
                    "head": "{{entities.head}}",
                    "base": "{{entities.base}}",
                },
            ),
        ),
        entities=(
            EntityDefinition(
                name="title",
                description="Pull request title",
                required=True,
                tool_param="title",
                example="Add user authentication",
            ),
            EntityDefinition(
                name="body",
                description="Pull request description",
                required=False,
                aliases=("description",),
                tool_param="body",
                example="Implements OAuth2 login flow...",
            ),
            EntityDefinition(
                name="repo",
                description="Repository (owner/name)",
                required=True,
                aliases=("repository",),
                tool_param="repo",
                unit="owner/repo",
                example="acme/web-app",
            ),
            EntityDefinition(
                name="head",
                description="Source branch",
                required=True,
                aliases=("branch", "source_branch"),
                tool_param="head",
                unit="branch name",
                example="feat/auth",
            ),
            EntityDefinition(
                name="base",
                description="Target branch",
                required=False,
                aliases=("target_branch",),
                tool_param="base",
                unit="branch name",
                example="main",
            ),
        ),
        related_actions=("CREATE_PULL_REQUEST", "PULLS_CREATE"),
        related_providers=("github",),
    ),
    WorkflowDefinition(
        intent="list_prs_github",
        provider="github",
        steps=(
            StepTemplate(
                step=1,
                role="Fetcher",
                type="api",
                tool="GITHUB_PULLS_LIST",
                call="GITHUB_PULLS_LIST",
                timeout_s=30,
            ),
            StepTemplate(
                step=2,
                role="Reasoner",
                type="llm_reasoning",
                tool="github_summarizer",
                call="github_summarizer",
                after=(1,),
                context_from=(1,),
                policy_ref="policy-readonly",
                reasoning_config={
                    "model": "claude-sonnet-4-5-20250929",
                    "temperature": 0.0,
                    "max_tokens": 1024,
                    "system_prompt_ref": "Summarize the GitHub pull requests for the user.",
                },
            ),
        ),
        entities=(
            EntityDefinition(
                name="repo",
                description="Repository (owner/name)",
                required=True,
                aliases=("repository",),
                tool_param="repo",
                unit="owner/repo",
                example="acme/web-app",
            ),
            EntityDefinition(
                name="state",
                description="PR state filter (open/closed)",
                required=False,
                aliases=("status",),
                tool_param="state",
                example="open",
            ),
        ),
        related_actions=("PULLS_LIST", "LIST_PULL_REQUESTS"),
        related_providers=("github",),
    ),
    # ── Slack intents ─────────────────────────────────────────────────
    WorkflowDefinition(
        intent="send_message_slack",
        provider="slack",
        steps=(
            StepTemplate(
                step=1,
                role="Reasoner",
                type="llm_reasoning",
                tool="slack_validator",
                call="slack_validator",
                can_spawn=True,
                max_spawned_steps=3,
                trust_level="trusted",
                policy_ref="policy-slack",
                reasoning_config={
                    "model": "claude-sonnet-4-5-20250929",
                    "temperature": 0.0,
                    "max_tokens": 1024,
                    "system_prompt_ref": "Validate Slack message content and channel before sending.",
                },
            ),
            StepTemplate(
                step=2,
                role="Resolver",
                type="api",
                tool="system.confirm",
                call="system.confirm",
                after=(1,),
                context_from=(1,),
                gate_id="gate-confirm",
            ),
            StepTemplate(
                step=3,
                role="Booker",
                type="api",
                tool="SLACK_SENDS_A_MESSAGE_TO_A_SLACK_CHANNEL",
                call="SLACK_SENDS_A_MESSAGE_TO_A_SLACK_CHANNEL",
                after=(2,),
                gate_id="gate-execute",
                timeout_s=60,
                args_template={
                    "channel": "{{entities.channel}}",
                    "text": "{{entities.message}}",
                },
            ),
        ),
        entities=(
            EntityDefinition(
                name="channel",
                description="Slack channel to send message to",
                required=True,
                aliases=("to", "channel_name", "room"),
                tool_param="channel",
                unit="channel name",
                example="#general",
            ),
            EntityDefinition(
                name="message",
                description="Message content",
                required=True,
                aliases=("text", "content", "body"),
                tool_param="text",
                example="Hey team, standup in 5 min!",
            ),
        ),
        related_actions=("SENDS_A_MESSAGE", "CHAT_POST_MESSAGE"),
        related_providers=("slack",),
    ),
    WorkflowDefinition(
        intent="search_messages_slack",
        provider="slack",
        steps=(
            StepTemplate(
                step=1,
                role="Fetcher",
                type="api",
                tool="SLACK_SEARCH_FOR_MESSAGES_IN_SLACK",
                call="SLACK_SEARCH_FOR_MESSAGES_IN_SLACK",
                timeout_s=30,
            ),
            StepTemplate(
                step=2,
                role="Reasoner",
                type="llm_reasoning",
                tool="slack_summarizer",
                call="slack_summarizer",
                after=(1,),
                context_from=(1,),
                policy_ref="policy-readonly",
                reasoning_config={
                    "model": "claude-sonnet-4-5-20250929",
                    "temperature": 0.0,
                    "max_tokens": 1024,
                    "system_prompt_ref": "Summarize the Slack message search results for the user.",
                },
            ),
        ),
        entities=(
            EntityDefinition(
                name="query",
                description="Search query for messages",
                required=True,
                aliases=("search", "keyword"),
                tool_param="query",
                example="deployment update",
            ),
            EntityDefinition(
                name="channel",
                description="Limit search to a specific channel",
                required=False,
                aliases=("in_channel",),
                tool_param="channel",
                unit="channel name",
                example="#engineering",
            ),
        ),
        related_actions=("SEARCH_FOR_MESSAGES",),
        related_providers=("slack",),
    ),
    WorkflowDefinition(
        intent="list_channels_slack",
        provider="slack",
        steps=(
            StepTemplate(
                step=1,
                role="Fetcher",
                type="api",
                tool="SLACK_LIST_ALL_SLACK_TEAM_CHANNELS",
                call="SLACK_LIST_ALL_SLACK_TEAM_CHANNELS",
                timeout_s=30,
            ),
            StepTemplate(
                step=2,
                role="Reasoner",
                type="llm_reasoning",
                tool="slack_summarizer",
                call="slack_summarizer",
                after=(1,),
                context_from=(1,),
                policy_ref="policy-readonly",
                reasoning_config={
                    "model": "claude-sonnet-4-5-20250929",
                    "temperature": 0.0,
                    "max_tokens": 1024,
                    "system_prompt_ref": "Summarize the Slack channel list for the user.",
                },
            ),
        ),
        entities=(
            EntityDefinition(
                name="limit",
                description="Maximum number of channels to list",
                required=False,
                aliases=("count", "max_results"),
                tool_param="limit",
                unit="count",
                example="25",
            ),
        ),
        related_actions=("LIST_ALL_SLACK_TEAM_CHANNELS",),
        related_providers=("slack",),
    ),
)

# ---------------------------------------------------------------------------
# Generic workflows — provider-agnostic intents with tool="" for API steps.
# When the skeleton has empty-tool steps, the UI shows a tool picker dropdown.
# ---------------------------------------------------------------------------

_GENERIC_WORKFLOWS: tuple[WorkflowDefinition, ...] = (
    WorkflowDefinition(
        intent="send_email",
        provider="generic",
        steps=(
            StepTemplate(
                step=1, role="Reasoner", type="llm_reasoning",
                tool="email_validator", call="email_validator",
                can_spawn=True, max_spawned_steps=3,
                trust_level="trusted", policy_ref="policy-email",
                reasoning_config={
                    "model": "claude-sonnet-4-5-20250929",
                    "temperature": 0.0, "max_tokens": 1024,
                    "system_prompt_ref": "Validate email content and recipients before sending.",
                },
            ),
            StepTemplate(
                step=2, role="Resolver", type="api",
                tool="system.confirm", call="system.confirm",
                after=(1,), context_from=(1,), gate_id="gate-confirm",
            ),
            StepTemplate(
                step=3, role="Booker", type="api",
                tool="", call="",
                after=(2,), gate_id="gate-execute", timeout_s=60,
            ),
        ),
        entities=(
            EntityDefinition(
                name="recipient", description="Who to send the email to",
                required=True, aliases=("to", "attendee", "attendee_email"),
                unit="email address", example="alice@company.com",
            ),
            EntityDefinition(
                name="subject", description="Email subject line",
                required=True, aliases=("email_subject",),
                example="Quick sync on project timeline",
            ),
            EntityDefinition(
                name="body", description="Email body content",
                required=True, aliases=("content", "message", "email_body"),
                example="Hi, just wanted to follow up on...",
            ),
        ),
    ),
    WorkflowDefinition(
        intent="list_email",
        provider="generic",
        steps=(
            StepTemplate(
                step=1, role="Fetcher", type="api",
                tool="", call="", timeout_s=30,
            ),
            StepTemplate(
                step=2, role="Reasoner", type="llm_reasoning",
                tool="email_summarizer", call="email_summarizer",
                after=(1,), context_from=(1,), policy_ref="policy-readonly",
                reasoning_config={
                    "model": "claude-sonnet-4-5-20250929",
                    "temperature": 0.0, "max_tokens": 1024,
                    "system_prompt_ref": "Summarize the email list for the user.",
                },
            ),
        ),
        entities=(
            EntityDefinition(
                name="query", description="Email search query or filter",
                required=False, aliases=("search", "filter"),
                example="is:unread",
            ),
            EntityDefinition(
                name="limit", description="Maximum number of emails to list",
                required=False, aliases=("max_results", "count"),
                unit="count", example="10",
            ),
        ),
    ),
    WorkflowDefinition(
        intent="schedule_meeting",
        provider="generic",
        steps=(
            StepTemplate(
                step=1, role="Fetcher", type="api",
                tool="", call="", timeout_s=30,
            ),
            StepTemplate(
                step=2, role="Reasoner", type="llm_reasoning",
                tool="calendar_conflict_resolver", call="calendar_conflict_resolver",
                after=(1,), context_from=(1,),
                can_spawn=True, max_spawned_steps=3,
                trust_level="trusted", policy_ref="policy-scheduling",
                reasoning_config={
                    "model": "claude-sonnet-4-5-20250929",
                    "temperature": 0.0, "max_tokens": 1024,
                    "system_prompt_ref": "Analyze calendar events for conflicts and recommend optimal meeting time.",
                },
            ),
            StepTemplate(
                step=3, role="Resolver", type="api",
                tool="system.confirm", call="system.confirm",
                after=(2,), context_from=(2,), gate_id="gate-confirm",
            ),
            StepTemplate(
                step=4, role="Booker", type="api",
                tool="", call="",
                after=(3,), gate_id="gate-execute", timeout_s=60,
            ),
        ),
        entities=(
            EntityDefinition(
                name="attendee", description="Who should attend the meeting",
                required=True, aliases=("attendees", "participant", "with"),
                unit="name or email", example="alice@company.com",
            ),
            EntityDefinition(
                name="date_time", description="When to schedule the meeting",
                required=True,
                aliases=("start_time", "time", "when", "date",
                         "meeting_date", "meeting_time", "start",
                         "schedule_date", "start_date"),
                unit="date and time", example="2026-05-02T14:00",
            ),
            EntityDefinition(
                name="title", description="What the meeting is about",
                required=True,
                aliases=("subject", "summary", "description", "topic", "agenda", "meeting_title"),
                example="Weekly sync",
            ),
            EntityDefinition(
                name="duration", description="How long the meeting should be",
                required=False,
                aliases=("duration_minutes", "length", "meeting_duration", "minutes"),
                default_preference_key="meeting_duration_min",
                unit="minutes", example="30",
            ),
            EntityDefinition(
                name="timezone", description="Timezone for the meeting",
                required=True, aliases=("tz", "time_zone"),
                default_preference_key="timezone",
                unit="IANA timezone", example="Asia/Kolkata",
            ),
        ),
    ),
    WorkflowDefinition(
        intent="list_meetings",
        provider="generic",
        steps=(
            StepTemplate(
                step=1, role="Fetcher", type="api",
                tool="", call="", timeout_s=30,
            ),
            StepTemplate(
                step=2, role="Reasoner", type="llm_reasoning",
                tool="meeting_summarizer", call="meeting_summarizer",
                after=(1,), context_from=(1,), policy_ref="policy-readonly",
                reasoning_config={
                    "model": "claude-sonnet-4-5-20250929",
                    "temperature": 0.0, "max_tokens": 1024,
                    "system_prompt_ref": "Summarize the calendar events for the user.",
                },
            ),
        ),
        entities=(
            EntityDefinition(
                name="date_range", description="Time range to list meetings for",
                required=False, aliases=("date", "when", "period", "timeframe"),
                example="this week",
            ),
        ),
    ),
    WorkflowDefinition(
        intent="create_task",
        provider="generic",
        steps=(
            StepTemplate(
                step=1, role="Fetcher", type="api",
                tool="", call="", timeout_s=30,
            ),
            StepTemplate(
                step=2, role="Reasoner", type="llm_reasoning",
                tool="task_validator", call="task_validator",
                after=(1,), context_from=(1,),
                can_spawn=True, max_spawned_steps=3,
                trust_level="trusted", policy_ref="policy-tasks",
                reasoning_config={
                    "model": "claude-sonnet-4-5-20250929",
                    "temperature": 0.0, "max_tokens": 1024,
                    "system_prompt_ref": "Validate task creation parameters.",
                },
            ),
            StepTemplate(
                step=3, role="Resolver", type="api",
                tool="system.confirm", call="system.confirm",
                after=(2,), context_from=(2,), gate_id="gate-confirm",
            ),
            StepTemplate(
                step=4, role="Booker", type="api",
                tool="", call="",
                after=(3,), gate_id="gate-execute", timeout_s=60,
            ),
        ),
        entities=(
            EntityDefinition(
                name="title", description="Task name",
                required=True, aliases=("task_name", "name"),
                example="Review PR #42",
            ),
            EntityDefinition(
                name="status", description="Task status",
                required=False, example="To Do",
            ),
            EntityDefinition(
                name="due_date", description="Task deadline",
                required=False, aliases=("deadline",),
                example="next Friday",
            ),
            EntityDefinition(
                name="assignee", description="Person assigned to the task",
                required=False, unit="name or email", example="Alice",
            ),
        ),
    ),
    WorkflowDefinition(
        intent="search_files",
        provider="generic",
        steps=(
            StepTemplate(
                step=1, role="Fetcher", type="api",
                tool="", call="", timeout_s=30,
            ),
            StepTemplate(
                step=2, role="Reasoner", type="llm_reasoning",
                tool="file_summarizer", call="file_summarizer",
                after=(1,), context_from=(1,), policy_ref="policy-readonly",
                reasoning_config={
                    "model": "claude-sonnet-4-5-20250929",
                    "temperature": 0.0, "max_tokens": 1024,
                    "system_prompt_ref": "Summarize the file search results for the user.",
                },
            ),
        ),
        entities=(
            EntityDefinition(
                name="query", description="Search query for files",
                required=True, aliases=("search", "keyword"),
                example="meeting notes",
            ),
        ),
    ),
)

# ---------------------------------------------------------------------------
# Indexed lookups (built once at import time)
# ---------------------------------------------------------------------------

_WORKFLOW_BY_INTENT: dict[str, WorkflowDefinition] = {
    wf.intent: wf for wf in (*_WORKFLOWS, *_GENERIC_WORKFLOWS)
}


# ---------------------------------------------------------------------------
# Public API — single-workflow helpers
# ---------------------------------------------------------------------------


def get_workflow(intent: str) -> WorkflowDefinition | None:
    """Look up a workflow definition by exact intent name."""
    return _WORKFLOW_BY_INTENT.get(intent)


def has_workflow(intent: str) -> bool:
    """Check if a deterministic workflow exists for this intent."""
    return intent in _WORKFLOW_BY_INTENT


def get_all_intents() -> list[str]:
    """Return all registered workflow intent names."""
    return list(_WORKFLOW_BY_INTENT.keys())


def get_entity_map() -> dict[str, dict]:
    """Build a static entity map compatible with PlannerService's format.

    Returns ``{intent: {"tools": [...], "entities": [...]}}`` where each
    entity dict has ``name``, ``description``, ``required``,
    ``default_preference_key``, and ``aliases``.

    Includes both provider-specific and generic workflows so the Intake
    system prompt shows all valid intent names.
    """
    result: dict[str, dict] = {}
    for wf in (*_WORKFLOWS, *_GENERIC_WORKFLOWS):
        tools: list[str] = []
        for st in wf.steps:
            if st.type == "api" and st.tool and not st.tool.startswith("system."):
                tools.append(st.tool)
        entities = [
            {
                "name": e.name,
                "description": e.description,
                "required": e.required,
                "default_preference_key": e.default_preference_key,
                "aliases": list(e.aliases),
            }
            for e in wf.entities
        ]
        result[wf.intent] = {"tools": tools, "entities": entities}
    return result


def get_provider_map() -> dict[str, tuple[str, ...]]:
    """Build intent → provider tuple map for tool_filter integration."""
    result: dict[str, tuple[str, ...]] = {}
    for wf in (*_WORKFLOWS, *_GENERIC_WORKFLOWS):
        result[wf.intent] = wf.related_providers
    return result


def get_action_map() -> dict[str, tuple[str, ...]]:
    """Build intent → action names tuple map for tool_filter integration."""
    result: dict[str, tuple[str, ...]] = {}
    for wf in (*_WORKFLOWS, *_GENERIC_WORKFLOWS):
        result[wf.intent] = wf.related_actions
    return result


def get_alias_map() -> dict[str, dict[str, str]]:
    """Build intent → {alias: canonical_name} map from EntityDefinition aliases.

    Single source of truth for entity key normalization.  Intake uses this
    to map LLM-generated keys (e.g. ``"time"``) to the canonical names
    expected by the readiness check (e.g. ``"date_time"``).
    """
    result: dict[str, dict[str, str]] = {}
    for wf in (*_WORKFLOWS, *_GENERIC_WORKFLOWS):
        aliases: dict[str, str] = {}
        for entity in wf.entities:
            for alias in entity.aliases:
                aliases[alias] = entity.name
        if aliases:
            result[wf.intent] = aliases
    return result


# ---------------------------------------------------------------------------
# Entity reference extraction
# ---------------------------------------------------------------------------

_ENTITY_REF_RE = re.compile(r"\{\{entities\.(\w+)\}\}")


def parse_entity_refs(args_template: dict[str, Any] | None) -> list[str]:
    """Extract entity names referenced in args_template (e.g. {{entities.attendee}})."""
    if not args_template:
        return []
    refs: set[str] = set()
    for value in args_template.values():
        if isinstance(value, str):
            refs.update(_ENTITY_REF_RE.findall(value))
    return sorted(refs)


# ---------------------------------------------------------------------------
# Composition functions — compound intent support
# ---------------------------------------------------------------------------


def decompose_intent(intent_type: str) -> list[WorkflowDefinition] | None:
    """Decompose a compound intent into known sub-workflows.

    Tries exact match first. If no match, scans the intent string for
    known workflow intent keywords and returns matching workflows in
    the order they appear in the string.

    Returns None if no known workflows match (→ LLM fallback).
    Returns a list with 1 entry for single-intent matches.
    Returns a list with 2+ entries for compound matches.
    """
    # Exact match
    wf = _WORKFLOW_BY_INTENT.get(intent_type)
    if wf is not None:
        return [wf]

    # Scan for known intent keywords in the intent string
    found: list[tuple[int, WorkflowDefinition]] = []
    for known_intent, known_wf in _WORKFLOW_BY_INTENT.items():
        idx = intent_type.find(known_intent)
        if idx >= 0:
            found.append((idx, known_wf))

    if not found:
        return None

    # Sort by position in the intent string (execution order)
    found.sort(key=lambda x: x[0])
    return [wf for _, wf in found]


def compose_workflows(
    workflows: list[WorkflowDefinition],
    entities: dict[str, Any] | None = None,  # noqa: ARG001
) -> tuple[list[StepTemplate], list[str]]:
    """Chain multiple workflow DAGs into a single step sequence.

    1. Renumber steps sequentially across all workflows
    2. Wire inter-workflow dependencies: each workflow's first step
       depends on the previous workflow's last step (sequential chaining)
    3. Fix internal after/context_from references to use new step numbers
    4. Update template references (``{{step_N.result.field}}``) to new numbering
    5. Return (combined_steps, combined_tool_list)
    """
    combined_steps: list[StepTemplate] = []
    combined_tools: list[str] = []
    seen_tools: set[str] = set()
    step_offset = 0
    prev_workflow_last_step: int | None = None

    for wf in workflows:
        # Build old→new step number mapping for this workflow
        renumber: dict[int, int] = {}
        for st in wf.steps:
            renumber[st.step] = st.step + step_offset

        for i, st in enumerate(wf.steps):
            new_step = renumber[st.step]

            # Fix after references
            new_after = tuple(renumber[a] for a in st.after if a in renumber)
            # Wire first step of this workflow to last step of previous workflow
            if i == 0 and prev_workflow_last_step is not None and prev_workflow_last_step not in new_after:
                new_after = (prev_workflow_last_step, *new_after)

            # Fix context_from references
            new_context = tuple(renumber[c] for c in st.context_from if c in renumber)

            # Fix template references in args_template
            new_args = _renumber_template_refs(st.args_template, renumber) if st.args_template else st.args_template

            combined_steps.append(StepTemplate(
                step=new_step,
                role=st.role,
                type=st.type,
                tool=st.tool,
                call=st.call,
                timeout_s=st.timeout_s,
                gate_id=st.gate_id,
                context_from=new_context,
                after=new_after,
                can_spawn=st.can_spawn,
                max_spawned_steps=st.max_spawned_steps,
                trust_level=st.trust_level,
                policy_ref=st.policy_ref,
                reasoning_config=st.reasoning_config,
                args_template=new_args,
            ))

            # Collect tools
            if st.tool and st.tool not in seen_tools:
                combined_tools.append(st.tool)
                seen_tools.add(st.tool)

        # Track the last step number for inter-workflow chaining
        if wf.steps:
            prev_workflow_last_step = renumber[wf.steps[-1].step]
        step_offset += len(wf.steps)

    return combined_steps, combined_tools


def merge_entity_requirements(
    workflows: list[WorkflowDefinition],
) -> list[EntityDefinition]:
    """Merge entity requirements from multiple workflows.

    De-duplicates entities with the same name (e.g., if both workflows
    need ``"attendee"``, keep it once). ``required=True`` wins over
    ``required=False`` if both exist.
    """
    seen: dict[str, EntityDefinition] = {}
    for wf in workflows:
        for entity in wf.entities:
            if entity.name in seen:
                existing = seen[entity.name]
                # required wins over optional
                if entity.required and not existing.required:
                    seen[entity.name] = entity
            else:
                seen[entity.name] = entity
    return list(seen.values())


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_STEP_REF_RE = re.compile(r"\{\{step_(\d+)\.")


def _renumber_template_refs(
    args: dict[str, Any],
    renumber: dict[int, int],
) -> dict[str, Any]:
    """Update ``{{step_N.result.field}}`` references in args_template."""
    result: dict[str, Any] = {}
    for key, value in args.items():
        if isinstance(value, str):
            def _replace(m: re.Match) -> str:
                old_num = int(m.group(1))
                new_num = renumber.get(old_num, old_num)
                return f"{{{{step_{new_num}."
            result[key] = _STEP_REF_RE.sub(_replace, value)
        else:
            result[key] = value
    return result
