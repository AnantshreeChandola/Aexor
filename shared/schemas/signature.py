"""
Signature Schema - GLOBAL_SPEC §2.4 Implementation

Pydantic model for cryptographic plan signatures.
Used by Signer, PlanWriter, and ExecuteOrchestrator.

Reference: GLOBAL_SPEC.md §2.4, signature.schema.json
"""

from typing import Literal

from pydantic import BaseModel, Field


class Signature(BaseModel):
    """
    Cryptographic plan signature contract (GLOBAL_SPEC §2.4).

    Fields:
        algo: Signature algorithm (Ed25519)
        signer: Signer identity (e.g., "planner@system")
        signature: Base64-encoded signature (min 64 chars)
        pubkey_id: Public key identifier (e.g., "k1")
        plan_hash: SHA-256 hash of canonical plan (64-char hex)
    """

    algo: Literal["Ed25519"] = Field(..., description="Signature algorithm")

    signer: str = Field(..., description="Signer identity (e.g., 'planner@system')")

    signature: str = Field(..., min_length=64, description="Base64-encoded signature")

    pubkey_id: str = Field(..., description="Public key identifier (e.g., 'k1')")

    plan_hash: str = Field(
        ...,
        min_length=64,
        max_length=64,
        description="SHA-256 hash of canonical plan (64-char hex)",
    )
