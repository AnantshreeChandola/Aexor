"""
Google Gemini API adapter using the ``google-genai`` SDK.

Reference: LLD SS5.3, SS6.1
"""

from __future__ import annotations

from components.Planner.adapters.llm.factory import LLMAdapterFactory
from components.Planner.adapters.llm.protocol import LLMConfig
from components.Planner.domain.models import LLMCallError


class GeminiAdapter:
    """Google Gemini adapter implementing LLMAdapter protocol.

    Uses the unified ``google-genai`` SDK (not the legacy
    ``google-generativeai`` package). Rate-limit errors are mapped to
    ``LLMCallError(model, "Rate limited")`` so the CircuitBreaker sees a
    uniform signal across providers.
    """

    def __init__(self, config: LLMConfig) -> None:
        if not config.api_key:
            raise ValueError("LLM_API_KEY must be set for provider=gemini")
        from google import genai
        from google.genai import types as genai_types

        self._timeout_s = config.timeout_s
        # Disable SDK-internal retries. By default google-genai retries 429
        # and 5xx up to 5 total attempts with exponential backoff, which
        # multiplies quota burn on rate-limited free tiers (a single user
        # turn can silently consume 5x the visible RPD). attempts=1 means
        # one attempt total with no retries; the CircuitBreaker one layer
        # up already handles retry/backoff policy for this app.
        self._client = genai.Client(
            api_key=config.api_key,
            http_options=genai_types.HttpOptions(
                retry_options=genai_types.HttpRetryOptions(attempts=1),
            ),
        )

    async def generate(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> str:
        """Call Gemini ``generate_content`` and return text content.

        ``gemini-2.5-*`` models are "thinking" models: by default they burn a
        large portion of ``max_output_tokens`` on hidden reasoning tokens
        before emitting visible output, which truncates JSON responses at
        low token budgets. This adapter disables thinking via
        ``thinking_config.thinking_budget=0`` because every caller in this
        codebase asks for structured JSON output and the Planner/Intake
        prompts already request visible reasoning in JSON fields.
        """
        from google.genai import errors as genai_errors
        from google.genai import types as genai_types

        try:
            response = await self._client.aio.models.generate_content(
                model=model,
                contents=user_prompt,
                config=genai_types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=temperature,
                    max_output_tokens=max_tokens,
                    thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
                ),
            )
            text = response.text
            if not text:
                raise LLMCallError(model, "No text content in response")
            return text
        except TimeoutError as e:
            raise LLMCallError(model, f"Timeout after {self._timeout_s}s") from e
        except genai_errors.APIError as e:
            # Rate-limit errors surface as ClientError with 429. Map to the
            # same "Rate limited" reason the other adapters emit so the
            # CircuitBreaker can treat them uniformly.
            status = getattr(e, "code", None) or getattr(e, "status_code", None)
            if status == 429:
                raise LLMCallError(model, "Rate limited") from e
            raise LLMCallError(model, f"API error: {status or e}") from e
        except LLMCallError:
            raise
        except Exception as e:
            raise LLMCallError(model, str(e)) from e


LLMAdapterFactory.register("gemini", GeminiAdapter)
