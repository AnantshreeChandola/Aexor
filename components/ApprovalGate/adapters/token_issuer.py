"""
Token Issuer Adapter

JWT signing and verification using python-jose (HS256).
Wraps jose.jwt.encode()/decode() for codebase consistency.

Reference: LLD.md Section 6.1
"""

from __future__ import annotations

from typing import Any

from jose import ExpiredSignatureError, JWTError, jwt

from ..domain.models import TokenExpiredError, TokenValidationError


class TokenIssuer:
    """JWT token signing and verification."""

    def __init__(self, secret: str, algorithm: str = "HS256") -> None:
        self._secret = secret
        self._algorithm = algorithm

    def sign(self, claims: dict[str, Any]) -> str:
        """Sign claims into a JWT string.

        Claims must include: plan_id, user_id, gate_id, scopes, exp, iat, token_id.
        Returns JWT string (eyJ... format).
        """
        return jwt.encode(claims, self._secret, algorithm=self._algorithm)

    def verify(self, token: str) -> dict[str, Any]:
        """Verify and decode a JWT token.

        Returns decoded claims dict.

        Raises:
            TokenExpiredError: If token exp is in the past.
            TokenValidationError: If signature is invalid.
        """
        try:
            return jwt.decode(token, self._secret, algorithms=[self._algorithm])
        except ExpiredSignatureError:
            raise TokenExpiredError("Token has expired")
        except JWTError:
            raise TokenValidationError("invalid_signature")
