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
    "- Return ONLY valid JSON, no explanation."
)


@runtime_checkable
class IntentParser(Protocol):
    """Protocol for intent extraction from user messages."""

    async def parse(
        self,
        message: str,
        context: Session | None = None,
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
            "claude-haiku-4-5-20251001",
        )
        self._max_tokens = max_tokens

    async def parse(
        self,
        message: str,
        context: Session | None = None,
    ) -> ParseResult:
        """Extract intent, entities, constraints from a user message."""
        user_prompt = self._build_user_prompt(message, context)

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
    def _build_user_prompt(message: str, context: Session | None) -> str:
        parts = [f"User message: {message}"]
        if context and (context.detected_intent or context.extracted_entities):
            parts.append("\nPrior context:")
            if context.detected_intent:
                parts.append(f"  detected_intent: {context.detected_intent}")
            if context.extracted_entities:
                parts.append(
                    f"  extracted_entities: {json.dumps(context.extracted_entities)}"
                )
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
