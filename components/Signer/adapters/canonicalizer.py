"""
Canonicalizer Adapter

Deterministic JSON canonicalization and SHA-256 hashing for plan data.
Produces identical output for identical input dicts, enabling
reproducible plan hashes for signature verification.
"""

import hashlib
import json
from typing import Any


def canonicalize_plan(plan_data: dict[str, Any]) -> str:
    """Canonicalize plan JSON for deterministic hashing.

    Sorted keys, no whitespace, consistent formatting.

    Args:
        plan_data: Plan dictionary to canonicalize.

    Returns:
        Canonical JSON string.
    """
    return json.dumps(plan_data, sort_keys=True, separators=(",", ":"))


def compute_plan_hash(plan_data: dict[str, Any]) -> str:
    """Compute SHA-256 hash of canonical plan bytes.

    Args:
        plan_data: Plan dictionary to hash.

    Returns:
        SHA-256 hex digest (64 characters, lowercase).
    """
    canonical = canonicalize_plan(plan_data)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
