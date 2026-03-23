"""ES256 JWT authentication for Coinbase Advanced Trade API."""

from __future__ import annotations

import secrets
import time

import jwt
from cryptography.hazmat.primitives import serialization


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
        # Normalize PEM: env vars may use literal \n instead of real newlines
        normalized_secret = api_secret.replace("\\n", "\n")
        self._private_key = serialization.load_pem_private_key(
            normalized_secret.encode("utf-8"), password=None,
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
