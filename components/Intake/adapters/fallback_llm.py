"""
FallbackLLMAdapter — local-first LLM with remote fallback.

Wraps a local adapter (e.g. Ollama) and a remote adapter (e.g. Claude).
Tries local first; on any exception falls back to remote transparently.
Exposes ``last_provider`` for observability.

Reference: LLM Latency Optimization — Local Llama 3.2 3B
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class FallbackLLMAdapter:
    """Composing adapter: local-first with remote fallback."""

    def __init__(self, local, remote, local_model: str) -> None:
        self._local = local
        self._remote = remote
        self._local_model = local_model
        self.last_provider: str = "remote"

    async def generate(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> str:
        try:
            result = await self._local.generate(
                model=self._local_model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            self.last_provider = "local"
            logger.info(
                "fallback_llm_local_success",
                extra={"local_model": self._local_model},
            )
            return result
        except Exception:
            logger.info(
                "fallback_llm_local_failed_using_remote",
                extra={"local_model": self._local_model, "remote_model": model},
                exc_info=True,
            )
            self.last_provider = "remote"
            return await self._remote.generate(
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=max_tokens,
                temperature=temperature,
            )
