"""
Canonicalizer Adapter

Deterministic JSON canonicalization and SHA-256 hashing for plan data.
Produces identical output for identical input dicts, enabling
reproducible plan hashes for signature verification.
"""

import hashlib
import json
from typing import Any

# Runtime fields that may change during execution and must be excluded
# from canonical hashing to ensure deterministic signatures.
_STEP_RUNTIME_FIELDS = frozenset({"status", "result", "error"})

# Signature-level runtime fields and signing metadata
_TOP_RUNTIME_FIELDS = frozenset({"policy_attestations", "signature", "ts", "nonce"})


def _strip_runtime_fields(plan_data: dict[str, Any]) -> dict[str, Any]:
    """Strip runtime-mutable fields from plan data before canonicalization.

    Removes step-level fields (status, result, error) and top-level
    fields (policy_attestations) that are populated at execution time.
    """
    cleaned = {k: v for k, v in plan_data.items() if k not in _TOP_RUNTIME_FIELDS}

    if "graph" in cleaned and isinstance(cleaned["graph"], list):
        cleaned["graph"] = [
            {k: v for k, v in step.items() if k not in _STEP_RUNTIME_FIELDS}
            for step in cleaned["graph"]
        ]

    return cleaned


def canonicalize_plan(plan_data: dict[str, Any]) -> str:
    """Canonicalize plan JSON for deterministic hashing.

    Strips runtime-mutable fields, then applies sorted keys and
    no-whitespace formatting for reproducible output.

    Args:
        plan_data: Plan dictionary to canonicalize.

    Returns:
        Canonical JSON string.
    """
    cleaned = _strip_runtime_fields(plan_data)
    return json.dumps(cleaned, sort_keys=True, separators=(",", ":"))


def compute_plan_hash(plan_data: dict[str, Any]) -> str:
    """Compute SHA-256 hash of canonical plan bytes.

    Args:
        plan_data: Plan dictionary to hash.

    Returns:
        SHA-256 hex digest (64 characters, lowercase).
    """
    canonical = canonicalize_plan(plan_data)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
