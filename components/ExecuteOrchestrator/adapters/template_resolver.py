"""
Template Resolver Adapter

Resolves {{step_N.result.field}} templates from execution context.

Reference: LLD.md Section 6.6
"""

from __future__ import annotations

import re
from typing import Any

from ..domain.models import StepResult

_STEP_PATTERN = re.compile(r"\{\{step_(\d+)\.(?:result|response)\.(.+?)\}\}")
_PREVIEW_PATTERN = re.compile(r"\{\{preview\.cached_state\.step_(\d+)_result\.(.+?)\}\}")


class TemplateResolver:
    """Resolve template references in step args."""

    def resolve(
        self,
        args: dict[str, Any],
        step_results: dict[int, StepResult],
        preview_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Recursively resolve template references in step args.

        Patterns:
            {{step_N.result.field}} - from step N's result
            {{preview.cached_state.step_N_result.field}} - from preview

        Args:
            args: Step arguments that may contain templates.
            step_results: Completed step results indexed by step number.
            preview_state: Cached preview results indexed by step number.

        Returns:
            Resolved args dict with templates replaced.

        Raises:
            KeyError: On missing step or field reference.
        """
        return self._resolve_value(args, step_results, preview_state)

    def _resolve_value(
        self,
        value: Any,
        step_results: dict[int, StepResult],
        preview_state: dict[str, Any] | None,
    ) -> Any:
        if isinstance(value, str):
            return self._resolve_string(value, step_results, preview_state)
        if isinstance(value, dict):
            return {
                k: self._resolve_value(v, step_results, preview_state) for k, v in value.items()
            }
        if isinstance(value, list):
            return [self._resolve_value(v, step_results, preview_state) for v in value]
        return value

    def _resolve_string(
        self,
        text: str,
        step_results: dict[int, StepResult],
        preview_state: dict[str, Any] | None,
    ) -> Any:
        # Check full-string step template match (return raw value)
        full_match = _STEP_PATTERN.fullmatch(text)
        if full_match:
            step_num = int(full_match.group(1))
            field_path = full_match.group(2)
            return self._extract_step_field(step_num, field_path, step_results)

        # Check full-string preview template match
        full_preview = _PREVIEW_PATTERN.fullmatch(text)
        if full_preview:
            step_num = int(full_preview.group(1))
            field_path = full_preview.group(2)
            return self._extract_preview_field(step_num, field_path, preview_state)

        # Inline replacement for embedded templates
        result = _STEP_PATTERN.sub(
            lambda m: str(self._extract_step_field(int(m.group(1)), m.group(2), step_results)),
            text,
        )
        result = _PREVIEW_PATTERN.sub(
            lambda m: str(self._extract_preview_field(int(m.group(1)), m.group(2), preview_state)),
            result,
        )
        return result

    def _extract_step_field(
        self,
        step_num: int,
        field_path: str,
        step_results: dict[int, StepResult],
    ) -> Any:
        if step_num not in step_results:
            raise KeyError(f"Step {step_num} not found in results")
        result = step_results[step_num].result
        if result is None:
            raise KeyError(f"Step {step_num} has no result")
        return self._traverse_path(result, field_path, f"step_{step_num}")

    def _extract_preview_field(
        self,
        step_num: int,
        field_path: str,
        preview_state: dict[str, Any] | None,
    ) -> Any:
        if preview_state is None:
            raise KeyError("No preview state available")
        key = str(step_num)
        if key not in preview_state:
            raise KeyError(f"Step {step_num} not in preview state")
        data = preview_state[key]
        return self._traverse_path(data, field_path, f"preview_step_{step_num}")

    def _traverse_path(self, data: Any, path: str, context: str) -> Any:
        parts = path.split(".")
        current = data
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                raise KeyError(f"Field '{part}' not found in {context} (path: {path})")
        return current
