"""
Unit tests for FallbackLLMAdapter.

Validates local-first / remote-fallback behaviour, model override,
and ``last_provider`` tracking.
"""

from __future__ import annotations

import pytest

from components.Intake.adapters.fallback_llm import FallbackLLMAdapter


class _FakeLLM:
    """Minimal LLMAdapter stub for testing."""

    def __init__(self, response: str | None = None, error: Exception | None = None):
        self._response = response
        self._error = error
        self.calls: list[dict] = []

    async def generate(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> str:
        self.calls.append({"model": model, "system_prompt": system_prompt, "user_prompt": user_prompt})
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response


class TestFallbackLLMAdapter:
    @pytest.mark.asyncio
    async def test_local_success(self):
        """When local succeeds, remote is never called."""
        local = _FakeLLM(response="local result")
        remote = _FakeLLM(response="remote result")
        adapter = FallbackLLMAdapter(local=local, remote=remote, local_model="llama3.2:3b")

        result = await adapter.generate(
            model="claude-sonnet-4-5-20250929",
            system_prompt="sys",
            user_prompt="hello",
        )

        assert result == "local result"
        assert adapter.last_provider == "local"
        assert len(local.calls) == 1
        assert len(remote.calls) == 0

    @pytest.mark.asyncio
    async def test_local_failure_falls_back(self):
        """When local raises, remote is called and succeeds."""
        local = _FakeLLM(error=ConnectionError("Ollama down"))
        remote = _FakeLLM(response="remote result")
        adapter = FallbackLLMAdapter(local=local, remote=remote, local_model="llama3.2:3b")

        result = await adapter.generate(
            model="claude-sonnet-4-5-20250929",
            system_prompt="sys",
            user_prompt="hello",
        )

        assert result == "remote result"
        assert adapter.last_provider == "remote"
        assert len(local.calls) == 1
        assert len(remote.calls) == 1

    @pytest.mark.asyncio
    async def test_both_fail(self):
        """When both adapters fail, the remote exception propagates."""
        local = _FakeLLM(error=ConnectionError("Ollama down"))
        remote = _FakeLLM(error=RuntimeError("API error"))
        adapter = FallbackLLMAdapter(local=local, remote=remote, local_model="llama3.2:3b")

        with pytest.raises(RuntimeError, match="API error"):
            await adapter.generate(
                model="claude-sonnet-4-5-20250929",
                system_prompt="sys",
                user_prompt="hello",
            )

        assert adapter.last_provider == "remote"

    @pytest.mark.asyncio
    async def test_local_model_override(self):
        """Local adapter receives the local model name, not the remote one."""
        local = _FakeLLM(response="ok")
        remote = _FakeLLM(response="ok")
        adapter = FallbackLLMAdapter(local=local, remote=remote, local_model="llama3.2:3b")

        await adapter.generate(
            model="claude-sonnet-4-5-20250929",
            system_prompt="sys",
            user_prompt="hello",
        )

        assert local.calls[0]["model"] == "llama3.2:3b"

    @pytest.mark.asyncio
    async def test_remote_gets_original_model(self):
        """On fallback, remote adapter receives the original model name."""
        local = _FakeLLM(error=ConnectionError("down"))
        remote = _FakeLLM(response="ok")
        adapter = FallbackLLMAdapter(local=local, remote=remote, local_model="llama3.2:3b")

        await adapter.generate(
            model="claude-sonnet-4-5-20250929",
            system_prompt="sys",
            user_prompt="hello",
        )

        assert remote.calls[0]["model"] == "claude-sonnet-4-5-20250929"

    @pytest.mark.asyncio
    async def test_last_provider_tracking(self):
        """last_provider updates correctly across multiple calls."""
        local = _FakeLLM(response="ok")
        remote = _FakeLLM(response="ok")
        adapter = FallbackLLMAdapter(local=local, remote=remote, local_model="llama3.2:3b")

        # Default before any call
        assert adapter.last_provider == "remote"

        # First call — local succeeds
        await adapter.generate(model="m", system_prompt="s", user_prompt="u")
        assert adapter.last_provider == "local"

        # Now make local fail for next call
        local._error = RuntimeError("crash")
        await adapter.generate(model="m", system_prompt="s", user_prompt="u")
        assert adapter.last_provider == "remote"
