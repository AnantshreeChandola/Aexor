"""
Two-Tier LLM Trust Enforcement Tests

Tests that Tier 1 (untrusted_input) disables tools and enforces output schema,
and Tier 2 (trusted) enables tools and parses spawn requests.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from shared.schemas.policy import ReasoningConfig

from ..adapters.llm_client import AnthropicReasoningAdapter, LLMClient

# ---------------------------------------------------------------------------
# Mock Anthropic response helpers
# ---------------------------------------------------------------------------


def _text_block(text: str) -> MagicMock:
    block = MagicMock()
    block.text = text
    block.type = "text"
    return block


def _tool_use_block(name: str, input_data: dict) -> MagicMock:
    block = MagicMock()
    block.type = "tool_use"
    block.name = name
    block.input = input_data
    del block.text  # Make hasattr(block, "text") return False
    return block


def _mock_response(*blocks) -> MagicMock:
    resp = MagicMock()
    resp.content = list(blocks)
    return resp


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestLLMClientProtocol:
    def test_protocol_checkable(self):
        adapter = MagicMock(spec=LLMClient)
        assert isinstance(adapter, LLMClient)


# ---------------------------------------------------------------------------
# Tier 1 (untrusted_input)
# ---------------------------------------------------------------------------


class TestTier1Enforcement:
    @pytest.fixture()
    def adapter(self):
        adapter = AnthropicReasoningAdapter.__new__(AnthropicReasoningAdapter)
        adapter._client = MagicMock()
        adapter._client.messages = MagicMock()
        return adapter

    async def test_tier1_tools_disabled(self, adapter):
        """Tier 1 calls must have tools=[]."""
        mock_resp = _mock_response(_text_block("analysis result"))
        adapter._client.messages.create = AsyncMock(return_value=mock_resp)

        config = ReasoningConfig(system_prompt_ref="test.prompt")
        await adapter.reason(config, [], "untrusted_input")

        call_kwargs = adapter._client.messages.create.call_args.kwargs
        assert call_kwargs["tools"] == []

    async def test_tier1_no_spawn_requests(self, adapter):
        """Tier 1 responses must not include spawn_requests."""
        mock_resp = _mock_response(_text_block("result"))
        adapter._client.messages.create = AsyncMock(return_value=mock_resp)

        config = ReasoningConfig(system_prompt_ref="test.prompt")
        result = await adapter.reason(config, [], "untrusted_input")
        assert "spawn_requests" not in result

    async def test_tier1_text_content_extracted(self, adapter):
        """Tier 1 content is extracted from text blocks."""
        mock_resp = _mock_response(_text_block("line one"))
        adapter._client.messages.create = AsyncMock(return_value=mock_resp)

        config = ReasoningConfig(system_prompt_ref="test.prompt")
        result = await adapter.reason(config, [], "untrusted_input")
        assert result["content"] == "line one"

    async def test_tier1_tool_use_blocks_ignored(self, adapter):
        """Even if tool_use blocks appear in Tier 1, they are not parsed."""
        mock_resp = _mock_response(
            _text_block("text"),
            _tool_use_block("spawn_step", {"role": "Fetcher"}),
        )
        adapter._client.messages.create = AsyncMock(return_value=mock_resp)

        config = ReasoningConfig(system_prompt_ref="test.prompt")
        result = await adapter.reason(config, [], "untrusted_input")
        # spawn_requests key should not exist for tier 1
        assert "spawn_requests" not in result


# ---------------------------------------------------------------------------
# Tier 2 (trusted)
# ---------------------------------------------------------------------------


class TestTier2Enforcement:
    @pytest.fixture()
    def adapter(self):
        adapter = AnthropicReasoningAdapter.__new__(AnthropicReasoningAdapter)
        adapter._client = MagicMock()
        adapter._client.messages = MagicMock()
        return adapter

    async def test_tier2_tools_enabled(self, adapter):
        """Tier 2 calls must have spawn tool defined."""
        mock_resp = _mock_response(_text_block("reasoning"))
        adapter._client.messages.create = AsyncMock(return_value=mock_resp)

        config = ReasoningConfig(system_prompt_ref="test.prompt")
        await adapter.reason(config, [], "trusted")

        call_kwargs = adapter._client.messages.create.call_args.kwargs
        tools = call_kwargs["tools"]
        assert len(tools) == 1
        assert tools[0]["name"] == "spawn_step"

    async def test_tier2_spawn_requests_extracted(self, adapter):
        """Tier 2 spawn requests are extracted from tool_use blocks."""
        mock_resp = _mock_response(
            _text_block("reasoning"),
            _tool_use_block(
                "spawn_step",
                {
                    "role": "Fetcher",
                    "uses": "google.flights",
                    "call": "search",
                    "args": {"from": "LAX"},
                },
            ),
        )
        adapter._client.messages.create = AsyncMock(return_value=mock_resp)

        config = ReasoningConfig(system_prompt_ref="test.prompt")
        result = await adapter.reason(config, [], "trusted")
        assert len(result["spawn_requests"]) == 1
        assert result["spawn_requests"][0]["role"] == "Fetcher"

    async def test_tier2_no_spawn_requests(self, adapter):
        """Tier 2 with no tool_use blocks returns empty spawn list."""
        mock_resp = _mock_response(_text_block("no spawns"))
        adapter._client.messages.create = AsyncMock(return_value=mock_resp)

        config = ReasoningConfig(system_prompt_ref="test.prompt")
        result = await adapter.reason(config, [], "trusted")
        assert result["spawn_requests"] == []

    async def test_tier2_context_passed(self, adapter):
        """Tier 2 context from upstream steps is included in messages."""
        mock_resp = _mock_response(_text_block("ok"))
        adapter._client.messages.create = AsyncMock(return_value=mock_resp)

        config = ReasoningConfig(system_prompt_ref="test.prompt")
        context = [
            {"step": 1, "result": {"data": "value"}},
        ]
        await adapter.reason(config, context, "trusted")

        call_kwargs = adapter._client.messages.create.call_args.kwargs
        messages = call_kwargs["messages"]
        assert len(messages) == 1
        assert "Step 1 result" in messages[0]["content"]
