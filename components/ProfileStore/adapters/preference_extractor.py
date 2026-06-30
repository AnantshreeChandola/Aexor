"""
Preference Extractor — LLM-based preference extraction from free text.

Uses the shared LLMAdapter to extract structured preferences from
unstructured user input. Follows the same pattern as
``Intake/adapters/intent_parser.py``.

Reference: ProfileStore LLD
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from components.Planner.adapters.llm_adapter import LLMAdapter

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a preference extraction engine. Given free-text from a user "
    "describing their preferences, extract structured preference key-value pairs.\n\n"
    "You will be provided with a list of KNOWN PREFERENCE KEYS and their metadata "
    "(type, description, examples, validation constraints). Only extract preferences "
    "that match known keys.\n\n"
    "IMPORTANT RULES:\n"
    "- Only extract preferences for keys in the KNOWN KEYS list.\n"
    "- Do NOT extract sensitive-category preferences (e.g. passport_number, "
    "emergency_contact) from free text — skip them entirely.\n"
    "- Values must conform to the type and constraints described for each key.\n"
    "- Include a confidence score (0.0-1.0) for each extraction.\n"
    "- Include source_text: the snippet from the input that yielded this preference.\n"
    "- Return ONLY valid JSON with this structure:\n"
    '{"preferences": [{"key": "...", "value": ..., "confidence": 0.9, "source_text": "..."}]}\n'
    "- If no preferences can be extracted, return {\"preferences\": []}.\n"
    "- Return ONLY valid JSON, no explanation or markdown."
)


class PreferenceExtractor:
    """LLM-based preference extractor from free text."""

    def __init__(
        self,
        llm_adapter: LLMAdapter,
        model: str | None = None,
        max_tokens: int = 1024,
    ) -> None:
        self._llm = llm_adapter
        self._model = model or os.environ.get(
            "INTAKE_PARSER_MODEL",
            "claude-sonnet-4-5-20250929",
        )
        self._max_tokens = max_tokens

    async def extract(
        self,
        text: str,
        known_keys_info: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Extract preferences from free text using LLM.

        Args:
            text: Free-text user input describing preferences.
            known_keys_info: List of dicts with key metadata from schema registry.

        Returns:
            List of extracted preference dicts with key, value, confidence, source_text.
        """
        user_prompt = self._build_user_prompt(text, known_keys_info)

        try:
            raw = await self._llm.generate(
                model=self._model,
                system_prompt=SYSTEM_PROMPT,
                user_prompt=user_prompt,
                max_tokens=self._max_tokens,
                temperature=0.0,
            )
            return self._parse_response(raw)
        except Exception as exc:
            logger.error("PreferenceExtractor LLM call failed: %s", exc)
            raise

    @staticmethod
    def _build_user_prompt(text: str, known_keys_info: list[dict[str, Any]]) -> str:
        parts: list[str] = ["KNOWN PREFERENCE KEYS:\n"]

        for info in known_keys_info:
            if info.get("category") == "security":
                continue  # skip sensitive category from extraction prompt
            line = f"- {info['key']} (type: {info.get('type', 'any')})"
            if info.get("description"):
                line += f" — {info['description']}"
            if info.get("examples"):
                line += f" | examples: {info['examples']}"
            if info.get("validation"):
                line += f" | validation: {json.dumps(info['validation'])}"
            parts.append(line)

        parts.append(f"\nUser input:\n{text}")
        return "\n".join(parts)

    @staticmethod
    def _parse_response(raw: str) -> list[dict[str, Any]]:
        """Parse LLM JSON response into preference list."""
        cleaned = raw.strip()
        # Strip markdown fences if present
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0]
            cleaned = cleaned.strip()

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            logger.error("PreferenceExtractor invalid JSON: %s", exc)
            return []

        if not isinstance(data, dict):
            return []

        return data.get("preferences", [])
