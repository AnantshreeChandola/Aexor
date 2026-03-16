"""
Contract Tests for PlanSignature

Validates that PlanSignature output conforms to
shared/schemas/signature.schema.json using the jsonschema library.
"""

import json
import re
from pathlib import Path

import jsonschema
import pytest

from components.Signer.service.signer_service import SignerService

SCHEMA_PATH = (
    Path(__file__).resolve().parents[3]
    / "shared"
    / "schemas"
    / "signature.schema.json"
)


@pytest.fixture(scope="session")
def signature_schema() -> dict:
    """Load the signature JSON schema.

    Returns:
        Parsed JSON schema dict.
    """
    with SCHEMA_PATH.open() as f:
        return json.load(f)


class TestSignatureContract:
    """Contract tests against signature.schema.json."""

    async def test_signature_output_conforms_to_schema(
        self,
        signer_service: SignerService,
        sample_plan: dict,
        signature_schema: dict,
    ) -> None:
        """Signed plan output validates against JSON schema."""
        sig = await signer_service.sign_plan(sample_plan)
        sig_dict = sig.model_dump()
        jsonschema.validate(
            instance=sig_dict, schema=signature_schema
        )

    async def test_signature_algo_field_matches_enum(
        self,
        signer_service: SignerService,
        sample_plan: dict,
        signature_schema: dict,
    ) -> None:
        """algo is in the schema enum [Ed25519]."""
        sig = await signer_service.sign_plan(sample_plan)
        allowed = signature_schema["properties"]["algo"]["enum"]
        assert sig.algo in allowed

    async def test_signature_nonce_matches_ulid_pattern(
        self,
        signer_service: SignerService,
        sample_plan: dict,
    ) -> None:
        """nonce matches ULID regex ^[0-9A-HJKMNP-TV-Z]{26}$."""
        sig = await signer_service.sign_plan(sample_plan)
        pattern = r"^[0-9A-HJKMNP-TV-Z]{26}$"
        assert re.match(pattern, sig.nonce), (
            f"nonce {sig.nonce!r} does not match ULID pattern"
        )

    async def test_signature_plan_hash_matches_hex_pattern(
        self,
        signer_service: SignerService,
        sample_plan: dict,
    ) -> None:
        """plan_hash matches ^[a-f0-9]{64}$."""
        sig = await signer_service.sign_plan(sample_plan)
        pattern = r"^[a-f0-9]{64}$"
        assert re.match(pattern, sig.plan_hash)

    async def test_signature_pubkey_id_matches_pattern(
        self,
        signer_service: SignerService,
        sample_plan: dict,
    ) -> None:
        """pubkey_id matches ^k[0-9]+$."""
        sig = await signer_service.sign_plan(sample_plan)
        pattern = r"^k[0-9]+$"
        assert re.match(pattern, sig.pubkey_id)

    async def test_signature_signer_matches_pattern(
        self,
        signer_service: SignerService,
        sample_plan: dict,
    ) -> None:
        """signer matches ^[a-zA-Z0-9_-]+@[a-zA-Z0-9_-]+$."""
        sig = await signer_service.sign_plan(sample_plan)
        pattern = r"^[a-zA-Z0-9_-]+@[a-zA-Z0-9_-]+$"
        assert re.match(pattern, sig.signer)

    async def test_signature_no_additional_properties(
        self,
        signer_service: SignerService,
        sample_plan: dict,
        signature_schema: dict,
    ) -> None:
        """model_dump() has no extra fields beyond schema."""
        sig = await signer_service.sign_plan(sample_plan)
        sig_dict = sig.model_dump()
        schema_keys = set(
            signature_schema["properties"].keys()
        )
        dump_keys = set(sig_dict.keys())
        extra = dump_keys - schema_keys
        assert not extra, f"Extra fields found: {extra}"
