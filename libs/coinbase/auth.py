"""ES256 JWT authentication for Coinbase Advanced Trade API."""

from __future__ import annotations

import secrets
import time
from typing import cast

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePrivateKey
from cryptography.hazmat.primitives.asymmetric.ed448 import Ed448PrivateKey
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

# Matches jwt.AllowedPrivateKeyTypes — the union of key types jwt.encode accepts.
_JwtPrivateKey = RSAPrivateKey | EllipticCurvePrivateKey | Ed25519PrivateKey | Ed448PrivateKey


class CoinbaseAuth:
    """Generates JWT Authorization headers for Coinbase Advanced Trade API.

    Each request gets a fresh JWT (2-minute lifetime) with the specific
    method+path embedded in the uri claim. No caching needed -- JWT
    generation with a pre-loaded EC key takes <1ms.

    Args:
        api_key: Cloud API Key name (UUID format).
        api_secret: PEM-encoded EC private key string (ES256 / P-256 curve).
    """

    BASE_HOST = "api.coinbase.com"

    def __init__(self, api_key: str, api_secret: str) -> None:
        self.api_key = api_key
        self._private_key = self._load_key(api_secret)

    @staticmethod
    def _load_key(raw: str) -> _JwtPrivateKey:
        """Load an EC private key from various formats.

        Coinbase CDP portal may provide the key as:
        - Full PEM with headers (-----BEGIN ... -----)
        - Raw base64 DER (no headers) in SEC1 or PKCS8 format
        """
        normalized = raw.replace("\\\\n", "\n").replace("\\n", "\n").strip()

        # Already has PEM headers — load directly
        if normalized.startswith("-----"):
            return cast(
                _JwtPrivateKey,
                serialization.load_pem_private_key(normalized.encode("utf-8"), password=None),
            )

        # Raw base64 — try wrapping with both PKCS8 and SEC1 headers
        import base64

        # First try loading as raw DER bytes
        try:
            der_bytes = base64.b64decode(normalized)
        except Exception as exc:
            msg = f"API secret is not valid PEM or base64: {exc}"
            raise ValueError(msg) from exc

        # Try PKCS8 DER first (most common from CDP)
        try:
            return cast(
                _JwtPrivateKey,
                serialization.load_der_private_key(der_bytes, password=None),
            )
        except Exception:
            pass

        # Try PEM with PKCS8 headers
        pem_pkcs8 = (
            "-----BEGIN PRIVATE KEY-----\n"
            + normalized
            + "\n-----END PRIVATE KEY-----"
        )
        try:
            return cast(
                _JwtPrivateKey,
                serialization.load_pem_private_key(pem_pkcs8.encode("utf-8"), password=None),
            )
        except Exception:
            pass

        # Try PEM with EC headers (SEC1)
        pem_ec = (
            "-----BEGIN EC PRIVATE KEY-----\n"
            + normalized
            + "\n-----END EC PRIVATE KEY-----"
        )
        return cast(
            _JwtPrivateKey,
            serialization.load_pem_private_key(pem_ec.encode("utf-8"), password=None),
        )

    def sign(self, method: str, path: str, body: str = "") -> dict[str, str]:
        """Generate Authorization header with JWT for a REST request.

        Args:
            method: HTTP method (GET, POST, etc.).
            path: Request path (e.g., '/api/v3/brokerage/orders').
            body: Unused for JWT auth (kept for interface compatibility).

        Returns:
            Headers dict with Authorization bearer token.
        """
        uri = f"{method.upper()} {self.BASE_HOST}{path}"
        now = int(time.time())
        jwt_payload = {
            "sub": self.api_key,
            "iss": "cdp",
            "nbf": now,
            "exp": now + 120,
            "uri": uri,
        }
        token = jwt.encode(
            jwt_payload,
            self._private_key,
            algorithm="ES256",
            headers={"kid": self.api_key, "nonce": secrets.token_hex()},
        )
        return {"Authorization": f"Bearer {token}"}
