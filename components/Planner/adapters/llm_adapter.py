"""Back-compat shim.

New code should import from ``components.Planner.adapters.llm``. This module
re-exports the public Protocol, factory, and config dataclass so existing
imports in Intake and tests continue to resolve.

Reference: LLD SS5.3, SS6.1
"""

from components.Planner.adapters.llm import (
    LLMAdapter,
    LLMAdapterFactory,
    LLMConfig,
)

__all__ = ["LLMAdapter", "LLMAdapterFactory", "LLMConfig"]
