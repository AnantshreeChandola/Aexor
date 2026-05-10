"""
Ollama adapter — local LLM via Ollama's OpenAI-compatible endpoint.

Uses httpx (already a project dependency) to call
``{OLLAMA_BASE_URL}/v1/chat/completions``. No API key required.

Reference: LLM Latency Optimization — Local Llama 3.2 3B
"""

from __future__ import annotations

import logging
import os

from components.Planner.adapters.llm.factory import LLMAdapterFactory
from components.Planner.adapters.llm.protocol import LLMConfig
from components.Planner.domain.models import LLMCallError

logger = logging.getLogger(__name__)


class OllamaAdapter:
    """Ollama local-LLM adapter implementing LLMAdapter protocol."""

    def __init__(self, config: LLMConfig) -> None:
        import httpx

        base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        self._base_url = base_url.rstrip("/")
        self._timeout_s = config.timeout_s
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(config.timeout_s, connect=10),
        )

    async def generate(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> str:
        """Call Ollama's OpenAI-compatible chat completions endpoint."""
        import httpx

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }

        try:
            response = await self._client.post(
                "/v1/chat/completions",
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            if not content:
                raise LLMCallError(model, "No content in response")
            return content
        except httpx.ConnectError as e:
            raise LLMCallError(model, f"Ollama not reachable at {self._base_url}") from e
        except httpx.TimeoutException as e:
            raise LLMCallError(model, f"Timeout after {self._timeout_s}s") from e
        except httpx.HTTPStatusError as e:
            raise LLMCallError(model, f"HTTP {e.response.status_code}") from e
        except LLMCallError:
            raise
        except Exception as e:
            raise LLMCallError(model, str(e)) from e


LLMAdapterFactory.register("ollama", OllamaAdapter)
