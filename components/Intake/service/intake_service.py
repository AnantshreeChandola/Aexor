"""
IntakeService -- intent recognition and clarification.

Detects user intent via LLM (single-turn for skeleton, multi-turn for
chat fallback).  Entity collection is handled by the visual plan builder;
Intake only identifies *what* the user wants.

Reference: LLD Section 4.2
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

import ulid

from components.Intake.adapters.intent_parser import IntentParser, LLMBasedParser
from components.Intake.adapters.session_store import RedisSessionStore, SessionStore
from components.Intake.domain.models import (
    IntakeResponse,
    IntentParserError,
    MaxTurnsExceededError,
    ParseResult,
    RateLimitedError,
    Session,
    SessionNotFoundError,
    SessionTurn,
)
from components.Planner.adapters.llm_adapter import LLMAdapter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Entity key normalization — built from WorkflowRegistry aliases.
# Maps LLM-generated key names (e.g. "time") to canonical entity names
# (e.g. "date_time") so downstream matching works.
# ---------------------------------------------------------------------------

from components.Planner.adapters.workflow_registry import get_alias_map

_ENTITY_ALIAS_MAP: dict[str, dict[str, str]] = get_alias_map()


class IntakeService:
    """Detects user intent and clarifies via multi-turn when needed."""

    def __init__(
        self,
        session_store: SessionStore,
        intent_parser: IntentParser,
        max_turns: int = 20,
    ) -> None:
        self._session_store = session_store
        self._intent_parser = intent_parser
        self._max_turns = max_turns

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def process_message(
        self,
        user_id: str,
        message: str,
        session_id: str | None = None,
        tz: str = "America/Chicago",
    ) -> IntakeResponse:
        """Process a user message for intent clarification.

        Returns ``status: "ready"`` when an intent is detected (with
        partial entities extracted from the message), or ``"collecting"``
        with a follow-up prompt asking the user to clarify.
        """
        t_total = time.monotonic()

        # 1. Create or load session
        session = await self._resolve_session(user_id, session_id)

        logger.info(
            "intake_process_start",
            extra={
                "session_id": session.session_id,
                "user_id": user_id,
                "turn_count": len(session.turns),
            },
        )

        # 2. Max turns check
        if len(session.turns) >= self._max_turns:
            raise MaxTurnsExceededError(session.session_id, self._max_turns)

        # 3. Parse intent via LLM
        t0 = time.monotonic()
        parse_result = await self._route_and_parse(message, session, tz)
        t_parse = time.monotonic() - t0
        logger.info(
            "intake_timing_parse",
            extra={
                "session_id": session.session_id,
                "parse_ms": int(t_parse * 1000),
                "parsed_intent": parse_result.intent,
                "parsed_entities": list(parse_result.entities.keys()),
                "routing_tier": session.routing_tier,
            },
        )

        # 4. Merge into session
        self._merge_parse_result(session, parse_result)
        session.updated_at = datetime.now(UTC)

        # 5. Determine status: intent detected → ready, else → collecting
        if session.detected_intent:
            status = "ready"
            follow_up = None
        else:
            status = "collecting"
            follow_up = "What would you like me to help you with?"

        # 6. Append turn so next parse sees conversation history
        session.turns.append(
            SessionTurn(
                message=message,
                assistant_response=follow_up,
                timestamp=datetime.now(UTC),
                extracted_intent=parse_result.intent,
                extracted_entities=parse_result.entities,
                extracted_constraints=parse_result.constraints,
            )
        )

        # 7. Save session
        await self._session_store.save(session)

        t_total_ms = int((time.monotonic() - t_total) * 1000)
        logger.info(
            "intake_process_complete",
            extra={
                "session_id": session.session_id,
                "user_id": user_id,
                "intent": session.detected_intent,
                "entity_count": len(session.extracted_entities),
                "turn_count": len(session.turns),
                "status": status,
                "total_ms": t_total_ms,
            },
        )

        return IntakeResponse(
            status=status,
            session_id=session.session_id,
            detected_intent=session.detected_intent,
            collected_entities=session.extracted_entities,
            follow_up=follow_up,
            turn_count=len(session.turns),
        )

    async def reset_session(self, user_id: str, session_id: str) -> None:
        """Delete a session. Raises SessionNotFoundError if absent."""
        deleted = await self._session_store.delete(user_id, session_id)
        if not deleted:
            raise SessionNotFoundError(session_id)

        logger.info(
            "intake_session_reset",
            extra={"session_id": session_id, "user_id": user_id},
        )

    async def parse_once(
        self, message: str, user_id: str, tz: str = "America/Chicago"
    ) -> ParseResult:
        """Single-turn parse: detect intent + extract entities.

        Creates a transient session (not persisted) so the LLM parser
        has the Session context it expects, but avoids Redis writes.
        Used by the skeleton flow — 1 LLM call only.
        """
        session = Session(session_id=f"transient_{ulid.new()!s}", user_id=user_id)
        return await self._safe_parse(message, session, tz)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _resolve_session(
        self,
        user_id: str,
        session_id: str | None,
    ) -> Session:
        if session_id:
            existing = await self._session_store.get(user_id, session_id)
            if existing is not None:
                logger.info(
                    "intake_session_loaded",
                    extra={
                        "session_id": existing.session_id,
                        "user_id": user_id,
                    },
                )
                return existing

        new_session = Session(
            session_id=f"ses_{ulid.new()!s}",
            user_id=user_id,
        )
        logger.info(
            "intake_new_session",
            extra={
                "session_id": new_session.session_id,
                "user_id": user_id,
            },
        )
        return new_session

    async def _safe_parse(
        self,
        message: str,
        session: Session,
        tz: str = "UTC",
    ) -> ParseResult:
        t0 = time.monotonic()
        try:
            result = await self._intent_parser.parse(message, session, tz)
            t_ms = int((time.monotonic() - t0) * 1000)
            logger.info(
                "intake_parse_success",
                extra={
                    "session_id": session.session_id,
                    "parse_ms": t_ms,
                    "intent": result.intent,
                    "entities": list(result.entities.keys()),
                    "constraints": list(result.constraints.keys()),
                    "model": self._parser_model_name(),
                },
            )
            return result
        except IntentParserError as exc:
            t_ms = int((time.monotonic() - t0) * 1000)
            if self._is_rate_limited(exc.reason):
                logger.warning(
                    "intake_parser_rate_limited",
                    extra={
                        "session_id": session.session_id,
                        "reason": exc.reason,
                        "parse_ms": t_ms,
                    },
                )
                raise RateLimitedError(
                    provider=self._llm_provider_name(),
                    model=self._parser_model_name(),
                ) from exc
            logger.error(
                "intake_parser_failed",
                extra={
                    "session_id": session.session_id,
                    "error": str(exc),
                    "reason": exc.reason,
                    "parse_ms": t_ms,
                    "model": self._parser_model_name(),
                    "message_preview": message[:100],
                },
                exc_info=True,
            )
            return ParseResult()

    async def _route_and_parse(
        self,
        message: str,
        session: Session,
        tz: str = "UTC",
    ) -> ParseResult:
        """Parse via LLM (local-first when FallbackLLMAdapter is wired).

        Records which backend served the first parse for observability.
        """
        result = await self._safe_parse(message, session, tz)
        if session.routing_tier is None:
            llm = getattr(self._intent_parser, "_llm", None)
            provider = getattr(llm, "last_provider", None)
            session.routing_tier = provider if isinstance(provider, str) else "remote"
        return result

    @staticmethod
    def _is_rate_limited(reason: str | None) -> bool:
        if not reason:
            return False
        lowered = reason.lower()
        return "rate limit" in lowered or "rate_limited" in lowered or "429" in lowered

    def _llm_provider_name(self) -> str:
        parser = getattr(self, "_intent_parser", None)
        llm = getattr(parser, "_llm", None)
        if llm is None:
            return "llm"
        return type(llm).__name__.replace("Adapter", "").lower() or "llm"

    def _parser_model_name(self) -> str | None:
        parser = getattr(self, "_intent_parser", None)
        return getattr(parser, "_model", None)

    @staticmethod
    def _normalize_entity_keys(
        entities: dict[str, Any], intent_type: str | None,
    ) -> dict[str, Any]:
        """Map LLM-generated alias keys to canonical entity names."""
        if not intent_type or intent_type not in _ENTITY_ALIAS_MAP:
            return entities
        alias_map = _ENTITY_ALIAS_MAP[intent_type]
        normalized: dict[str, Any] = {}
        for key, value in entities.items():
            canonical = alias_map.get(key, key)
            if canonical in normalized and key != canonical:
                existing = normalized[canonical]
                if isinstance(existing, str) and isinstance(value, str):
                    normalized[canonical] = f"{existing} {value}"
                continue
            normalized[canonical] = value
        return normalized

    @staticmethod
    def _merge_parse_result(session: Session, result: ParseResult) -> None:
        if result.intent is not None:
            session.detected_intent = result.intent
        intent_type = result.intent or session.detected_intent
        normalized = IntakeService._normalize_entity_keys(result.entities, intent_type)
        session.extracted_entities.update(normalized)
        session.extracted_constraints.update(result.constraints)
        if result.sub_intents:
            session.sub_intents = result.sub_intents


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_intake_service(
    redis_client: Any,
    llm_adapter: LLMAdapter,
    local_llm_adapter: LLMAdapter | None = None,
) -> IntakeService:
    """Create IntakeService with concrete adapters.

    When ``local_llm_adapter`` is provided, wraps it with the remote
    adapter in a :class:`FallbackLLMAdapter` so Intake tries the local
    LLM first (e.g. Ollama/Llama 3.2) and falls back to the remote LLM
    (e.g. Claude) on failure.
    """
    import os

    effective_llm = llm_adapter
    if local_llm_adapter is not None:
        from components.Intake.adapters.fallback_llm import FallbackLLMAdapter

        local_model = os.environ.get("INTAKE_LOCAL_MODEL", "llama3.2:3b")
        effective_llm = FallbackLLMAdapter(
            local=local_llm_adapter,
            remote=llm_adapter,
            local_model=local_model,
        )

    session_store = RedisSessionStore(redis_client)
    intent_parser = LLMBasedParser(
        effective_llm,
        remote_llm=llm_adapter if local_llm_adapter is not None else None,
    )
    return IntakeService(
        session_store=session_store,
        intent_parser=intent_parser,
    )
