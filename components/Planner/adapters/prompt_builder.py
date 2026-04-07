"""
Prompt builder for LLM plan generation.

Builds structured system + user prompts from Intent, Evidence, and tool catalog.

Reference: LLD SS6.4
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from shared.schemas.evidence import EvidenceItem
from shared.schemas.intent import Intent

MAX_INTENT_TEXT_BYTES = 10_240  # 10KB truncation limit


class PromptBuilder:
    """Builds system and user prompts for plan generation."""

    def build_system_prompt(self) -> str:
        """Static system prompt with Plan JSON schema instructions."""
        return """You are a hybrid plan generator. Your job is to produce a valid JSON execution plan.

## Output Format
Return ONLY a valid JSON object (no markdown, no code fences) matching this schema:

{
  "graph": [
    {
      "step": <int, 1-indexed>,
      "mode": "interactive" | "durable",
      "role": "Fetcher" | "Analyzer" | "Watcher" | "Resolver" | "Booker" | "Notifier" | "Reasoner",
      "uses": "<tool_name from catalog>",
      "call": "<same tool_name>",
      "args": { ... },
      "after": [<step numbers this depends on>],
      "timeout_s": <int, 5-3600, default 30>,
      "gate_id": "<required for Booker role, null otherwise>",
      "dry_run": true,
      "type": "api" | "llm_reasoning" | "policy_check",
      "context_from": [<step numbers whose results feed into this step>],
      "can_spawn": false,
      "max_spawned_steps": null | <int, 1-10>,
      "policy_ref": "<policy_id or null>",
      "reasoning_config": null | {
        "model": "<model_id>",
        "temperature": <float, 0.0-1.0>,
        "max_tokens": <int, 256-8192>,
        "system_prompt_ref": "<prompt template reference>",
        "output_schema_ref": null | "<json schema reference>"
      }
    }
  ],
  "constraints": {
    "scopes": ["<aggregated OAuth scopes from all tools used>"],
    "ttl_s": 900,
    "max_retries": 3,
    "policy_version": 0
  },
  "plugins": ["<unique tool_ids used in graph>"]
}

## Rules
1. Every step MUST have "dry_run": true (preview-first safety).
2. Steps with role "Booker" MUST have a non-null "gate_id" (format: "gate-A", "gate-B", etc.) for human-in-the-loop approval.
3. Only use tool names from the provided catalog. The `uses` and `call` fields should both be the tool name.
4. Step dependencies in "after" must reference earlier step numbers only (no forward or self-references).
5. Steps are numbered sequentially starting from 1.
6. Maximum 50 steps, maximum 10 parallel steps (steps with same "after" dependency).
7. NEVER include credential values, API keys, or secrets in args. Use credential ID references only.
8. Role assignments:
   - Fetcher: Read-only data retrieval
   - Analyzer: Data processing and analysis
   - Watcher: Monitor for changes or conditions
   - Resolver: Resolve conflicts or ambiguities
   - Booker: Actions that modify external state (requires gate_id)
   - Notifier: Send notifications or alerts
   - Reasoner: LLM-based adaptive reasoning (requires type="llm_reasoning", policy_ref, reasoning_config)
9. Step types:
   - "api" (default): Deterministic tool call
   - "llm_reasoning": LLM-based adaptive decision — MUST have role="Reasoner", policy_ref, and reasoning_config
   - "policy_check": Policy evaluation gate — MUST have policy_ref
10. Spawning rules:
   - Steps with can_spawn=true may create child steps at runtime (max_spawned_steps ≤ 10)
   - Spawned steps inherit their parent's policy_ref
   - No recursive spawning (spawned steps cannot themselves spawn)
   - Total graph size must stay ≤ 100 steps
