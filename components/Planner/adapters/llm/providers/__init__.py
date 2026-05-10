"""
Concrete LLMAdapter implementations.

Importing this subpackage triggers self-registration of every bundled
provider with :class:`LLMAdapterFactory`. Import order is irrelevant.
"""

from components.Planner.adapters.llm.providers import anthropic  # noqa: F401
from components.Planner.adapters.llm.providers import claude_code  # noqa: F401
from components.Planner.adapters.llm.providers import gemini  # noqa: F401
from components.Planner.adapters.llm.providers import ollama  # noqa: F401
from components.Planner.adapters.llm.providers import openai  # noqa: F401
