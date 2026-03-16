"""
Signer Test Fixtures

Shared fixtures for all Signer component tests. Generates real
Ed25519 key pairs for cryptographic test isolation.
"""

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)

from components.Signer.domain.models import PlanSignature
from components.Signer.service.signer_service import SignerService


@pytest.fixture(scope="session")
def test_key_pair() -> (
    tuple[Ed25519PrivateKey, "Ed25519PrivateKey"]
):
    """Generate a fresh Ed25519 key pair for tests.

    Returns:
        Tuple of (private_key, public_key).
    """
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    return private_key, public_key


@pytest.fixture(scope="session")
def signer_service(
    test_key_pair: tuple,
) -> SignerService:
    """Create a SignerService from test key pair.

    Args:
        test_key_pair: Generated Ed25519 key pair.

    Returns:
        Configured SignerService for testing.
    """
    private_key, public_key = test_key_pair
    return SignerService(
        private_key=private_key,
        public_key=public_key,
        pubkey_id="k1",
    )


@pytest.fixture()
def sample_plan() -> dict:
    """Return a realistic plan dict matching GLOBAL_SPEC Section 2.3.

    Returns:
        Plan dict with plan_id, intent, graph, constraints, meta.
    """
    return {
        "plan_id": "plan_01HXYZ",
        "intent": {
            "action": "book_flight",
            "parameters": {
                "origin": "SFO",
                "destination": "JFK",
                "date": "2026-04-01",
            },
        },
        "graph": [
            {
                "step": 1,
                "action": "search_flights",
                "args": {
                    "origin": "SFO",
                    "destination": "JFK",
                },
            },
            {
                "step": 2,
                "action": "select_flight",
                "args": {"flight_id": "UA123"},
                "depends_on": [1],
            },
        ],
        "constraints": {
            "max_price": 500,
            "currency": "USD",
        },
        "meta": {
            "version": "2.2",
            "created_by": "planner@system",
        },
    }


@pytest.fixture()
def sample_plan_minimal() -> dict:
    """Return a minimal valid plan dict.

    Returns:
        Minimal dict with a single field.
    """
    return {"step": 1}


@pytest.fixture()
async def signed_plan(
    signer_service: SignerService,
    sample_plan: dict,
) -> tuple[dict, PlanSignature]:
    """Sign the sample plan and return plan + signature.

    Args:
        signer_service: Test SignerService instance.
        sample_plan: Sample plan dict.

    Returns:
        Tuple of (plan_data, PlanSignature).
    """
    signature = await signer_service.sign_plan(sample_plan)
    return sample_plan, signature
