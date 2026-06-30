"""
LLM Client Adapter

Protocol and provider implementations for reasoning steps
with two-tier trust enforcement.

- AnthropicReasoningAdapter: legacy, direct Anthropic SDK path (retained
  for tests and for direct instantiation with a key).
- GenericReasoningAdapter: provider-agnostic shim over the Planner-side
  LLMAdapter, so ExecuteOrchestrator reasoning honours the same
  LLM_PROVIDER / LLM_API_KEY selection as the Planner and Intake layers.

Reference: LLD.md Section 6.2
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Literal, Protocol, runtime_checkable

from shared.schemas.policy import ReasoningConfig

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = os.environ.get("REASONING_MODEL", "claude-haiku-4-5-20251001")
_ANTHROPIC_PREFIXES = ("claude-",)

# System-prompt suffix used by GenericReasoningAdapter in Tier 2 mode so the
# LLM emits a structured spawn_requests field as JSON (since we don't have
# native tool-use on arbitrary providers).
_SPAWN_INSTRUCTION = (
    "\n\nIf you need to request additional execution steps, include a "
    '"spawn_requests" array in your JSON response. Each entry must be an '
    "object with keys: role (string), uses (string), call (string), "
    'args (object), and optionally step_type (string, default "api"). '
    "If you don't need to spawn any steps, omit the field or use an empty "
    "array. Respond with valid JSON only."
)


@runtime_checkable
class LLMClient(Protocol):
    """Protocol for LLM reasoning dispatch."""

    async def reason(
        self,
        config: ReasoningConfig,
        context: list[dict[str, Any]],
        trust_level: Literal["untrusted_input", "trusted"],
    ) -> dict[str, Any]: ...


class AnthropicReasoningAdapter:
    """Anthropic API adapter for reasoning steps.

    Enforces two-tier trust:
    - Tier 1 (untrusted_input): tools=[], output schema validated
    - Tier 2 (trusted): tools enabled, spawn requests parsed
    """

    def __init__(self, api_key: str | None = None) -> None:
        import anthropic

        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    async def reason(
        self,
        config: ReasoningConfig,
        context: list[dict[str, Any]],
        trust_level: Literal["untrusted_input", "trusted"],
    ) -> dict[str, Any]:
        """Dispatch reasoning to Anthropic API with trust enforcement.

        Args:
            config: LLM configuration for this reasoning step.
            context: List of context dicts from upstream steps.
            trust_level: Trust tier for this call.

        Returns:
            Dict with 'content' and optionally 'spawn_requests'.
        """
        messages = self._build_messages(context)

        # Guard: reject non-Anthropic model names from Planner output
        model = config.model
        if not model.startswith(_ANTHROPIC_PREFIXES):
            logger.warning(
                "non_anthropic_model_overridden",
                extra={"requested": model, "using": _DEFAULT_MODEL},
            )
            model = _DEFAULT_MODEL

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": config.max_tokens,
            "temperature": config.temperature,
            "system": config.system_prompt_ref,
            "messages": messages,
        }

        if trust_level == "trusted":
            # Tier 2: enable spawn tool
            kwargs["tools"] = self._build_spawn_tools()
        # Tier 1 (untrusted_input): no tools — omit 'tools' key entirely
        # (Anthropic API rejects tools=[])

        response = await self._client.messages.create(**kwargs)
        return self._parse_response(response, trust_level)

    def _build_messages(self, context: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Build message list from step context.

        Each step result is capped at ~12K chars to stay well within
        Anthropic's per-minute input token limits on lower-tier plans.
        """
        _MAX_RESULT_CHARS = 12_000
        messages: list[dict[str, Any]] = []
        for ctx in context:
            result_str = str(ctx.get("result", {}))
            if len(result_str) > _MAX_RESULT_CHARS:
                result_str = result_str[:_MAX_RESULT_CHARS] + "... [truncated]"
            messages.append(
                {
                    "role": "user",
                    "content": f"Step {ctx.get('step', '?')} result: {result_str}",
                }
            )
        if not messages:
            messages.append(
                {
                    "role": "user",
                    "content": "No upstream context available.",
                }
            )
        return messages

    def _build_spawn_tools(self) -> list[dict[str, Any]]:
        """Build tool definitions for Tier 2 spawn requests."""
        return [
            {
                "name": "spawn_step",
                "description": "Request spawning a new execution step",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "role": {"type": "string"},
                        "uses": {"type": "string"},
                        "call": {"type": "string"},
                        "args": {"type": "object"},
                        "step_type": {"type": "string", "default": "api"},
                    },
                    "required": ["role", "uses", "call"],
                },
            }
        ]

    def _parse_response(
        self,
        response: Any,
        trust_level: str,
    ) -> dict[str, Any]:
        """Parse Anthropic response into structured result."""
        content_parts: list[str] = []
        spawn_requests: list[dict[str, Any]] = []

        for block in response.content:
            if hasattr(block, "text"):
                content_parts.append(block.text)
            elif (
                hasattr(block, "type")
                and block.type == "tool_use"
                and trust_level == "trusted"
                and block.name == "spawn_step"
            ):
                spawn_requests.append(block.input)

        result: dict[str, Any] = {
            "content": "\n".join(content_parts),
        }
        if trust_level == "trusted":
            result["spawn_requests"] = spawn_requests

        return result


