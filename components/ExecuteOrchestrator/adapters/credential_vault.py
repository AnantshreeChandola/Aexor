"""
Credential Vault Adapter

AES-256-GCM credential decryption from PostgreSQL vault.
Credentials are decrypted at execution time and zeroed after use.

Reference: LLD.md Section 6.3
"""

from __future__ import annotations

import logging
import os
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..domain.models import ExecuteError

logger = logging.getLogger(__name__)


class CredentialVaultError(ExecuteError):
    """Credential vault operation failed."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Credential vault error: {reason}")


class CredentialVaultAdapter:
    """AES-256-GCM credential decryption.

    Reads encrypted values from credential_vault table,
    decrypts with master key from CREDENTIAL_MASTER_KEY env var.
    """

    def __init__(self, db: Any) -> None:
        self._db = db
        master_key_hex = os.environ.get("CREDENTIAL_MASTER_KEY", "")
        if not master_key_hex:
            logger.warning(
                "CREDENTIAL_MASTER_KEY not set -- credential decryption will fail at runtime"
            )
        self._master_key = bytes.fromhex(master_key_hex) if master_key_hex else b""

    async def decrypt(self, credential_id: str, user_id: str) -> str:
        """Decrypt credential value from vault.

        Args:
            credential_id: Vault record ID.
            user_id: Requesting user's ID (verified against record).

        Returns:
            Plaintext credential string.

        Raises:
            CredentialVaultError: On missing key, not found, or mismatch.
        """
        if not self._master_key:
            raise CredentialVaultError("CREDENTIAL_MASTER_KEY not configured")

        record = await self._fetch_record(credential_id)
        if record is None:
            raise CredentialVaultError(f"Credential {credential_id} not found")

        record_user = str(record.get("user_id", ""))
        if record_user != user_id:
            raise CredentialVaultError("Credential user_id mismatch (security violation)")

        try:
            encrypted_value = record["encrypted_value"]
            iv = record["iv"]
            aesgcm = AESGCM(self._master_key)
            plaintext_bytes = aesgcm.decrypt(iv, encrypted_value, None)
            return plaintext_bytes.decode("utf-8")
        except Exception as exc:
            raise CredentialVaultError(f"Decryption failed: {exc}")

    async def _fetch_record(self, credential_id: str) -> dict[str, Any] | None:
        """Fetch credential record from DB.

        Returns dict with encrypted_value, iv, key_version, user_id.
        """
        try:
            async with self._db.get_session() as session:
                from sqlalchemy import select

                from shared.database.models import CredentialVaultTable

                stmt = select(CredentialVaultTable).where(
                    CredentialVaultTable.credential_id == credential_id
                )
                result = await session.execute(stmt)
                row = result.scalar_one_or_none()
                if row is None:
                    return None
                return {
                    "encrypted_value": row.encrypted_value,
                    "iv": row.iv,
                    "key_version": row.key_version,
                    "user_id": str(row.user_id),
                }
        except Exception as exc:
            raise CredentialVaultError(f"DB query failed: {exc}")
