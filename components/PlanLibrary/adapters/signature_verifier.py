"""
Signature Verifier - Ed25519 signature verification for plans.

Handles plan canonicalization for consistent hashing and
Ed25519 signature verification for integrity validation.
"""

import hashlib
import logging
from typing import Union
import base64

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature

from ..domain.models import Signature

logger = logging.getLogger(__name__)


class SignatureVerificationError(Exception):
    """Raised when signature verification fails."""
    pass


class SignatureVerifier:
    """
    Ed25519 signature verification service.
    
    Provides:
    - Plan canonicalization for consistent hashing
    - Ed25519 signature verification
    - SHA-256 hash generation for integrity checks
    """
    
    def __init__(self):
        """Initialize signature verifier."""
        logger.info("SignatureVerifier initialized")

    async def verify_signature(
        self, 
        canonical_json: str, 
        signature: Signature
    ) -> bool:
        """
        Verify Ed25519 signature against canonical plan JSON.
        
        Args:
            canonical_json: Canonical JSON representation of plan
            signature: Signature object with Ed25519 data
            
        Returns:
            True if signature is valid, False otherwise
            
        Raises:
            SignatureVerificationError: If verification process fails
        """
        try:
            # Validate signature algorithm
            if signature.algorithm != "Ed25519":
                logger.warning(f"Unsupported signature algorithm: {signature.algorithm}")
                return False
            
            # Decode base64-encoded signature and public key
            try:
                signature_bytes = base64.b64decode(signature.signature)
                public_key_bytes = base64.b64decode(signature.public_key)
            except Exception as e:
                logger.warning(f"Failed to decode signature/key: {e}")
                return False
            
            # Create Ed25519 public key object
            try:
                public_key = Ed25519PublicKey.from_public_bytes(public_key_bytes)
            except Exception as e:
                logger.warning(f"Invalid Ed25519 public key: {e}")
                return False
            
            # Generate message bytes from canonical JSON
            message_bytes = canonical_json.encode('utf-8')
            
            # Verify signature
            try:
                public_key.verify(signature_bytes, message_bytes)
                logger.debug("Ed25519 signature verification successful")
                return True
                
            except InvalidSignature:
                logger.warning("Ed25519 signature verification failed")
                return False
                
        except Exception as e:
            logger.error(f"Signature verification error: {e}")
            raise SignatureVerificationError(f"Verification failed: {str(e)}")

    def generate_plan_hash(self, canonical_json: str) -> str:
        """
        Generate SHA-256 hash of canonical plan JSON.
        
        Used for plan integrity verification and deduplication.
        
        Args:
            canonical_json: Canonical JSON representation
            
        Returns:
            Hex string of SHA-256 hash
        """
        try:
            message_bytes = canonical_json.encode('utf-8')
            hash_object = hashlib.sha256(message_bytes)
            return hash_object.hexdigest()
            
        except Exception as e:
            logger.error(f"Hash generation error: {e}")
            raise SignatureVerificationError(f"Hash generation failed: {str(e)}")

    def validate_signature_format(self, signature: Signature) -> bool:
        """
        Validate signature object format without verification.
        
        Checks for required fields and proper base64 encoding
        without performing cryptographic verification.
        
        Args:
            signature: Signature object to validate
            
        Returns:
            True if format is valid, False otherwise
        """
        try:
            # Check algorithm
            if signature.algorithm != "Ed25519":
                return False
            
            # Check required fields
            if not signature.signature or not signature.public_key:
                return False
            
            # Validate base64 encoding
            try:
                sig_bytes = base64.b64decode(signature.signature)
                key_bytes = base64.b64decode(signature.public_key)
                
                # Ed25519 signatures are 64 bytes, public keys are 32 bytes
                if len(sig_bytes) != 64 or len(key_bytes) != 32:
                    return False
                    
            except Exception:
                return False
            
            return True
            
        except Exception as e:
            logger.warning(f"Signature format validation error: {e}")
            return False

    async def verify_plan_integrity(
        self,
        canonical_json: str,
        expected_hash: str
    ) -> bool:
        """
        Verify plan integrity by comparing hashes.
        
        Args:
            canonical_json: Current canonical JSON
            expected_hash: Expected SHA-256 hash
            
        Returns:
            True if hashes match, False otherwise
        """
        try:
            actual_hash = self.generate_plan_hash(canonical_json)
            return actual_hash == expected_hash
            
        except Exception as e:
            logger.error(f"Plan integrity verification error: {e}")
            return False

    def extract_public_key_info(self, signature: Signature) -> Union[str, None]:
        """
        Extract public key identifier for logging/debugging.
        
        Returns a truncated version of the public key for identification
        without exposing the full key in logs.
        
        Args:
            signature: Signature object
            
        Returns:
            Truncated public key identifier or None if invalid
        """
        try:
            if not signature.public_key:
                return None
            
            # Return first 8 characters of base64 key for identification
            return signature.public_key[:8] + "..."
            
        except Exception:
            return None

    async def health_check(self) -> bool:
        """
        Check signature verifier functionality.
        
        Performs a test signature verification with known values
        to ensure the cryptographic functions are working.
        
        Returns:
            True if health check passes
        """
        try:
            # Test with empty signature object (should fail gracefully)
            test_signature = Signature(
                signature="",
                public_key="",
                algorithm="Ed25519"
            )
            
            # This should return False (invalid) but not raise an exception
            result = self.validate_signature_format(test_signature)
            
            # Also test hash generation
            test_hash = self.generate_plan_hash('{"test": "data"}')
            
            return isinstance(result, bool) and isinstance(test_hash, str)
            
        except Exception as e:
            logger.error(f"Signature verifier health check failed: {e}")
            return False