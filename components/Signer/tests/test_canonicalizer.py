"""
Unit Tests for Canonicalizer Adapter

Tests deterministic JSON canonicalization and SHA-256 hashing.
"""

import re

from components.Signer.adapters.canonicalizer import (
    canonicalize_plan,
    compute_plan_hash,
)


class TestCanonicalizePlan:
    """Tests for canonicalize_plan()."""

    def test_canonicalize_sorted_keys(self) -> None:
        """Keys are sorted regardless of input order."""
        result = canonicalize_plan({"b": 1, "a": 2})
        assert result == '{"a":2,"b":1}'

    def test_canonicalize_no_whitespace(self) -> None:
        """Output contains no extraneous spaces."""
        result = canonicalize_plan({"key": "value", "num": 42})
        assert " " not in result
        assert "\n" not in result
        assert "\t" not in result

    def test_canonicalize_deterministic(self) -> None:
        """Same dict gives same string every time."""
        data = {"x": 10, "y": [1, 2, 3], "z": {"nested": True}}
        results = [canonicalize_plan(data) for _ in range(100)]
        assert len(set(results)) == 1

    def test_canonicalize_nested_objects(self) -> None:
        """Nested dicts also have sorted keys."""
        data = {"outer": {"b_inner": 2, "a_inner": 1}}
        result = canonicalize_plan(data)
        assert result == '{"outer":{"a_inner":1,"b_inner":2}}'

    def test_canonicalize_handles_lists(self) -> None:
        """List element order is preserved (not sorted)."""
        data = {"items": [3, 1, 2]}
        result = canonicalize_plan(data)
        assert result == '{"items":[3,1,2]}'

    def test_canonicalize_handles_special_chars(self) -> None:
        """Unicode and special characters are handled."""
        data = {"name": "hello world", "emoji": "test"}
        result = canonicalize_plan(data)
        assert '"name":"hello world"' in result
        assert '"emoji":"test"' in result

    def test_canonicalize_empty_nested(self) -> None:
        """Empty nested structures are preserved."""
        data = {"a": {}, "b": []}
        result = canonicalize_plan(data)
        assert result == '{"a":{},"b":[]}'


class TestComputePlanHash:
    """Tests for compute_plan_hash()."""

    def test_compute_hash_returns_64_hex_chars(self) -> None:
        """Hash is 64-character lowercase hex string."""
        result = compute_plan_hash({"key": "value"})
        assert len(result) == 64
        assert re.match(r"^[a-f0-9]{64}$", result)

    def test_compute_hash_deterministic(self) -> None:
        """Same dict gives same hash every time."""
        data = {"a": 1, "b": 2}
        hashes = [compute_plan_hash(data) for _ in range(100)]
        assert len(set(hashes)) == 1

    def test_compute_hash_different_inputs(self) -> None:
        """Different dicts produce different hashes."""
        hash1 = compute_plan_hash({"a": 1})
        hash2 = compute_plan_hash({"a": 2})
        assert hash1 != hash2

    def test_compute_hash_key_order_independent(self) -> None:
        """Different key orderings produce the same hash."""
        hash1 = compute_plan_hash({"b": 1, "a": 2})
        hash2 = compute_plan_hash({"a": 2, "b": 1})
        assert hash1 == hash2


class TestRuntimeFieldStripping:
    """Tests for runtime field exclusion from canonical hashing."""

    def test_status_result_error_stripped_from_steps(self) -> None:
        """Step runtime fields (status, result, error) are excluded."""
        plan_clean = {
            "graph": [{"step": 1, "uses": "test.tool", "call": "op"}],
        }
        plan_with_runtime = {
            "graph": [
                {
                    "step": 1,
                    "uses": "test.tool",
                    "call": "op",
                    "status": "completed",
                    "result": {"data": "value"},
                    "error": None,
                }
            ],
        }
        assert canonicalize_plan(plan_clean) == canonicalize_plan(plan_with_runtime)

    def test_policy_attestations_stripped_from_top_level(self) -> None:
        """Top-level policy_attestations field is excluded."""
        plan_clean = {"plan_id": "test"}
        plan_with_attestations = {
            "plan_id": "test",
            "policy_attestations": [{"id": "att-1"}],
        }
        assert canonicalize_plan(plan_clean) == canonicalize_plan(plan_with_attestations)

    def test_hash_stable_with_runtime_fields(self) -> None:
        """Hash is identical whether runtime fields are present or not."""
        base = {"graph": [{"step": 1, "role": "Fetcher"}], "meta": {"v": 1}}
        with_runtime = {
            "graph": [
                {
                    "step": 1,
                    "role": "Fetcher",
                    "status": "failed",
                    "result": None,
                    "error": {"msg": "timeout"},
                }
            ],
            "meta": {"v": 1},
            "policy_attestations": [{"att": 1}],
        }
        assert compute_plan_hash(base) == compute_plan_hash(with_runtime)

    def test_non_runtime_step_fields_preserved(self) -> None:
        """Non-runtime step fields like type, policy_ref are preserved in canonical."""
        plan = {
            "graph": [
                {
                    "step": 1,
                    "type": "llm_reasoning",
                    "policy_ref": "policy-1",
                    "can_spawn": True,
                }
            ],
        }
        canonical = canonicalize_plan(plan)
        assert '"type":"llm_reasoning"' in canonical
        assert '"policy_ref":"policy-1"' in canonical
        assert '"can_spawn":true' in canonical

    def test_ts_and_nonce_stripped_from_top_level(self) -> None:
        """Signing metadata (ts, nonce) is excluded from canonical hashing."""
        plan_clean = {"plan_id": "test", "graph": [{"step": 1}]}
        plan_with_signing_meta = {
            "plan_id": "test",
            "graph": [{"step": 1}],
            "ts": "2026-04-01T00:00:00+00:00",
            "nonce": "01JBXYZ1234567890ABCDEFGHI",
            "signature": "b" * 88,
        }
        assert canonicalize_plan(plan_clean) == canonicalize_plan(plan_with_signing_meta)

    def test_hash_stable_with_signing_metadata(self) -> None:
        """Hash is identical whether signing metadata is present or not."""
        base = {"plan_id": "test", "graph": [{"step": 1, "role": "Fetcher"}]}
        with_meta = {
            "plan_id": "test",
            "graph": [{"step": 1, "role": "Fetcher"}],
            "ts": "2026-04-01T12:00:00+00:00",
            "nonce": "01JBXYZ1234567890ABCDEFGHI",
        }
        assert compute_plan_hash(base) == compute_plan_hash(with_meta)
