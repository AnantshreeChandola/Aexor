"""
Provider-agnostic LLM adapter package.

Exposes the :class:`LLMAdapter` Protocol, :class:`LLMConfig` dataclass, and
:class:`LLMAdapterFactory`. Importing this package triggers registration of
every bundled provider (anthropic, openai, gemini, claude_code) via the
``providers`` subpackage.
"""

# Side-effect import: loading the providers subpackage triggers every
# concrete adapter's self-registration call.
from components.Planner.adapters.llm import providers
from components.Planner.adapters.llm.factory import LLMAdapterFactory
from components.Planner.adapters.llm.protocol import LLMAdapter, LLMConfig

__all__ = ["LLMAdapter", "LLMAdapterFactory", "LLMConfig"]
