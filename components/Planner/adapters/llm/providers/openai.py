"""
OpenAI ChatCompletion adapter.

Reference: LLD SS5.3, SS6.1
"""

from __future__ import annotations

from components.Planner.adapters.llm.factory import LLMAdapterFactory
from components.Planner.adapters.llm.protocol import LLMConfig
from components.Planner.domain.models import LLMCallError


class OpenAIAdapter:
    """OpenAI ChatCompletion adapter implementing LLMAdapter protocol."""

    def __init__(self, config: LLMConfig) -> None:
        if not config.api_key:
            raise ValueError("LLM_API_KEY must be set for provider=openai")
        import openai

        self._timeout_s = config.timeout_s
        self._client = openai.AsyncOpenAI(api_key=config.api_key, timeout=config.timeout_s)

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


LLMAdapterFactory.register("openai", OpenAIAdapter)
