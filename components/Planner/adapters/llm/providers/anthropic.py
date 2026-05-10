"""
Anthropic Claude API adapter.

Reference: LLD SS5.3, SS6.1
"""

from __future__ import annotations

import anthropic

from components.Planner.adapters.llm.factory import LLMAdapterFactory
from components.Planner.adapters.llm.protocol import LLMConfig
from components.Planner.domain.models import LLMCallError


class AnthropicAdapter:
    """Anthropic Claude API adapter implementing LLMAdapter protocol."""

    def __init__(self, config: LLMConfig) -> None:
        if not config.api_key:
            raise ValueError("LLM_API_KEY must be set for provider=anthropic")
        self._timeout_s = config.timeout_s
        self._client = anthropic.AsyncAnthropic(
            api_key=config.api_key,
            timeout=config.timeout_s,
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
                system=[
                    {
                        "type": "text",
                        "text": system_prompt,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": user_prompt}],
            )
            # Extract text from first content block
            for block in response.content:
                if block.type == "text":
                    text = block.text
                    if not text or not text.strip():
                        raise LLMCallError(model, "Empty text content in response")
                    return text
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


LLMAdapterFactory.register("anthropic", AnthropicAdapter)
