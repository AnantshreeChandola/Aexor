"""
IntakeService -- multi-turn intent collection orchestrator.

Coordinates session store, intent parser, Planner readiness check,
and consent-gated ProfileStore defaults.

Reference: LLD Section 4.2, Sequences 8.1-8.9
"""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import ulid

from components.Intake.adapters.intent_parser import IntentParser, LLMBasedParser
from components.Intake.adapters.session_store import RedisSessionStore, SessionStore
from components.Intake.domain.models import (
    IntakeResponse,
    IntentParserError,
    MaxTurnsExceededError,
    ParseResult,
    Session,
    SessionNotFoundError,
    SessionTurn,
    ToolNotAvailableError,
)
from components.Planner.adapters.llm_adapter import LLMAdapter
from components.Planner.domain.models import (
    ToolNotAvailableError as PlannerToolNotAvailableError,
)
from shared.schemas.intent import Intent

logger = logging.getLogger(__name__)


class ProviderNotConnectedError(Exception):
    """User has not connected one or more required providers."""

    def __init__(self, provider_names: list[str]) -> None:
        self.provider_names = provider_names
        names = ", ".join(provider_names)
        super().__init__(
            f"You haven't connected: {names}. Go to Settings > Integrations to set them up."
        )


class IntakeService:
    """Orchestrates multi-turn intent collection."""

    def __init__(
        self,
        session_store: SessionStore,
        intent_parser: IntentParser,
        planner_service: Any,
        preference_service: Any,
        max_turns: int = 20,
        tool_catalog: Any | None = None,
        integration_manager: Any | None = None,
        db_adapter: Any | None = None,
    ) -> None:
        self._session_store = session_store
        self._intent_parser = intent_parser
        self._planner_service = planner_service
        self._preference_service = preference_service
        self._max_turns = max_turns
        self._tool_catalog = tool_catalog
        self._integration_manager = integration_manager
        self._db_adapter = db_adapter

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def process_message(
        self,
        user_id: str,
        message: str,
        context_tier: int,
        session_id: str | None = None,
        tz: str = "America/Chicago",
    ) -> IntakeResponse:
        """Process a user message and return collecting/ready response."""
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

        # 3. Parse intent via LLM (context includes pending_suggestions
        #    and last_follow_up so the LLM can interpret confirmations)
        parse_result = await self._safe_parse(message, session, tz)

        # 4. Apply confirmed suggestions: if LLM resolved a pending
        #    suggestion, clear it from contact_suggestions
        self._apply_confirmed_suggestions(session, parse_result)

        # 5. Merge into session
        self._merge_parse_result(session, parse_result)
        session.updated_at = datetime.now(UTC)

        # 6. Determine readiness
        status, missing_fields, planner_result = await self._check_readiness(session)

        # 7. Build follow-up or intent
        follow_up: str | None = None
        intent_dict: dict[str, Any] | None = None

        if status == "collecting":
            follow_up = await self._build_follow_up(
                session, missing_fields, planner_result, context_tier, user_id
            )
        else:
            intent_dict = self._build_intent(session, user_id, tz)

        # 8. Store last follow-up for parser context in next turn
        session.last_follow_up = follow_up

        # 9. Append turn with assistant response so next parse sees full history
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

        # 9. Save session
        await self._session_store.save(session)

        logger.info(
            "intake_process_complete",
            extra={
                "session_id": session.session_id,
                "user_id": user_id,
                "intent": session.detected_intent,
                "entity_count": len(session.extracted_entities),
                "turn_count": len(session.turns),
                "status": status,
            },
        )

        return IntakeResponse(
            status=status,
            session_id=session.session_id,
            detected_intent=session.detected_intent,
            collected_entities=session.extracted_entities,
            missing_fields=missing_fields,
            follow_up=follow_up,
            turn_count=len(session.turns),
            intent=intent_dict,
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

        # Warm caches at session start so readiness checks use fast
        # Redis lookups instead of per-tool DB queries / MCP calls.
        await self._warm_connection_cache(user_id)
        await self._warm_user_tools(user_id)

        return new_session

    async def _safe_parse(
        self,
        message: str,
        session: Session,
        tz: str = "UTC",
    ) -> ParseResult:
        try:
            return await self._intent_parser.parse(message, session, tz)
        except IntentParserError:
            logger.warning(
                "intake_parser_failed",
                extra={"session_id": session.session_id},
            )
            return ParseResult()

    @staticmethod
    def _merge_parse_result(session: Session, result: ParseResult) -> None:
        if result.intent is not None:
            session.detected_intent = result.intent
        session.extracted_entities.update(result.entities)
        session.extracted_constraints.update(result.constraints)

    async def _check_readiness(
        self,
        session: Session,
    ) -> tuple[str, list[str], Any]:
        """Returns (status, missing_fields, planner_result_or_None)."""
        if not session.detected_intent:
            return "collecting", [], None

        try:
            result = await self._planner_service.get_required_entities(
                intent_type=session.detected_intent,
                collected_entities=session.extracted_entities,
            )
        except PlannerToolNotAvailableError as exc:
            raise ToolNotAvailableError(
                intent_type=exc.intent_type,
                required_tools=exc.required_tools,
            ) from exc
        except Exception:
            logger.warning(
                "intake_planner_unavailable",
                extra={
                    "session_id": session.session_id,
                    "intent_type": session.detected_intent,
                },
            )
            # Heuristic fallback
            if session.extracted_entities:
                return "ready", [], None
            return "collecting", [], None

        missing_names = [e.name for e in result.missing_entities]
        if not missing_names:
            # All entities collected — check provider connections if available
            await self._check_provider_connections(session.user_id, result.resolved_tools)
            return "ready", [], result
        return "collecting", missing_names, result

    async def _warm_connection_cache(self, user_id: str) -> None:
        """Warm the connection cache for this user (best-effort)."""
        if self._integration_manager is None:
            return
        try:
            await self._integration_manager.warm_connection_cache(user_id)
        except Exception:
            logger.warning(
                "connection_cache_warm_failed",
                extra={"user_id": user_id},
            )

    async def _warm_user_tools(self, user_id: str) -> None:
        """Fetch per-user MCP tools and cache them (best-effort).

        Calls ``tools/list`` on the user's Composio MCP URL so we know
        exactly which tools this user has access to.  Graceful — failures
        are logged but do not block session creation.
        """
        if self._tool_catalog is None:
            return
        try:
            await self._tool_catalog.refresh_user(user_id)
        except Exception:
            logger.warning(
                "user_tool_cache_warm_failed",
                extra={"user_id": user_id},
            )

    async def _check_provider_connections(
        self,
        user_id: str,
        resolved_tools: list[str],
    ) -> None:
        """Check that the user has connected required providers.

        Re-warms the connection cache from Composio before checking so
        that connections made mid-session are picked up.  Uses the
        ToolCatalog to map tool IDs → provider names, then checks
        connection status via IntegrationManager (cache-first, DB
        fallback).  Skipped if either dependency is unavailable
        (graceful degradation).
        """
        if self._tool_catalog is None or self._integration_manager is None:
            return

        # Re-warm cache so mid-session connections are reflected
        await self._warm_connection_cache(user_id)

        missing_providers: list[str] = []
        checked: set[str] = set()
        for tool_id in resolved_tools:
            tool_def = self._tool_catalog.get_tool(tool_id)
            if tool_def is None or tool_def.provider_name in checked:
                continue
            checked.add(tool_def.provider_name)
            try:
                connected = await self._integration_manager.is_user_connected_cached(
                    user_id, tool_def.provider_name
                )
                if not connected:
                    missing_providers.append(tool_def.provider_name)
            except Exception:
                logger.warning(
                    "provider_connection_check_failed",
                    extra={
                        "user_id": user_id,
                        "provider": tool_def.provider_name,
                    },
                )
        if missing_providers:
            raise ProviderNotConnectedError(missing_providers)

    @staticmethod
    def _apply_confirmed_suggestions(session: Session, parse_result: ParseResult) -> None:
        """Reconcile LLM parse result with pending contact suggestions.

        The LLM sees pending_suggestions in context. When the user confirms,
        the LLM *should* emit the suggested value. But small models sometimes
        emit the literal user text (e.g. "yes") instead.

        Logic:
        - If the LLM emitted the exact suggested value → confirmed, clear it.
        - If the LLM emitted a bogus/short value for a pending suggestion
          entity (e.g. "yes", "correct") → replace with the suggested value
          and clear the suggestion.  The user confirmed; the LLM just failed
          to emit the right value.
        - If the LLM emitted a different *real* value (e.g. a different email)
          → the user corrected; keep suggestion cleared, use parsed value.
        - If the LLM didn't emit anything for the entity → leave as-is.
        """
        if not session.contact_suggestions:
            return
        # Common confirmation words the LLM might emit as a literal value
        confirmation_literals = {
            "yes",
            "yeah",
            "yep",
            "correct",
            "confirm",
            "sure",
            "ok",
            "that's right",
            "thats right",
            "true",
        }
        resolved = []
        for entity_name, suggested_value in session.contact_suggestions.items():
            parsed_value = parse_result.entities.get(entity_name)
            if parsed_value is None:
                continue
            if parsed_value == suggested_value:
                # LLM got it right
                resolved.append(entity_name)
            elif str(parsed_value).strip().lower().rstrip(".!,") in confirmation_literals:
                # LLM emitted a confirmation word instead of the value — fix it
                parse_result.entities[entity_name] = suggested_value
                resolved.append(entity_name)
            else:
                # User provided a different real value — accept it, clear suggestion
                resolved.append(entity_name)
        for name in resolved:
            del session.contact_suggestions[name]

    async def _resolve_contact_email(self, name: str) -> str | None:
        """Look up a contact email by name from the Users table.

        Returns email if exactly 1 match found, None otherwise (graceful).
        """
        if self._db_adapter is None:
            return None
        try:
            from sqlalchemy import func, select

            from shared.database.models import UserTable

            async with self._db_adapter.get_session() as session:
                stmt = (
                    select(UserTable.email, UserTable.full_name)
                    .where(
                        func.lower(UserTable.full_name).contains(name.lower()),
                        UserTable.deleted_at.is_(None),
                    )
                    .limit(5)
                )
                rows = (await session.execute(stmt)).all()
            if len(rows) == 1:
                return rows[0].email
            return None
        except Exception:
            logger.warning(
                "contact_resolution_failed",
                extra={"name": name},
            )
            return None

    async def _build_follow_up(
        self,
        session: Session,
        missing_fields: list[str],
        planner_result: Any,
        context_tier: int,
        user_id: str,
    ) -> str:
        if not session.detected_intent:
            return "What would you like me to help you with?"

        if not missing_fields:
            return "Could you provide a bit more detail?"

        # Consent-gated profile defaults (Tier 2+)
        lines: list[str] = []
        if context_tier >= 2 and planner_result is not None:
            for entity in planner_result.missing_entities:
                # Skip entities already collected (prevents re-suggestion loop)
                if entity.name in session.extracted_entities:
                    continue
                # Contact resolution for attendee_email
                if entity.name == "attendee_email":
                    attendee_name = session.extracted_entities.get("attendee")
                    if attendee_name and isinstance(attendee_name, str):
                        resolved = await self._resolve_contact_email(attendee_name)
                        if resolved is not None:
                            session.contact_suggestions["attendee_email"] = resolved
                            lines.append(f"- {entity.description}: Is this {resolved}?")
                            continue

                if (
                    entity.default_preference_key
                    and entity.name not in session.profile_defaults_offered
                ):
                    default_val = await self._try_profile_default(
                        user_id, entity.default_preference_key, context_tier
                    )
                    if default_val is not None:
                        session.profile_defaults_offered[entity.name] = default_val
                        lines.append(
                            f"- {entity.description}: I see you usually use "
                            f"{default_val}. Use that, or specify a different "
                            f"value?"
                        )
                        continue
                lines.append(f"- {entity.description}")
        else:
            for field in missing_fields:
                lines.append(f"- {field}")

        return "I still need a few details:\n" + "\n".join(lines)

    async def _try_profile_default(
        self,
        user_id: str,
        preference_key: str,
        context_tier: int,
    ) -> Any:
        try:
            evidence = await self._preference_service.get_preference(
                user_id=UUID(user_id),
                preference_key=preference_key,
                context_tier=context_tier,
                plan_id=None,
            )
            return evidence.value
        except Exception:
            return None

    @staticmethod
    def _build_intent(
        session: Session,
        user_id: str,
        tz: str,
    ) -> dict[str, Any]:
        trace_id = secrets.token_hex(16)
        intent_obj = Intent(
            intent=session.detected_intent,
            entities=session.extracted_entities,
            constraints=session.extracted_constraints,
            tz=tz,
            user_id=user_id,
            session_id=session.session_id,
            trace_id=trace_id,
        )
        return intent_obj.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_intake_service(
    redis_client: Any,
    llm_adapter: LLMAdapter,
    planner_service: Any,
    preference_service: Any,
    tool_catalog: Any | None = None,
    integration_manager: Any | None = None,
    db_adapter: Any | None = None,
) -> IntakeService:
    """Create IntakeService with concrete adapters."""
    session_store = RedisSessionStore(redis_client)
    intent_parser = LLMBasedParser(llm_adapter)
    return IntakeService(
        session_store=session_store,
        intent_parser=intent_parser,
        planner_service=planner_service,
        preference_service=preference_service,
        tool_catalog=tool_catalog,
        integration_manager=integration_manager,
        db_adapter=db_adapter,
    )
