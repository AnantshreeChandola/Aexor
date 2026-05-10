"""
Intent-aware tool catalog filter.

The raw MCP tool catalog can contain hundreds of tools across many providers.
Dumping the full catalog into the Planner's LLM prompt produces prompts in
the ~180k-token range, which exceeds typical organization rate limits on
frontier models. This module narrows the catalog to the providers that are
plausibly relevant to the user's intent before the prompt builder runs.

The filter is a best-effort keyword match on the free-form intent string.
If the intent does not match any known keyword, the full catalog is returned
(fail-open — better to blow the token budget than reject a valid intent).
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any, Iterable

from components.Planner.adapters.workflow_registry import get_action_map, get_provider_map

# Keyword → provider allowlist. Keyword is matched as a case-insensitive
# substring against Intent.intent. The union of all matching providers is
# used, so compound intents like "schedule_meeting_and_email" still work.
_INTENT_PROVIDER_MAP: dict[str, tuple[str, ...]] = {
    # Calendar / meetings — only bundle gmail for write intents (scheduling)
    "schedule_meeting": ("googlecalendar", "gmail"),
    "book_meeting": ("googlecalendar", "gmail"),
    "create_meeting": ("googlecalendar", "gmail"),
    "create_event": ("googlecalendar", "gmail"),
    "meeting": ("googlecalendar",),
    "calendar": ("googlecalendar",),
    "event": ("googlecalendar",),
    "appointment": ("googlecalendar", "gmail"),
    "reschedule": ("googlecalendar", "gmail"),
    "availability": ("googlecalendar",),
    "freebusy": ("googlecalendar",),
    # Email
    "send_email": ("gmail",),
    "draft_email": ("gmail",),
    "reply_email": ("gmail",),
    "email": ("gmail",),
    "mail": ("gmail",),
    # Docs / drive
    "create_document": ("googledocs", "googledrive"),
    "edit_document": ("googledocs", "googledrive"),
    "document": ("googledocs", "googledrive"),
    "doc": ("googledocs", "googledrive"),
    "drive": ("googledrive",),
    "file": ("googledrive",),
    "upload": ("googledrive",),
    "download": ("googledrive",),
    # Notion
    "notion": ("notion",),
    "note": ("notion",),
    "page": ("notion",),
    "database": ("notion",),
    "task": ("notion",),
    "todo": ("notion",),
    # GitHub
    "github": ("github",),
    "pull_request": ("github",),
    "issue": ("github",),
    "commit": ("github",),
    "repo": ("github",),
    "pr": ("github",),
    # Slack
    "slack": ("slack",),
    "message": ("slack",),
    "channel": ("slack",),
    # Video
    "youtube": ("youtube",),
    "video": ("youtube",),
    # LinkedIn
    "linkedin": ("linkedin",),
    "post": ("linkedin",),
}


def filter_tools_by_intent(
    tools: Iterable[Any], intent_type: str | None
) -> list[Any]:
    """Narrow a tool catalog to providers plausibly relevant to the intent.

    Args:
        tools: Full tool catalog (iterable of objects with ``provider_name``).
        intent_type: Free-form intent string from ``Intent.intent``.

    Returns:
        Filtered list of tools. If no keyword matches (or the filter would
        leave zero tools), returns the full catalog unchanged.
    """
    all_tools = list(tools)
    if not intent_type:
        return all_tools

    needle = intent_type.lower()
    allowed: set[str] = set()
    for keyword, providers in _INTENT_PROVIDER_MAP.items():
        if keyword in needle:
            allowed.update(providers)

    if not allowed:
        return all_tools

    filtered = [
        t for t in all_tools if getattr(t, "provider_name", "").lower() in allowed
    ]
    return filtered if filtered else all_tools


def _compact_property(prop: Any) -> dict[str, Any]:
    """Reduce a JSON Schema property to the fields the LLM actually needs."""
    if not isinstance(prop, dict):
        return {}
    out: dict[str, Any] = {}
    if "type" in prop:
        out["type"] = prop["type"]
    if "enum" in prop:
        enum_vals = prop["enum"]
        if isinstance(enum_vals, list) and len(enum_vals) <= 8:
            out["enum"] = enum_vals
    if prop.get("type") == "array" and isinstance(prop.get("items"), dict):
        item_type = prop["items"].get("type")
        if item_type:
            out["items"] = {"type": item_type}
    return out


def compact_tool_schemas(tools: Iterable[Any]) -> list[Any]:
    """Return a copy of the tool list with each input_schema slimmed down.

    The prompt builder serializes every tool's full JSON Schema into the user
    prompt. Per-property description strings dominate the size — a single
    GOOGLECALENDAR_CREATE_EVENT schema is ~11KB of mostly English text. This
    function reduces each property to ``{type, enum?, items?}`` and keeps only
    ``properties`` and ``required`` at the top level, which cuts the catalog
    footprint by roughly 4-5x while preserving the structural information the
    LLM needs to produce valid args.

    The original tool objects are not mutated; new ``ToolDefinition`` instances
    are returned via ``dataclasses.replace``.
    """
    result = []
    for tool in tools:
        schema = getattr(tool, "input_schema", None) or {}
        if not isinstance(schema, dict):
            result.append(tool)
            continue
        props = schema.get("properties") or {}
        compact_props = {name: _compact_property(p) for name, p in props.items()}
        compact_schema: dict[str, Any] = {
            "type": schema.get("type", "object"),
            "properties": compact_props,
        }
        if "required" in schema:
            compact_schema["required"] = schema["required"]
        try:
            result.append(replace(tool, input_schema=compact_schema))
        except TypeError:
            # Not a dataclass — fall back to the original tool.
            result.append(tool)
    return result


# ---------------------------------------------------------------------------
# Action-level tool filtering (second pass, within a provider)
# ---------------------------------------------------------------------------

MAX_TOOLS_PER_INTENT: int = 8

_FILTER_STOP_WORDS: frozenset[str] = frozenset({
    "the", "a", "an", "to", "for", "with", "from", "in", "on", "at",
    "by", "of", "and", "or", "is", "all",
})

# Intent pattern -> specific MCP action names. Matched as a case-insensitive
# substring of Intent.intent; the union of all matching actions is used.
_INTENT_ACTION_MAP: dict[str, tuple[str, ...]] = {
    # Email actions
    "send_email": ("SEND_EMAIL", "CREATE_DRAFT"),
    "draft_email": ("CREATE_DRAFT", "SEND_EMAIL"),
    "reply_email": ("REPLY_TO_EMAIL", "SEND_EMAIL", "GET_EMAIL"),
    "forward_email": ("SEND_EMAIL", "GET_EMAIL"),
    "read_email": ("GET_EMAIL", "LIST_EMAILS", "FETCH_EMAILS"),
    "list_email": ("LIST_EMAILS", "FETCH_EMAILS"),
    "search_email": ("LIST_EMAILS", "FETCH_EMAILS"),
    # Calendar actions
    "schedule_meeting": (
        "CREATE_EVENT", "FIND_FREE_SLOTS", "FIND_EVENT",
        "LIST_EVENTS", "LIST_ALL_CALENDARS",
    ),
    "book_meeting": (
        "CREATE_EVENT", "FIND_FREE_SLOTS", "FIND_EVENT",
        "LIST_EVENTS", "LIST_ALL_CALENDARS",
    ),
    "create_event": ("CREATE_EVENT", "FIND_FREE_SLOTS", "FIND_EVENT"),
    "create_meeting": (
        "CREATE_EVENT", "FIND_FREE_SLOTS", "FIND_EVENT", "LIST_EVENTS",
    ),
    "list_meetings": (
        "FIND_EVENT", "LIST_EVENTS", "LIST_ALL_CALENDARS",
        "EVENTS_LIST_ALL_CALENDARS",
    ),
    "check_calendar": (
        "FIND_EVENT", "LIST_EVENTS", "LIST_ALL_CALENDARS",
        "EVENTS_LIST_ALL_CALENDARS",
    ),
    "reschedule": (
        "UPDATE_EVENT", "FIND_EVENT", "FIND_FREE_SLOTS", "LIST_EVENTS",
    ),
    "cancel_meeting": ("DELETE_EVENT", "FIND_EVENT", "LIST_EVENTS"),
    "cancel_event": ("DELETE_EVENT", "FIND_EVENT"),
    "availability": ("FIND_FREE_SLOTS", "FIND_EVENT", "LIST_EVENTS"),
    "freebusy": ("FIND_FREE_SLOTS",),
    # Docs/Drive actions
    "create_document": ("CREATE_DOCUMENT", "CREATE_DOCUMENT_FROM_TEXT"),
    "edit_document": ("APPEND_TEXT", "UPDATE_DOCUMENT"),
    "upload_file": ("UPLOAD_FILE",),
    "download_file": ("DOWNLOAD_FILE",),
    "search_drive": ("SEARCH_FILE", "FIND_FILE", "LIST_FILES"),
    "list_files": ("LIST_FILES", "SEARCH_FILE"),
    # Notion actions
    "create_page": ("CREATE_PAGE", "CREATE_A_NEW_PAGE"),
    "create_task": ("CREATE_PAGE", "CREATE_A_NEW_PAGE", "CREATE_BLOCK"),
    "list_tasks": ("FETCH_PAGE", "LIST_PAGES", "FETCH_DATABASE"),
    "search_notion": ("SEARCH_NOTION", "FETCH_PAGE"),
    # GitHub actions
    "create_issue": ("CREATE_ISSUE", "ISSUES_CREATE"),
    "list_issues": ("ISSUES_LIST", "LIST_ISSUES"),
    "create_pr": ("CREATE_PULL_REQUEST", "PULLS_CREATE"),
    "list_pr": ("PULLS_LIST", "LIST_PULL_REQUESTS"),
    # Slack actions
    "send_message": ("SENDS_A_MESSAGE", "CHAT_POST_MESSAGE"),
    "search_messages": ("SEARCH_FOR_MESSAGES",),
    "list_channels": ("LIST_ALL_SLACK_TEAM_CHANNELS",),
}

# Merge registry-derived maps (additive — manual entries for non-workflow
# intents like notion, github, etc. remain unchanged).
_INTENT_PROVIDER_MAP.update(get_provider_map())
_INTENT_ACTION_MAP.update(get_action_map())


def _tokenize_intent(intent: str) -> set[str]:
    """Tokenize intent string into action keywords."""
    parts = re.split(r"[_\s.\-]+", intent.lower())
    return {p for p in parts if p and len(p) > 1 and p not in _FILTER_STOP_WORDS}


def _tokenize_tool_name(name: str) -> set[str]:
    """Tokenize MCP tool name into keywords."""
    parts = re.split(r"[_\s.\-]+", name.lower())
    return {p for p in parts if p and len(p) > 1 and p not in _FILTER_STOP_WORDS}


def filter_tools_by_action(
    tools: Iterable[Any],
    intent_type: str | None,
    intent_entities: dict[str, Any] | None = None,
) -> list[Any]:
    """Second-pass filter: narrow to action-relevant tools within providers.

    Called AFTER filter_tools_by_intent() (provider filter).
    Fail-open: returns input list if filter produces 0 results.

    Args:
        tools: Provider-filtered tool list.
        intent_type: Intent action string.
        intent_entities: Optional entity dict for additional hints.

    Returns:
        Action-filtered list, capped at MAX_TOOLS_PER_INTENT.
    """
    all_tools = list(tools)
    if not intent_type or not all_tools:
        return all_tools

    # Pass 1: Exact intent-to-action map lookup
    needle = intent_type.lower()
    matched_actions: set[str] = set()
    for pattern, actions in _INTENT_ACTION_MAP.items():
        if pattern in needle:
            matched_actions.update(actions)

    if matched_actions:
        filtered = [
            t for t in all_tools
            if any(
                action in getattr(t, "name", "").upper()
                for action in matched_actions
            )
        ]
        if filtered:
            return filtered[:MAX_TOOLS_PER_INTENT]

    # Pass 2: Fuzzy token matching fallback
    intent_tokens = _tokenize_intent(needle)
    if not intent_tokens:
        return all_tools

    scored: list[tuple[Any, int]] = []
    for tool in all_tools:
        tool_tokens = _tokenize_tool_name(getattr(tool, "name", ""))
        desc_tokens = _tokenize_tool_name(getattr(tool, "description", ""))
        all_tool_tokens = tool_tokens | desc_tokens
        match_count = len(intent_tokens & all_tool_tokens)
        if match_count > 0:
            scored.append((tool, match_count))

    if not scored:
        return all_tools  # fail-open

    scored.sort(key=lambda x: -x[1])
    return [t for t, _ in scored[:MAX_TOOLS_PER_INTENT]]