11. Return raw JSON only. No explanation, no markdown wrapping.
12. Plan structure pattern — always follow this ordering:
    a. Fetcher step(s): Read-only data retrieval first (e.g., check calendar availability, list events)
    b. Reasoner step: Analyze fetched data and decide if action is safe (type="llm_reasoning", can_spawn=true, trust_level="trusted"). The Reasoner serves as the conflict-detection and recovery handler.
    c. Booker step: Execute the write action with CONCRETE values from the intent (e.g., create event at the requested time). MUST have role="Booker" and gate_id for HITL approval.
    d. Notifier step: Send notifications (e.g., email attendees) after the write action succeeds.
13. Recovery Reasoner requirements:
    - The Reasoner step MUST have can_spawn=true and max_spawned_steps=3
    - The Reasoner step MUST have trust_level="trusted" (so it can spawn steps)
    - The `uses` field on llm_reasoning steps should describe the reasoning context (e.g., "calendar_conflict_resolver") — it does NOT need to be a catalog tool
    - If a Booker step fails (e.g., time conflict), the system routes the error to this Reasoner, which spawns a new Booker step with corrected args (e.g., a different time slot)
14. Write operations (create, update, delete, send) MUST use role="Booker" with a gate_id.
15. When the intent involves attendees or recipients, ALWAYS include a Notifier step with the appropriate email/notification tool.
16. Conflict detection — for scheduling intents the Fetcher step MUST query existing events for the target date/time range so the Reasoner can detect overlaps. Use a tool that returns events (e.g., GOOGLECALENDAR_FIND_EVENT, GOOGLECALENDAR_EVENTS_LIST_ALL_CALENDARS) — NOT a metadata-only tool like GOOGLECALENDAR_GET_CALENDAR which returns no event data. Pass the target date range in the Fetcher args (timeMin/timeMax or equivalent).
17. Template references — step args may reference earlier step results using {{step_N.result.field}} syntax. ONLY reference API step results (Fetcher, Booker), NEVER reference Reasoner (llm_reasoning) step results — Reasoner output is free-form text, not structured data. Booker step args MUST use concrete values derived from the intent, not templates referencing Reasoner output.
18. IMPORTANT: The Notifier step's email body should reference the ACTUAL time/date from the Booker step args, not template references. Use concrete values from the intent entities."""

    def build_user_prompt(
        self,
        intent: Intent,
        evidence: list[EvidenceItem],
        catalog: Any,
    ) -> str:
        """Build per-request user prompt with intent, evidence, and catalog."""
        intent_data = intent.model_dump(mode="json")
        intent_json = json.dumps(intent_data, indent=2)

        # Truncate if too large
        if len(intent_json.encode("utf-8")) > MAX_INTENT_TEXT_BYTES:
            intent_json = intent_json[:MAX_INTENT_TEXT_BYTES] + "\n... [truncated]"

        evidence_list = [e.model_dump(mode="json") for e in evidence]
        evidence_json = json.dumps(evidence_list, indent=2)

        # Build tool catalog section
        tools_info = []
        if isinstance(catalog, list):
            # list[ToolDefinition] from ToolCatalog
            for tool in catalog:
                tool_entry: dict[str, Any] = {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                }
                tools_info.append(tool_entry)
        elif hasattr(catalog, "tools"):
            # Legacy CatalogResponse fallback
            for tool in catalog.tools:
                tool_entry = {
                    "tool_id": tool.tool_id,
                    "display_name": getattr(tool, "display_name", ""),
                }
                tools_info.append(tool_entry)
        catalog_json = json.dumps(tools_info, indent=2)

        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S%z")
        user_tz = intent.tz or "UTC"

        return f"""## Current Date/Time
Now: {now} (user timezone: {user_tz})
Resolve all relative dates (e.g. "tomorrow", "next Tuesday") relative to this timestamp.

## User Intent
{intent_json}

## Available Evidence (from memory)
{evidence_json}

## Available Tools (from catalog)
{catalog_json}

Generate a plan that fulfills the user's intent using the available tools and evidence.
Use evidence to inform argument values (e.g., preferred meeting duration, timezone).
Aggregate all required OAuth scopes from the tools you use into constraints.scopes."""