class GenericReasoningAdapter:
    """Provider-agnostic reasoning adapter over the Planner's LLMAdapter.

    Lets ExecuteOrchestrator reuse whichever provider is configured via
    LLM_PROVIDER / LLM_API_KEY (Anthropic, OpenAI, Gemini, or claude_code)
    without hard-coding an Anthropic client. Tier 2 spawn requests are
    surfaced by parsing a JSON ``spawn_requests`` field out of the model's
    text response instead of real tool-use, since only Anthropic supports
    that schema natively.
    """

    def __init__(
        self,
        adapter: Any,  # components.Planner.adapters.llm.protocol.LLMAdapter
        default_model: str | None = None,
    ) -> None:
        self._adapter = adapter
        self._default_model = default_model or _DEFAULT_MODEL

    async def reason(
        self,
        config: ReasoningConfig,
        context: list[dict[str, Any]],
        trust_level: Literal["untrusted_input", "trusted"],
    ) -> dict[str, Any]:
        """Dispatch reasoning through the wrapped Planner LLMAdapter."""
        # Guard against model/provider mismatches in either direction:
        # 1. Plan carries a Claude model but provider is non-Anthropic.
        # 2. Plan carries a non-Claude model (e.g. "gpt-4") but provider IS
        #    Claude-based (e.g. claude_code CLI).
        # In both cases, fall back to the provider-appropriate default.
        model = config.model
        plan_is_claude = model.startswith(_ANTHROPIC_PREFIXES)
        provider_is_claude = self._default_model.startswith(_ANTHROPIC_PREFIXES)
        if plan_is_claude != provider_is_claude:
            logger.warning(
                "reasoning_model_overridden",
                extra={"requested": model, "using": self._default_model},
            )
            model = self._default_model

        system_prompt = config.system_prompt_ref or ""
        if trust_level == "trusted":
            system_prompt = system_prompt + _SPAWN_INSTRUCTION

        user_prompt = self._build_user_prompt(context)

        text = await self._adapter.generate(
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
        )

        result: dict[str, Any] = {"content": text}
        if trust_level == "trusted":
            result["spawn_requests"] = self._extract_spawn_requests(text)
        return result

    @staticmethod
    def _build_user_prompt(context: list[dict[str, Any]]) -> str:
        """Flatten upstream step context into a single user message."""
        _MAX_RESULT_CHARS = 12_000
        parts: list[str] = []
        for ctx in context:
            result_str = str(ctx.get("result", {}))
            if len(result_str) > _MAX_RESULT_CHARS:
                result_str = result_str[:_MAX_RESULT_CHARS] + "... [truncated]"
            parts.append(f"Step {ctx.get('step', '?')} result: {result_str}")
        if not parts:
            return "No upstream context available."
        return "\n\n".join(parts)

    @staticmethod
    def _extract_spawn_requests(text: str) -> list[dict[str, Any]]:
        """Best-effort parse of a spawn_requests array from the model output.

        Accepts direct JSON, markdown-fenced JSON, or a JSON blob embedded in
        prose. Returns an empty list on any parse failure — spawn requests
        are optional and must not crash the reasoning step.
        """
        if not text:
            return []

        stripped = text.strip()
        fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", stripped, re.DOTALL)
        if fence:
            stripped = fence.group(1).strip()

        parsed: Any
        try:
            parsed = json.loads(stripped)
        except (ValueError, json.JSONDecodeError):
            match = re.search(r"\{.*\}", stripped, re.DOTALL)
            if not match:
                return []
            try:
                parsed = json.loads(match.group(0))
            except (ValueError, json.JSONDecodeError):
                return []

        if not isinstance(parsed, dict):
            return []

        requests = parsed.get("spawn_requests")
        if not isinstance(requests, list):
            return []

        return [
            r
            for r in requests
            if isinstance(r, dict)
            and r.get("role")
            and r.get("uses")
            and r.get("call")
        ]


def create_reasoning_adapter() -> LLMClient:
    """Factory: build an LLMClient using the Planner's LLMAdapterFactory.

    Reads LLM_PROVIDER / LLM_API_KEY / LLM_TIMEOUT_S via the Planner factory
    so ExecuteOrchestrator reasoning calls go through the same provider as
    Planner and Intake. REASONING_MODEL (or PLANNER_PRIMARY_MODEL as a
    secondary fallback) is used as the override when a plan carries a
    Claude-only default model name and the active provider is non-Anthropic.
    """
    from components.Planner.adapters.llm_adapter import LLMAdapterFactory

    adapter = LLMAdapterFactory.from_env()
    default_model = (
        os.environ.get("REASONING_MODEL")
        or os.environ.get("PLANNER_PRIMARY_MODEL")
        or _DEFAULT_MODEL
    )
    logger.info(
        "reasoning_adapter_created",
        extra={
            "provider_adapter": type(adapter).__name__,
            "default_model": default_model,
        },
    )
    return GenericReasoningAdapter(adapter=adapter, default_model=default_model)
