"""
Tool Discovery Domain Models

Data classes for the 3-tier tool discovery pipeline: embedding results,
discovery results, and domain-specific errors.

Reference: Tool Discovery Feature Spec — Key Entities
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ToolEmbeddingResult:
    """A search result from the tool_embeddings table (Tier 1B or Tier 3)."""

    tool_name: str
    provider_name: str
    rrf_score: float
    keyword_rank: int | None = None
    semantic_rank: int | None = None


@dataclass
class ToolDiscoveryResult:
    """Output of the full 3-tier tool discovery pipeline."""

    tools: list[Any]  # list[ToolDefinition]
    discovery_tier: int  # 0=fallback, 1=embedding, 2=reranked, 3=agentic
    candidate_count: int = 0
    reranked_count: int = 0
    plan_based_tools: int = 0
    direct_tools: int = 0
    discovery_ms: int = 0


class ToolNotConnectedError(Exception):
    """Raised when plan-based discovery identifies high-confidence tools
    the user hasn't connected.

    Attributes:
        missing_tools: List of dicts with 'tool_name' and 'provider_name'.
        message: Human-readable description.
    """

    def __init__(self, missing_tools: list[dict[str, str]], message: str = "") -> None:
        self.missing_tools = missing_tools
        if not message:
            names = ", ".join(t["tool_name"] for t in missing_tools)
            message = f"Required tools not connected: {names}"
        self.message = message
        super().__init__(message)


class NoToolsConnectedError(Exception):
    """Raised when the user has zero linked tools.

    Prevents the system from silently falling back to the global catalog.

    Attributes:
        user_id: The user who has no connected tools.
        message: Human-readable description.
    """

    def __init__(self, user_id: str, message: str = "") -> None:
        self.user_id = user_id
        if not message:
            message = f"User '{user_id}' has no connected tools"
        self.message = message
        super().__init__(message)
