"""
Integration Tests for Signer

End-to-end sign-then-verify roundtrips, audit verification,
replay protection, and cross-plan independence.
"""

import json

import pytest

from components.Signer.domain.models import InvalidSignatureError
from components.Signer.service.signer_service import SignerService


class TestSignThenVerify:
    """Integration tests for full sign/verify lifecycle."""

    async def test_sign_then_verify_roundtrip(
        self,
        signer_service: SignerService,
        sample_plan: dict,
    ) -> None:
        """Sign a plan then verify with same service."""
        sig = await signer_service.sign_plan(sample_plan)
        result = await signer_service.verify_signature(sample_plan, sig.model_dump())
        assert result is True

    async def test_sign_then_verify_with_complex_plan(
        self,
        signer_service: SignerService,
    ) -> None:
        """Realistic multi-step plan with nested structures."""
        complex_plan = {
            "plan_id": "plan_complex_01",
            "intent": {
                "action": "multi_step_booking",
                "parameters": {
                    "destinations": ["SFO", "LAX", "JFK"],
                    "dates": [
                        "2026-04-01",
                        "2026-04-05",
                    ],
                },
            },
            "graph": [
                {
                    "step": 1,
                    "action": "search",
                    "args": {"query": "flights"},
                },
                {
                    "step": 2,
                    "action": "filter",
                    "args": {
                        "criteria": {
                            "max_price": 1000,
                            "airline": ["UA", "AA"],
                        }
                    },
                    "depends_on": [1],
                },
                {
                    "step": 3,
                    "action": "book",
                    "args": {"confirm": True},
                    "depends_on": [2],
                },
            ],
            "constraints": {
                "max_total": 3000,
                "currency": "USD",
                "flexible_dates": True,
            },
            "meta": {"version": "2.2"},
        }

        sig = await signer_service.sign_plan(complex_plan)
        result = await signer_service.verify_signature(complex_plan, sig.model_dump())
        assert result is True

    async def test_audit_verification_scenario(
        self,
        signer_service: SignerService,
        sample_plan: dict,
    ) -> None:
        """Simulate audit: sign, serialize, deserialize, verify."""
        sig = await signer_service.sign_plan(sample_plan)

        # Simulate DB storage: serialize to JSON strings
        plan_json = json.dumps(sample_plan)
        sig_json = json.dumps(sig.model_dump())

        # Simulate retrieval: deserialize from JSON
        restored_plan = json.loads(plan_json)
        restored_sig = json.loads(sig_json)

        result = await signer_service.verify_signature(restored_plan, restored_sig)
        assert result is True

    async def test_audit_detects_post_storage_tampering(
        self,
        signer_service: SignerService,
        sample_plan: dict,
    ) -> None:
        """Tampering after serialization is detected."""
        sig = await signer_service.sign_plan(sample_plan)

        plan_json = json.dumps(sample_plan)
        sig_json = json.dumps(sig.model_dump())

        # Tamper with the stored plan
        restored_plan = json.loads(plan_json)
        restored_plan["plan_id"] = "TAMPERED_IN_DB"
        restored_sig = json.loads(sig_json)

        with pytest.raises(InvalidSignatureError) as exc_info:
            await signer_service.verify_signature(restored_plan, restored_sig)
        assert exc_info.value.reason == "hash_mismatch"

    async def test_multiple_plans_independent_sigs(
        self,
        signer_service: SignerService,
    ) -> None:
        """Two plans have independent signatures; cross-verify fails."""
        plan_a = {"plan": "alpha", "step": 1}
        plan_b = {"plan": "beta", "step": 2}

        sig_a = await signer_service.sign_plan(plan_a)
        sig_b = await signer_service.sign_plan(plan_b)

        # Self-verify succeeds
        assert await signer_service.verify_signature(plan_a, sig_a.model_dump())
        assert await signer_service.verify_signature(plan_b, sig_b.model_dump())

        # Cross-verify fails
        with pytest.raises(InvalidSignatureError):
            await signer_service.verify_signature(plan_a, sig_b.model_dump())
        with pytest.raises(InvalidSignatureError):
            await signer_service.verify_signature(plan_b, sig_a.model_dump())

    async def test_sign_then_verify_minimal_plan(
        self,
        signer_service: SignerService,
        sample_plan_minimal: dict,
    ) -> None:
        """Minimal plan can be signed and verified."""
        sig = await signer_service.sign_plan(sample_plan_minimal)
        result = await signer_service.verify_signature(sample_plan_minimal, sig.model_dump())
        assert result is True

    async def test_verify_with_different_service_same_keys(
        self,
        test_key_pair: tuple,
        sample_plan: dict,
    ) -> None:
        """Two service instances with same keys can cross-verify."""
        priv, pub = test_key_pair
        svc1 = SignerService(priv, pub, pubkey_id="k1")
        svc2 = SignerService(priv, pub, pubkey_id="k1")

        sig = await svc1.sign_plan(sample_plan)
        result = await svc2.verify_signature(sample_plan, sig.model_dump())
        assert result is True
