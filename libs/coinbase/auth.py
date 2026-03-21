"""HMAC-SHA256 request signing for Coinbase INTX API authentication."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time


class CoinbaseAuth:
    """Generates authentication headers for Coinbase INTX API requests.

    Every request must include:
        CB-ACCESS-KEY: API key
        CB-ACCESS-SIGN: HMAC-SHA256 signature of (timestamp + method + path + body)
        CB-ACCESS-TIMESTAMP: Unix timestamp (string)
        CB-ACCESS-PASSPHRASE: Passphrase set during key creation

    The signature is computed as:
        HMAC-SHA256(base64_decode(secret), timestamp + method + path + body)
    then base64-encoded.
    """

    def __init__(self, api_key: str, api_secret: str, passphrase: str) -> None:
        """Initialize with Coinbase INTX API credentials.

        Args:
            api_key: The API key.
            api_secret: The API secret (base64-encoded by Coinbase).
            passphrase: The passphrase chosen during key creation.
        """
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase

    def sign(
        self,
        method: str,
        path: str,
        body: str = "",
        timestamp: str | None = None,
    ) -> dict[str, str]:
        """Generate authentication headers for a request.

        Args:
            method: HTTP method (GET, POST, DELETE, etc.). Will be uppercased.
            path: Request path including leading slash (e.g., '/api/v1/orders').
            body: Request body as a string. Empty string for GET requests.
            timestamp: Unix timestamp as string. Auto-generated if None.

        Returns:
            Dictionary of authentication headers to merge into the request.
        """
        if timestamp is None:
            timestamp = str(int(time.time()))

        method = method.upper()
        message = timestamp + method + path + body
        secret_decoded = base64.b64decode(self.api_secret)
        signature = hmac.new(
            secret_decoded,
            message.encode("utf-8"),
            hashlib.sha256,
        )
        signature_b64 = base64.b64encode(signature.digest()).decode("utf-8")

        return {
            "CB-ACCESS-KEY": self.api_key,
            "CB-ACCESS-SIGN": signature_b64,
            "CB-ACCESS-TIMESTAMP": timestamp,
            "CB-ACCESS-PASSPHRASE": self.passphrase,
        }

    def sign_ws(self, timestamp: str | None = None) -> dict[str, str]:
        """Generate authentication fields for a WebSocket subscribe message.

        The WebSocket prehash differs from REST:
            HMAC-SHA256(secret, timestamp + api_key + "CBINTLMD" + passphrase)

        Args:
            timestamp: Unix timestamp as string. Auto-generated if None.

        Returns:
            Dictionary of auth fields to merge into the subscribe message.
        """
        if timestamp is None:
            timestamp = str(int(time.time()))

        message = timestamp + self.api_key + "CBINTLMD" + self.passphrase
        secret_decoded = base64.b64decode(self.api_secret)
        signature = hmac.new(
            secret_decoded,
            message.encode("utf-8"),
            hashlib.sha256,
        )
        signature_b64 = base64.b64encode(signature.digest()).decode("utf-8")

        return {
            "key": self.api_key,
            "passphrase": self.passphrase,
            "signature": signature_b64,
            "time": timestamp,
        }
