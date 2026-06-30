"""
LLMAdapter Protocol and LLMConfig dataclass.

Reference: LLD SS5.3, SS6.1
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


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


@dataclass(frozen=True)
class LLMConfig:
    """Uniform construction config for concrete LLM adapters.

    ``api_key`` is ``None`` only for the ``claude_code`` provider, which
    authenticates via the host's Claude Code OAuth subscription.
    """

    provider: str
    api_key: str | None
    timeout_s: int
