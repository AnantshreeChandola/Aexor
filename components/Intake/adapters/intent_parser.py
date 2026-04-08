"""
Intent Parser Protocol and LLM-based Implementation

IntentParser protocol for intent extraction. LLMBasedParser uses
the shared LLMAdapter (Anthropic Claude) for JSON-structured extraction.

Reference: LLD Section 7.2
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Protocol, runtime_checkable

from components.Intake.domain.models import (
    IntentParserError,
    ParseResult,
    Session,
)
from components.Planner.adapters.llm_adapter import LLMAdapter

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are an intent extraction engine. Given a user message and optional "
    "prior context, extract: intent (string), entities (dict), constraints (dict).\n"
    "Return JSON only with this structure:\n"
    '{"intent": "action_type", "entities": {...}, "constraints": {...}}\n\n'
    "Rules:\n"
    "- Open taxonomy: any intent type is valid.\n"
    "- intent: a short snake_case action type (e.g. 'schedule_meeting').\n"
    "- entities: extracted key-value pairs (e.g. {'attendee': 'Alice'}).\n"
    "- constraints: user preferences or limits (e.g. {'prefer_morning': true}).\n"
    "- If intent cannot be determined, return intent: null.\n"
    "- If context is provided, merge: new values override old.\n"
    "- DATE/TIME RESOLUTION: When the user provides relative dates or times "
    "(e.g. 'Friday', 'tomorrow', 'next week', '2pm'), resolve them to "
    "absolute ISO 8601 datetime strings using the current date/time provided "
    "in the prompt context. For example, if today is Wednesday 2026-04-08 and "
    "the user says 'Friday 2:00 am', resolve to '2026-04-10T02:00:00'. "
    "Always use the user's timezone if provided. Never leave dates as "
    "relative strings like 'Friday' or 'tomorrow'.\n"
    "- IMPORTANT: If pending_suggestions is provided in the context, it means "
    "the system previously suggested a value for an entity (e.g. an email). "
    "If the user confirms (says 'yes', 'correct', 'that's right', etc.), "
    "emit the suggested value as the entity value — NOT the word 'yes'. "
    "For example, if pending_suggestions has {\"attendee_email\": \"alice@x.com\"} "
    "and the user says 'yes', return {\"attendee_email\": \"alice@x.com\"}.\n"
    "- Return ONLY valid JSON, no explanation."
)


@runtime_checkable
class IntentParser(Protocol):
    """Protocol for intent extraction from user messages."""

    async def parse(
        self,
        message: str,
        context: Session | None = None,
        tz: str = "UTC",
    ) -> ParseResult: ...


class LLMBasedParser:
    """LLM-based intent parser using Anthropic Claude via LLMAdapter."""

    def __init__(
        self,
        llm_adapter: LLMAdapter,
        model: str | None = None,
        max_tokens: int = 512,
    ) -> None:
        self._llm = llm_adapter
        self._model = model or os.environ.get(
            "INTAKE_PARSER_MODEL",
            "claude-sonnet-4-5-20250929",
        )
        self._max_tokens = max_tokens

    async def parse(
        self,
        message: str,
        context: Session | None = None,
        tz: str = "UTC",
    ) -> ParseResult:
        """Extract intent, entities, constraints from a user message."""
        user_prompt = self._build_user_prompt(message, context, tz)

        try:
            raw = await self._llm.generate(
                model=self._model,
                system_prompt=SYSTEM_PROMPT,
                user_prompt=user_prompt,
                max_tokens=self._max_tokens,
                temperature=0.0,
            )
            return self._parse_response(raw)
        except IntentParserError:
            raise
        except Exception as exc:
            raise IntentParserError(str(exc)) from exc

    @staticmethod
    def _build_user_prompt(message: str, context: Session | None, tz: str = "UTC") -> str:
        parts: list[str] = []

        # Current date/time context for resolving relative dates
        now = datetime.now()
        parts.append(
            f"Current date/time: {now.strftime('%A, %Y-%m-%d %H:%M')} "
            f"(timezone: {tz})"
        )

        if context and context.turns:
            # Replay conversation history so the LLM sees the full
            # back-and-forth — just like Claude Chat would.
            parts.append("\nConversation so far:")
            for turn in context.turns:
                parts.append(f"  User: {turn.message}")
                if turn.assistant_response:
                    parts.append(f"  Assistant: {turn.assistant_response}")

        # Current state
        if context and (context.detected_intent or context.extracted_entities):
            parts.append("\nCurrent state:")
            if context.detected_intent:
                parts.append(f"  detected_intent: {context.detected_intent}")
            if context.extracted_entities:
                parts.append(f"  extracted_entities: {json.dumps(context.extracted_entities)}")
            if context.contact_suggestions:
                parts.append(f"  pending_suggestions: {json.dumps(context.contact_suggestions)}")

        parts.append(f"\nNew user message: {message}")
        return "\n".join(parts)

    @staticmethod
    def _parse_response(raw: str) -> ParseResult:
        """Parse LLM JSON response into ParseResult."""
        cleaned = raw.strip()
        # Strip markdown fences if present
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0]
            cleaned = cleaned.strip()

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise IntentParserError(f"Invalid JSON: {exc}") from exc

        if not isinstance(data, dict):
            raise IntentParserError("LLM response is not a JSON object")

        return ParseResult(
            intent=data.get("intent"),
            entities=data.get("entities", {}),
            constraints=data.get("constraints", {}),
        )
