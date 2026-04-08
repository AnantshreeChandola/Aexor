"""
Template Resolver Adapter

Resolves {{step_N.result.field}} templates from execution context.

Reference: LLD.md Section 6.6
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

from ..domain.models import StepResult

# Canonical: {{step_3.result.recommended_time}}
# Also handles LLM variations:
#   {step_3.result.field}       — single braces
#   {{step3.result.field}}      — missing underscore
#   {step3.field}               — no underscore, no .result.
#   {{step_3.response.field}}   — .response. alias
#   {step_3.field}              — no .result.
_STEP_PATTERN = re.compile(
    r"\{?\{step[_]?(\d+)\.(?:result|response)\.(.+?)\}?\}"
)
# Fallback: {stepN.field} — no .result. segment at all
_STEP_SHORTHAND = re.compile(
    r"\{?\{step[_]?(\d+)\.(.+?)\}?\}"
)

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

        Patterns supported (all resolve identically):
            {{step_N.result.field}}   — canonical
            {step_N.result.field}     — single braces
            {{stepN.result.field}}    — no underscore
            {stepN.field}             — shorthand (no .result.)
            {{step_N.response.field}} — .response. alias

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
        # 1. Try canonical pattern: {{step_N.result.field}}
        full_match = _STEP_PATTERN.fullmatch(text)
        if full_match:
            step_num = int(full_match.group(1))
            field_path = full_match.group(2)
            return self._extract_step_field(step_num, field_path, step_results)

        # 2. Try shorthand: {stepN.field} (no .result.)
        short_match = _STEP_SHORTHAND.fullmatch(text)
        if short_match:
            step_num = int(short_match.group(1))
            field_path = short_match.group(2)
            # Strip leading "result." if LLM duplicated it
            if field_path.startswith("result."):
                field_path = field_path[7:]
            return self._extract_step_field(step_num, field_path, step_results)

        # 3. Preview pattern
        full_preview = _PREVIEW_PATTERN.fullmatch(text)
        if full_preview:
            step_num = int(full_preview.group(1))
            field_path = full_preview.group(2)
            return self._extract_preview_field(step_num, field_path, preview_state)

        # 4. Inline replacement for embedded templates
        result = _STEP_PATTERN.sub(
            lambda m: str(self._extract_step_field(int(m.group(1)), m.group(2), step_results)),
            text,
        )
        result = _STEP_SHORTHAND.sub(
            lambda m: str(self._extract_step_field(
                int(m.group(1)),
                m.group(2)[7:] if m.group(2).startswith("result.") else m.group(2),
                step_results,
            )),
            result,
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

    # Common field name aliases the LLM might use interchangeably
    _FIELD_ALIASES: ClassVar[dict[str, list[str]]] = {
        "resolved_time": ["recommended_time", "start_time", "start_datetime", "time"],
        "recommended_time": ["resolved_time", "start_time", "start_datetime", "time"],
        "start_time": ["recommended_time", "resolved_time", "start_datetime", "time"],
        "start_datetime": ["recommended_time", "resolved_time", "start_time", "time"],
        "end_time": ["end_datetime", "end"],
        "end_datetime": ["end_time", "end"],
        "conflict": ["has_conflict", "conflicts"],
        "has_conflict": ["conflict", "conflicts"],
    }

    def _traverse_path(self, data: Any, path: str, context: str) -> Any:
        parts = path.split(".")
        current = data
        for part in parts:
            if isinstance(current, dict):
                if part in current:
                    current = current[part]
                else:
                    # Try aliases before failing
                    found = False
                    for alias in self._FIELD_ALIASES.get(part, []):
                        if alias in current:
                            current = current[alias]
                            found = True
                            break
                    if not found:
                        raise KeyError(f"Field '{part}' not found in {context} (path: {path})")
            else:
                raise KeyError(f"Field '{part}' not found in {context} (path: {path})")
        return current
