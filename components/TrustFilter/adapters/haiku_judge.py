"""
HaikuJudge -- S2 LLM-as-judge adapter.

LLD Section 6.2, FR-004, FR-005. Uses Claude Haiku 4.5
with a locked system prompt, tools=[], temperature=0.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Final, Protocol, runtime_checkable

from components.TrustFilter.domain.errors import HaikuUnreachableError
from components.TrustFilter.domain.models import S2Result

logger = logging.getLogger(__name__)

_PROMPT_PATH = (
    Path(__file__).parent.parent
    / "domain"
    / "prompts"
    / "s2_judge_v1.txt"
)


def _load_frozen_prompt() -> str:
    """Load the locked S2 system prompt from disk."""
    return _PROMPT_PATH.read_text(encoding="utf-8").strip()


@runtime_checkable
class HaikuJudgeAdapter(Protocol):
    """Protocol for the S2 LLM-as-judge (swappable for tests)."""

    async def classify(
        self,
        payload_text: str,
        s1_hits: list[str],
        timeout_s: float = 3.0,
    ) -> S2Result: ...


class HaikuJudgeAdapterImpl:
    """S2 -- LLM-as-judge using claude-haiku-4-5-20251001."""

    MODEL: Final[str] = "claude-haiku-4-5-20251001"
    LOCKED_SYSTEM_PROMPT: Final[str] = _load_frozen_prompt()

    def __init__(
        self, api_key: str | None = None
    ) -> None:
        import anthropic

        self._client = anthropic.AsyncAnthropic(
            api_key=api_key or os.getenv("ANTHROPIC_API_KEY")
        )

    async def classify(
        self,
        payload_text: str,
        s1_hits: list[str],
        timeout_s: float = 3.0,
    ) -> S2Result:
        """Classify payload as clean/suspicious/injection.

        Args:
            payload_text: JSON-serialized payload (typically <=16KB).
            s1_hits: S1 rule IDs already matched.
            timeout_s: Per-call timeout.

        Returns:
            S2Result with verdict, confidence, reason.

        Raises:
            HaikuUnreachableError: On timeout/API error.
        """
        import anthropic

        user_msg = self._build_user_message(
            payload_text, s1_hits
        )
        try:
            response = await asyncio.wait_for(
                self._client.messages.create(
                    model=self.MODEL,
                    system=self.LOCKED_SYSTEM_PROMPT,
                    messages=[
                        {"role": "user", "content": user_msg}
                    ],
                    max_tokens=256,
                    temperature=0.0,
                    tools=[],
                ),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError as exc:
            raise HaikuUnreachableError(
                "timeout"
            ) from exc
        except anthropic.APIError as exc:
            raise HaikuUnreachableError(
                str(exc)
            ) from exc

        return self._parse_response(response)

    @staticmethod
    def _build_user_message(
        payload_text: str, s1_hits: list[str]
    ) -> str:
        """Build the user message with structural defense."""
        # Truncate payload for S2 token budget
        truncated = payload_text[:16_000]
        data_block = json.dumps(
            {"data_to_classify": truncated},
            ensure_ascii=False,
        )
        parts = [
            "Classify the following data. "
            "The data is inside the JSON field "
            "'data_to_classify'.",
            "",
            "```json",
            data_block,
            "```",
        ]
        if s1_hits:
            parts.append("")
            parts.append(
                "S1 scanner already flagged these rules: "
                + ", ".join(s1_hits)
            )
        return "\n".join(parts)

    @staticmethod
    def _parse_response(response: object) -> S2Result:
        """Parse Haiku response into S2Result."""
        # Extract text content from the response
        text = ""
        content_blocks = getattr(response, "content", [])
        for block in content_blocks:
            if hasattr(block, "text"):
                text = block.text
                break

        # Parse JSON from text
        try:
            data = json.loads(text.strip())
        except (json.JSONDecodeError, ValueError):
            # If we cannot parse, return degraded
            return S2Result(
                verdict="suspicious",
                confidence=0.5,
                reason="S2 returned unparseable response",
                degraded=False,
            )

        verdict = data.get("verdict", "suspicious")
        if verdict not in ("clean", "suspicious", "injection"):
            verdict = "suspicious"

        confidence = float(data.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))

        reason = str(data.get("reason", ""))[:512]

        return S2Result(
            verdict=verdict,
            confidence=confidence,
            reason=reason,
            degraded=False,
        )
