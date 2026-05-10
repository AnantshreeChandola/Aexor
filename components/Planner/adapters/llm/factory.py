"""
LLMAdapterFactory — provider-agnostic factory for concrete LLM adapters.

Supports runtime registration of new providers via ``register()``, environment
based construction via ``from_env()``, and explicit construction via
``create()``.

Reference: LLD SS5.3, SS6.1
"""

from __future__ import annotations

import logging
import os
from typing import Callable

from components.Planner.adapters.llm.protocol import LLMAdapter, LLMConfig

logger = logging.getLogger(__name__)

ProviderBuilder = Callable[[LLMConfig], LLMAdapter]

_KEYLESS_PROVIDERS = frozenset({"claude_code", "ollama"})

# Heuristic model-name prefixes per provider. Used only for a soft warning when
# the configured model name obviously mismatches the selected provider.
_MODEL_PREFIX_BY_PROVIDER: dict[str, tuple[str, ...]] = {
    "anthropic": ("claude-",),
    "openai": ("gpt-", "o1-", "o3-", "o4-"),
    "gemini": ("gemini-",),
    "ollama": ("llama", "mistral", "phi", "gemma", "qwen"),
}


class LLMAdapterFactory:
    """Registry-based factory for LLMAdapter implementations."""

    _registry: dict[str, ProviderBuilder] = {}

    @classmethod
    def register(cls, name: str, builder: ProviderBuilder) -> None:
        """Register a provider builder under ``name`` (lowercased)."""
        cls._registry[name.strip().lower()] = builder

    @classmethod
    def create(cls, config: LLMConfig) -> LLMAdapter:
        """Build an adapter from an explicit :class:`LLMConfig`.

        Raises:
            ValueError: if ``config.provider`` is not registered.
        """
        key = config.provider.strip().lower()
        try:
            builder = cls._registry[key]
        except KeyError as exc:
            known = sorted(cls._registry)
            raise ValueError(
                f"Unknown LLM provider '{config.provider}'. Known providers: {known}"
            ) from exc
        return builder(config)

    @classmethod
    def from_env(cls) -> LLMAdapter:
        """Read ``LLM_PROVIDER`` / ``LLM_API_KEY`` / ``LLM_TIMEOUT_S`` and build.

        Raises:
            ValueError: if ``LLM_API_KEY`` is missing for a key-requiring
                provider, or if the provider name is not registered.
        """
        provider = os.environ.get("LLM_PROVIDER", "anthropic").strip().lower()
        api_key = os.environ.get("LLM_API_KEY")
        timeout_s = int(os.environ.get("LLM_TIMEOUT_S", "60"))

        if provider not in _KEYLESS_PROVIDERS and not api_key:
            raise ValueError(f"LLM_API_KEY must be set for provider={provider}")

        cls._warn_on_model_mismatch(provider)

        config = LLMConfig(provider=provider, api_key=api_key, timeout_s=timeout_s)
        adapter = cls.create(config)
        logger.info(
            "planner_llm_adapter_selected",
            extra={"component": "planner", "provider": provider},
        )
        return adapter

    @classmethod
    def _warn_on_model_mismatch(cls, provider: str) -> None:
        """Emit a warning if configured model names obviously don't match provider."""
        expected = _MODEL_PREFIX_BY_PROVIDER.get(provider)
        if not expected:
            return
        for env_var in (
            "PLANNER_PRIMARY_MODEL",
            "PLANNER_FALLBACK_MODEL",
            "INTAKE_PARSER_MODEL",
        ):
            name = os.environ.get(env_var)
            if not name:
                continue
            if not name.startswith(expected):
                logger.warning(
                    "llm_model_provider_mismatch",
                    extra={
                        "component": "planner",
                        "env_var": env_var,
                        "model": name,
                        "provider": provider,
                        "expected_prefixes": list(expected),
                    },
                )
