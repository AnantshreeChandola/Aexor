"""
Canonical JSON serialization and SHA-256 hash computation.

Intentionally duplicates Signer's canonicalizer to avoid circular dependency.
Planner computes hash before Signer re-verifies.

Reference: LLD SS6.5
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

# Runtime fields excluded from canonical hashing (populated at execution time).
_STEP_RUNTIME_FIELDS = frozenset({"status", "result", "error"})
_TOP_RUNTIME_FIELDS = frozenset({"policy_attestations"})


def _strip_runtime_fields(plan_data: dict[str, Any]) -> dict[str, Any]:
    """Strip runtime-mutable fields before canonicalization."""
    cleaned = {k: v for k, v in plan_data.items() if k not in _TOP_RUNTIME_FIELDS}
    if "graph" in cleaned and isinstance(cleaned["graph"], list):
        cleaned["graph"] = [
            {k: v for k, v in step.items() if k not in _STEP_RUNTIME_FIELDS}
            for step in cleaned["graph"]
        ]
    return cleaned


def canonicalize_plan(plan_data: dict[str, Any]) -> str:
    """Canonical JSON: sorted keys, no extra whitespace, runtime fields stripped."""
    cleaned = _strip_runtime_fields(plan_data)
    return json.dumps(cleaned, sort_keys=True, separators=(",", ":"))


def compute_plan_hash(plan_data: dict[str, Any]) -> str:
    """SHA-256 hex digest of canonical plan bytes."""
    canonical = canonicalize_plan(plan_data)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
