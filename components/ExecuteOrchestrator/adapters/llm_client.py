"""
LLM Client Adapter

Protocol and Anthropic implementation for reasoning steps
with two-tier trust enforcement.

Reference: LLD.md Section 6.2
"""

from __future__ import annotations

import logging
import os
from typing import Any, Literal, Protocol, runtime_checkable

from shared.schemas.policy import ReasoningConfig

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = os.environ.get("REASONING_MODEL", "claude-haiku-4-5-20251001")
_ANTHROPIC_PREFIXES = ("claude-",)


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

        if trust_level == "untrusted_input":
            # Tier 1: no tools, strict output schema
            kwargs["tools"] = []
        elif trust_level == "trusted":
            # Tier 2: enable spawn tool
            kwargs["tools"] = self._build_spawn_tools()

        response = await self._client.messages.create(**kwargs)
        return self._parse_response(response, trust_level)

    def _build_messages(self, context: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Build message list from step context."""
        messages: list[dict[str, Any]] = []
        for ctx in context:
            messages.append(
                {
                    "role": "user",
                    "content": f"Step {ctx.get('step', '?')} result: {ctx.get('result', {})}",
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
