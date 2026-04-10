"""
JsonTreeWalker -- LLD Section 6.3, FR-006, FR-007.

Recursive JSON traversal that yields (dotted_path, string_value)
pairs for scanning, plus a shape-preserving strip applicator.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Iterator
from typing import Any

from components.TrustFilter.domain.errors import (
    PayloadDepthExceededError,
)

MAX_PAYLOAD_BYTES: int = 1_048_576  # 1 MB
MAX_DEPTH: int = 32

ALWAYS_SCAN_FIELD_NAMES: frozenset[str] = frozenset({
    "description",
    "notes",
    "body",
    "comment",
    "memo",
    "content",
    "text",
})

_STRUCTURED_SUFFIXES: frozenset[str] = frozenset({
    "_id", "_at", "_url", "_uri",
})

_STRUCTURED_EXACT: frozenset[str] = frozenset({
    "id", "email", "url", "uri", "uuid",
    "timezone", "timestamp", "tz", "href",
})

_ISO_DATE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?(\.\d+)?"
    r"(Z|[+-]\d{2}:?\d{2})?)?$"
)
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_EMAIL_RE = re.compile(
    r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"
)
_URL_RE = re.compile(
    r"^https?://[^\s]+$"
)

REDACTED_MARKER = "[redacted: injection]"


class JsonTreeWalker:
    """Walk arbitrary JSON trees yielding string leaf values."""

    def walk(
        self,
        payload: Any,
        *,
        depth: int = 0,
        path: str = "",
    ) -> Iterator[tuple[str, str]]:
        """Yield (dotted_path, string_value) for every scannable leaf.

        Args:
            payload: JSON-like object (dict, list, str, etc.)
            depth: Current recursion depth.
            path: Current dotted path prefix.

        Yields:
            Tuples of (field_path, string_value).

        Raises:
            PayloadDepthExceededError: If depth exceeds MAX_DEPTH.
        """
        if depth > MAX_DEPTH:
            raise PayloadDepthExceededError(depth)

        if isinstance(payload, dict):
            for key, value in payload.items():
                child_path = f"{path}.{key}" if path else key
                yield from self._walk_field(
                    key, value, child_path, depth
                )
        elif isinstance(payload, list):
            for idx, item in enumerate(payload):
                child_path = f"{path}[{idx}]" if path else f"[{idx}]"
                yield from self.walk(
                    item, depth=depth + 1, path=child_path
                )
        elif isinstance(payload, str):
            if path and self._should_scan(path, payload):
                yield (path, payload)

    def _walk_field(
        self,
        key: str,
        value: Any,
        path: str,
        depth: int,
    ) -> Iterator[tuple[str, str]]:
        """Walk a single dict field."""
        if isinstance(value, str):
            if self._should_scan(key, value):
                yield (path, value)
        elif isinstance(value, (dict, list)):
            yield from self.walk(
                value, depth=depth + 1, path=path
            )
        # Non-string scalars (int, float, bool, None) are skipped

    def _should_scan(self, key_or_path: str, value: str) -> bool:
        """Decide whether a string field should be scanned.

        Fields in ALWAYS_SCAN_FIELD_NAMES are always scanned.
        Structured fields (IDs, dates, emails, URLs) are skipped.
        """
        # Extract the leaf field name from a dotted path
        field_name = self._leaf_name(key_or_path)

        # Always scan certain field names regardless
        if field_name.lower() in ALWAYS_SCAN_FIELD_NAMES:
            return True

        # Skip structured fields by name
        if self._is_structured_name(field_name):
            return False

        # Skip structured values by shape
        if self._is_structured_value(value):
            return False

        return True

    @staticmethod
    def _leaf_name(path: str) -> str:
        """Extract the leaf field name from a dotted path."""
        # Handle array index like "events[0].description"
        parts = path.replace("]", "").split(".")
        last = parts[-1] if parts else path
        # Strip array index prefix
        if "[" in last:
            last = last.split("[")[0]
        return last

    @staticmethod
    def _is_structured_name(name: str) -> bool:
        """Check if field name suggests structured data."""
        lower = name.lower()
        if lower in _STRUCTURED_EXACT:
            return True
        for suffix in _STRUCTURED_SUFFIXES:
            if lower.endswith(suffix):
                return True
        return False

    @staticmethod
    def _is_structured_value(value: str) -> bool:
        """Check if value shape indicates structured data."""
        stripped = value.strip()
        if not stripped or len(stripped) > 256:
            return False

        # Pure numeric
        try:
            float(stripped)
            return True
        except ValueError:
            pass

        # ISO date/datetime
        if _ISO_DATE_RE.match(stripped):
            return True

        # UUID
        if _UUID_RE.match(stripped):
            return True

        # Email
        if _EMAIL_RE.match(stripped):
            return True

        # URL (short ones)
        if len(stripped) < 200 and _URL_RE.match(stripped):
            return True

        return False

    def apply_strips(
        self,
        payload: Any,
        stripped_paths: set[str],
    ) -> Any:
        """Return a deep copy with stripped paths replaced.

        Args:
            payload: The original JSON-like payload.
            stripped_paths: Set of dotted paths to redact.

        Returns:
            Copy of payload with stripped fields set to
            REDACTED_MARKER.
        """
        if not stripped_paths:
            return copy.deepcopy(payload)
        result = copy.deepcopy(payload)
        for path in sorted(stripped_paths):
            self._set_at_path(result, path, REDACTED_MARKER)
        return result

    @staticmethod
    def _set_at_path(obj: Any, path: str, value: Any) -> None:
        """Set a value at a dotted/bracketed path."""
        tokens = _tokenize_path(path)
        current = obj
        for i, token in enumerate(tokens[:-1]):
            current = _navigate(current, token)
            if current is None:
                return
        last = tokens[-1]
        if isinstance(last, int) and isinstance(current, list):
            if 0 <= last < len(current):
                current[last] = value
        elif isinstance(last, str) and isinstance(current, dict):
            if last in current:
                current[last] = value


def _tokenize_path(path: str) -> list[str | int]:
    """Split 'a.b[0].c' into ['a', 'b', 0, 'c']."""
    tokens: list[str | int] = []
    for part in path.replace("]", "").split("."):
        if not part:
            continue
        if "[" in part:
            field, idx_str = part.split("[", 1)
            if field:
                tokens.append(field)
            tokens.append(int(idx_str))
        else:
            tokens.append(part)
    return tokens


def _navigate(obj: Any, token: str | int) -> Any:
    """Navigate one level into an object."""
    if isinstance(token, int) and isinstance(obj, list):
        if 0 <= token < len(obj):
            return obj[token]
        return None
    if isinstance(token, str) and isinstance(obj, dict):
        return obj.get(token)
    return None
