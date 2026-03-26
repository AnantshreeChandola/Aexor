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


def canonicalize_plan(plan_data: dict[str, Any]) -> str:
    """Canonical JSON: sorted keys, no extra whitespace."""
    return json.dumps(plan_data, sort_keys=True, separators=(",", ":"))


def compute_plan_hash(plan_data: dict[str, Any]) -> str:
    """SHA-256 hex digest of canonical plan bytes."""
    canonical = canonicalize_plan(plan_data)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
