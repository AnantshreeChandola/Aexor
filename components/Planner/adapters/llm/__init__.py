"""
Provider-agnostic LLM adapter package.

Exposes the :class:`LLMAdapter` Protocol, :class:`LLMConfig` dataclass, and
:class:`LLMAdapterFactory`. Importing this package triggers registration of
every bundled provider (anthropic, openai, gemini, claude_code) via the
``providers`` subpackage.
"""

from components.Planner.adapters.llm.factory import LLMAdapterFactory
from components.Planner.adapters.llm.protocol import LLMAdapter, LLMConfig

# Side-effect import: loading the providers subpackage triggers every
# concrete adapter's self-registration call.
from components.Planner.adapters.llm import providers  # noqa: F401,E402

__all__ = ["LLMAdapter", "LLMAdapterFactory", "LLMConfig"]
