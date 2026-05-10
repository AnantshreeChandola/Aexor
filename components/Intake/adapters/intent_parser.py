
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
from components.Planner.adapters.workflow_registry import get_entity_map

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT_BASE = (
    "You are an intent extraction engine. Given a user message and optional "
    "prior context, extract: intent (string), entities (dict), constraints (dict), "
    "confidence (float).\n"
    "Return JSON only with this structure:\n"
    '{"intent": "action_type", "entities": {...}, "constraints": {...}, "confidence": 0.85}\n\n'
    "Rules:\n"
    "- Open taxonomy: any intent type is valid.\n"
    "- intent: a short snake_case action type (e.g. 'schedule_meeting').\n"
    "- entities: extracted key-value pairs (e.g. {'attendee': 'Alice'}). "
    "For known intents (listed below), you MUST use the exact canonical key "
    "names from the entity schema. Do NOT invent alternative names.\n"
    "- constraints: user preferences or limits (e.g. {'prefer_morning': true}).\n"
    "- Always include a 'confidence' field (float, 0.0 to 1.0) indicating how "
    "certain you are about the intent classification. 1.0 = certain, 0.0 = guess. "
    "Lower your confidence when: the message is ambiguous, multiple intents could "
    "apply, or the user mentions a specific provider/tool that doesn't match the "
    "default provider for the intent.\n"
    "- If intent cannot be determined, return intent: null.\n"
    "- If context is provided, merge: new values override old.\n"
    "- DATE/TIME RESOLUTION: When the user provides relative dates or times "
    "(e.g. 'Friday', 'tomorrow', 'next week', '2pm'), resolve them to "
    "absolute ISO 8601 datetime strings using the current date/time provided "
    "in the prompt context. For example, if today is Wednesday 2026-04-08 and "
    "the user says 'Friday 2:00 am', resolve to '2026-04-10T02:00:00'. "
    "Always use the user's timezone if provided. Never leave dates as "
    "relative strings like 'Friday' or 'tomorrow'.\n"
    "- IMPORTANT: If the user specifies a DATE but NOT a TIME (e.g. 'tomorrow', "
    "'next Monday', 'May 5th'), resolve only the date portion and set the "
    "entity to the date string (e.g. '2026-05-02') WITHOUT inventing a time. "
    "Do NOT default to the current time of day. The system will ask the user "
    "for a specific time separately. Only include a time component when the "
    "user explicitly mentions one (e.g. 'tomorrow at 3pm', 'Friday 2:00').\n"
    "- COMPOUND INTENTS: If the user's request involves MULTIPLE known intents "
    "(e.g., 'schedule a meeting with Alice and email her the details'), "
    "output a sub_intents array listing which known intents compose the "
    "request, in execution order. If the request maps to a single known "
    "intent or an unknown intent, omit sub_intents or return an empty array. "
    "Example: 'book a meeting and send a confirmation email' "
    "→ intent: 'schedule_meeting_and_email', sub_intents: ['schedule_meeting', 'send_email']\n"
    "- Return ONLY valid JSON, no explanation."
)


_ENTITY_MAP: dict[str, dict] = get_entity_map()


def _build_entity_schema_reference() -> str:
    """Build a compact reference of all known intents and their canonical entity keys.

    Included in the system prompt so the LLM outputs canonical key names
    on every turn — including the first, before an intent is detected.
    """
    lines = [
        "ENTITY SCHEMAS — for known intents, use these exact entity key names "
        "(do NOT use synonyms or alternative names):"
    ]
    for intent, schema in _ENTITY_MAP.items():
        keys = ", ".join(
            f"{e['name']}{'*' if e.get('required', True) else ''}"
            for e in schema["entities"]
        )
        lines.append(f"  {intent}: {keys}")
    lines.append("  (* = required)")
    return "\n".join(lines)


