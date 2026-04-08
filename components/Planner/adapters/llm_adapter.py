"""
LLM adapter protocol and implementations (Anthropic Claude, OpenAI).

Reference: LLD SS5.3, SS6.1
"""

from __future__ import annotations

import logging
import os
from typing import Protocol, runtime_checkable

import anthropic

from components.Planner.domain.models import LLMCallError

logger = logging.getLogger(__name__)


@runtime_checkable
class LLMAdapter(Protocol):
    """Protocol for LLM generation calls."""

    async def generate(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> str: ...


class AnthropicAdapter:
    """Anthropic Claude API adapter implementing LLMAdapter protocol."""

    def __init__(self, api_key: str | None = None) -> None:
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise ValueError(
                "ANTHROPIC_API_KEY must be set in environment or passed to AnthropicAdapter"
            )
        self._timeout_s = int(os.environ.get("PLANNER_LLM_TIMEOUT_S", "10"))
        self._client = anthropic.AsyncAnthropic(
            api_key=key,
            timeout=self._timeout_s,
        )

    async def generate(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> str:
        """Call Anthropic Messages API and return text content."""
        try:
            response = await self._client.messages.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            # Extract text from first content block
            for block in response.content:
                if block.type == "text":
                    return block.text
            raise LLMCallError(model, "No text content in response")
        except anthropic.APITimeoutError as e:
            raise LLMCallError(model, f"Timeout after {self._timeout_s}s") from e
        except anthropic.RateLimitError as e:
            raise LLMCallError(model, "Rate limited") from e
        except anthropic.APIStatusError as e:
            raise LLMCallError(model, f"API error: {e.status_code}") from e
        except LLMCallError:
            raise
        except Exception as e:
            raise LLMCallError(model, str(e)) from e


class OpenAIAdapter:
    """OpenAI ChatCompletion adapter implementing LLMAdapter protocol."""

    def __init__(self, api_key: str | None = None) -> None:
        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise ValueError(
                "OPENAI_API_KEY must be set in environment or passed to OpenAIAdapter"
            )
        import openai

        self._timeout_s = int(os.environ.get("PLANNER_OPENAI_TIMEOUT_S", "15"))
        self._client = openai.AsyncOpenAI(api_key=key, timeout=self._timeout_s)

    async def generate(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> str:
        """Call OpenAI ChatCompletion API and return text content."""
        import openai

        try:
            response = await self._client.chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            content = response.choices[0].message.content
            if not content:
                raise LLMCallError(model, "No content in response")
            return content
        except openai.APITimeoutError as e:
            raise LLMCallError(model, f"Timeout after {self._timeout_s}s") from e
        except openai.RateLimitError as e:
            raise LLMCallError(model, "Rate limited") from e
        except openai.APIStatusError as e:
            raise LLMCallError(model, f"API error: {e.status_code}") from e
        except LLMCallError:
            raise
        except Exception as e:
            raise LLMCallError(model, str(e)) from e
