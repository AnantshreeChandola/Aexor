"""
Claude Code headless CLI adapter for the Planner's LLM calls.

This adapter shells out to the ``claude -p`` headless CLI from the
``@anthropic-ai/claude-code`` npm package, which authenticates via the host's
Claude Code OAuth credentials (``~/.claude/.credentials.json``) rather than an
API key. It lets development environments reuse an existing Claude Max
subscription for Planner/Intake calls instead of paying per-token API billing.

Selected via ``LLM_PROVIDER=claude_code``; ``LLM_API_KEY`` is ignored for this
provider.

Reference: LLD SS5.3, SS6.1 (LLMAdapter protocol).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os

from components.Planner.adapters.llm.factory import LLMAdapterFactory
from components.Planner.adapters.llm.protocol import LLMConfig
from components.Planner.domain.models import LLMCallError

logger = logging.getLogger(__name__)

_DEFAULT_BINARY = "claude"
_DEFAULT_TIMEOUT_S = 120


class ClaudeCodeAdapter:
    """LLMAdapter implementation that invokes the ``claude -p`` headless CLI.

    The adapter spawns ``claude -p`` as a subprocess, streams the user prompt
    over stdin, and parses the ``--output-format json`` response. The
    ``ANTHROPIC_API_KEY`` environment variable is explicitly removed from the
    subprocess environment so the CLI authenticates via the host's OAuth
    subscription rather than direct API billing.

    Environment variables:
        CLAUDE_CODE_BIN: Path to the ``claude`` binary (default: ``claude``).
        PLANNER_CLAUDE_CODE_TIMEOUT_S: Per-call timeout in seconds
            (default: 120). Subscriptions are slower than direct API calls so
            the default is deliberately higher than ``LLM_TIMEOUT_S``.
    """

    def __init__(self, config: LLMConfig) -> None:
        self._binary = os.environ.get("CLAUDE_CODE_BIN", _DEFAULT_BINARY)
        raw = os.environ.get("PLANNER_CLAUDE_CODE_TIMEOUT_S")
        self._timeout_s = int(raw) if raw else _DEFAULT_TIMEOUT_S
        # When CLAUDE_CODE_USE_API_KEY=true, pass the API key through to the
        # CLI (for Docker / headless environments where OAuth isn't available).
        # Otherwise strip it so the CLI uses the host's OAuth subscription.
        self._use_api_key = os.environ.get(
            "CLAUDE_CODE_USE_API_KEY", ""
        ).lower() in ("true", "1", "yes")
        self._api_key = config.api_key if self._use_api_key else None

    async def generate(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,  # noqa: ARG002 - CLI does not accept this flag
        temperature: float = 0.0,  # noqa: ARG002 - CLI does not accept this flag
    ) -> str:
        """Invoke ``claude -p`` and return the assistant text.

        ``max_tokens`` and ``temperature`` are accepted for protocol
        compatibility but ignored — the Claude Code CLI does not expose these
        knobs to headless callers.
        """
        env = os.environ.copy()
        if self._api_key:
            # Docker / headless mode: pass API key to the CLI
            env["ANTHROPIC_API_KEY"] = self._api_key
        else:
            # Host mode: force OAuth auth path so the CLI uses the
            # subscription instead of per-token API billing.
            env.pop("ANTHROPIC_API_KEY", None)

        # --system-prompt fully replaces Claude Code's built-in system prompt.
        # Using --append-system-prompt caused the built-in coding-assistant
        # persona to conflict with Planner/Intake JSON-only instructions,
        # resulting in the model refusing non-coding intents or wrapping
        # responses in explanatory text.
        cmd = [
            self._binary,
            "-p",
            "--output-format",
            "json",
            "--model",
            model,
            "--system-prompt",
            system_prompt,
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError as exc:
            raise LLMCallError(
                model, f"Claude Code CLI not found: {self._binary}"
            ) from exc
        except Exception as exc:
            raise LLMCallError(model, f"Failed to spawn claude -p: {exc}") from exc

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=user_prompt.encode("utf-8")),
                timeout=self._timeout_s,
            )
        except TimeoutError as exc:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            # Drain so the subprocess doesn't linger as a zombie.
            with contextlib.suppress(Exception):
                await proc.wait()
            raise LLMCallError(
                model, f"Timeout after {self._timeout_s}s"
            ) from exc
        except Exception as exc:
            raise LLMCallError(model, f"claude -p communicate failed: {exc}") from exc

        if proc.returncode != 0:
            err_text = stderr_bytes.decode("utf-8", errors="replace").strip()
            snippet = err_text[:500] or "<no stderr>"
            raise LLMCallError(
                model, f"claude -p exited {proc.returncode}: {snippet}"
            )

        stdout_text = stdout_bytes.decode("utf-8", errors="replace")
        try:
            payload = json.loads(stdout_text)
        except json.JSONDecodeError as exc:
            snippet = stdout_text[:200].replace("\n", " ")
            raise LLMCallError(
                model, f"Invalid JSON from claude -p: {exc}: {snippet!r}"
            ) from exc

        if not isinstance(payload, dict):
            raise LLMCallError(
                model, f"Unexpected claude -p payload type: {type(payload).__name__}"
            )

        if payload.get("is_error") or payload.get("subtype") == "error":
            err_msg = payload.get("result") or payload.get("error") or "unknown error"
            raise LLMCallError(model, f"claude -p error: {err_msg}")

        result = payload.get("result")
        if not isinstance(result, str) or not result:
            raise LLMCallError(model, "No text result in claude -p response")

        return result


LLMAdapterFactory.register("claude_code", ClaudeCodeAdapter)