# Final system prompt: base rules + canonical entity schemas from registry
SYSTEM_PROMPT = _SYSTEM_PROMPT_BASE + "\n\n" + _build_entity_schema_reference()


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
        remote_llm: LLMAdapter | None = None,
    ) -> None:
        self._llm = llm_adapter
        self._model = model or os.environ.get(
            "INTAKE_PARSER_MODEL",
            "claude-sonnet-4-5-20250929",
        )
        self._max_tokens = max_tokens
        self._remote_llm = remote_llm
        self._confidence_threshold = float(
            os.environ.get("INTAKE_CONFIDENCE_THRESHOLD", "0.80")
        )

    async def parse(
        self,
        message: str,
        context: Session | None = None,
        tz: str = "UTC",
    ) -> ParseResult:
        """Extract intent, entities, constraints from a user message.

        Retries once on transient failures (empty response, JSON parse error).
        """
        user_prompt = self._build_user_prompt(message, context, tz)

        logger.info(
            "intent_parser_calling_llm",
            extra={
                "model": self._model,
                "prompt_length": len(user_prompt),
                "message_preview": message[:100],
            },
        )

        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                raw = await self._llm.generate(
                    model=self._model,
                    system_prompt=SYSTEM_PROMPT,
                    user_prompt=user_prompt,
                    max_tokens=self._max_tokens,
                    temperature=0.0,
                )
                logger.info(
                    "intent_parser_llm_response",
                    extra={
                        "model": self._model,
                        "raw_response": raw[:500],
                        "response_length": len(raw),
                        "attempt": attempt + 1,
                    },
                )
                result = self._parse_response(raw)

                # Confidence-based escalation: if local LLM confidence is
                # low (or missing), re-classify using the remote LLM.
                if self._remote_llm is not None and (
                    result.confidence is None
                    or result.confidence < self._confidence_threshold
                ):
                    logger.info(
                        "intent_parser_low_confidence_escalating",
                        extra={
                            "local_intent": result.intent,
                            "local_confidence": result.confidence,
                            "threshold": self._confidence_threshold,
                        },
                    )
                    try:
                        remote_raw = await self._remote_llm.generate(
                            model=self._model,
                            system_prompt=SYSTEM_PROMPT,
                            user_prompt=user_prompt,
                            max_tokens=self._max_tokens,
                            temperature=0.0,
                        )
                        logger.info(
                            "intent_parser_remote_escalation_response",
                            extra={
                                "raw_response": remote_raw[:500],
                                "response_length": len(remote_raw),
                            },
                        )
                        result = self._parse_response(remote_raw)
                        result.escalated = True
                        logger.info(
                            "intent_parser_escalation_result",
                            extra={
                                "intent": result.intent,
                                "confidence": result.confidence,
                                "entities": list(result.entities.keys()),
                                "entity_values": {
                                    k: str(v)[:100] for k, v in result.entities.items()
                                },
                            },
                        )
                    except Exception as esc_exc:
                        logger.warning(
                            "intent_parser_escalation_failed",
                            extra={"error": str(esc_exc)},
                        )
                        # Fall back to local result on escalation failure

                return result
            except IntentParserError as exc:
                last_exc = exc
                if attempt == 0:
                    logger.warning(
                        "intent_parser_retry",
                        extra={
                            "model": self._model,
                            "error": str(exc),
                            "attempt": attempt + 1,
                        },
                    )
                    continue
                raise
            except Exception as exc:
                logger.error(
                    "intent_parser_llm_exception",
                    extra={
                        "model": self._model,
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                    },
                    exc_info=True,
                )
                raise IntentParserError(str(exc)) from exc

        # Should not reach here, but be safe
        raise last_exc or IntentParserError("Parse failed after retries")

    @staticmethod
    def _build_user_prompt(message: str, context: Session | None, tz: str = "UTC") -> str:
        parts: list[str] = []

        # Current date/time context for resolving relative dates
        now = datetime.now()
        parts.append(f"Current date/time: {now.strftime('%A, %Y-%m-%d %H:%M')} (timezone: {tz})")

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

        parts.append(f"\nNew user message: {message}")
        return "\n".join(parts)

    @staticmethod
    def _parse_response(raw: str) -> ParseResult:
        """Parse LLM JSON response into ParseResult."""
        cleaned = raw.strip()

        # Guard: empty response from LLM
        if not cleaned:
            raise IntentParserError("LLM returned empty response")

        # Strip markdown fences if present — handle text before fences
        # (e.g. LLM preamble like "I need to extract..." before ```json)
        if "```" in cleaned:
            # Find the first ``` block and extract its contents
            fence_start = cleaned.index("```")
            after_fence = cleaned[fence_start + 3 :]
            # Skip optional language tag (e.g. "json")
            if after_fence and not after_fence.startswith("\n"):
                after_fence = after_fence.split("\n", 1)[-1] if "\n" in after_fence else after_fence
            # Extract content up to closing fence
            if "```" in after_fence:
                cleaned = after_fence.rsplit("```", 1)[0].strip()
            else:
                cleaned = after_fence.strip()
        else:
            # No fences — try to find JSON object in the text
            brace_start = cleaned.find("{")
            if brace_start > 0:
                cleaned = cleaned[brace_start:]

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            logger.warning(
                "intent_parser_json_failed",
                extra={"cleaned_preview": cleaned[:200], "raw_preview": raw[:200]},
            )
            raise IntentParserError(f"Invalid JSON: {exc}") from exc

        if not isinstance(data, dict):
            raise IntentParserError("LLM response is not a JSON object")

        # Extract and normalize confidence score
        raw_confidence = data.get("confidence")
        confidence: float | None = None
        if raw_confidence is not None:
            try:
                confidence = float(raw_confidence)
                # Normalize: LLMs sometimes return 85 instead of 0.85
                if confidence > 1.0:
                    confidence = confidence / 100.0
                # Clamp to [0.0, 1.0]
                confidence = max(0.0, min(1.0, confidence))
            except (TypeError, ValueError):
                confidence = None

        return ParseResult(
            intent=data.get("intent"),
            entities=data.get("entities", {}),
            constraints=data.get("constraints", {}),
            sub_intents=data.get("sub_intents", []),
            confidence=confidence,
        )
