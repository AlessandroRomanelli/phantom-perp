"""Tests for Coinbase INTX HMAC-SHA256 request signing."""

import base64
import hashlib
import hmac

import pytest

from libs.coinbase.auth import CoinbaseAuth


@pytest.fixture
def auth() -> CoinbaseAuth:
    """Create a CoinbaseAuth with known test credentials."""
    # API secret must be base64-encoded (as Coinbase provides it)
    raw_secret = b"test-secret-key-1234567890"
    b64_secret = base64.b64encode(raw_secret).decode()
    return CoinbaseAuth(
        api_key="test-api-key",
        api_secret=b64_secret,
        passphrase="test-passphrase",
    )


class TestCoinbaseAuth:
    def test_sign_returns_all_required_headers(self, auth: CoinbaseAuth) -> None:
        headers = auth.sign("GET", "/api/v1/orders", "", "1700000000")
        assert "CB-ACCESS-KEY" in headers
        assert "CB-ACCESS-SIGN" in headers
        assert "CB-ACCESS-TIMESTAMP" in headers
        assert "CB-ACCESS-PASSPHRASE" in headers

    def test_sign_api_key_matches(self, auth: CoinbaseAuth) -> None:
        headers = auth.sign("GET", "/api/v1/orders", "", "1700000000")
        assert headers["CB-ACCESS-KEY"] == "test-api-key"

    def test_sign_passphrase_matches(self, auth: CoinbaseAuth) -> None:
        headers = auth.sign("GET", "/api/v1/orders", "", "1700000000")
        assert headers["CB-ACCESS-PASSPHRASE"] == "test-passphrase"

    def test_sign_timestamp_matches_provided(self, auth: CoinbaseAuth) -> None:
        headers = auth.sign("GET", "/api/v1/orders", "", "1700000000")
        assert headers["CB-ACCESS-TIMESTAMP"] == "1700000000"

    def test_sign_auto_generates_timestamp(self, auth: CoinbaseAuth) -> None:
        headers = auth.sign("GET", "/api/v1/orders")
        ts = headers["CB-ACCESS-TIMESTAMP"]
        assert ts.isdigit()
        assert int(ts) > 0

    def test_sign_signature_is_valid_hmac(self, auth: CoinbaseAuth) -> None:
        timestamp = "1700000000"
        method = "GET"
        path = "/api/v1/orders"
        body = ""

        headers = auth.sign(method, path, body, timestamp)

        # Manually compute expected signature
        message = timestamp + method + path + body
        secret_decoded = base64.b64decode(auth.api_secret)
        expected_sig = hmac.new(
            secret_decoded,
            message.encode("utf-8"),
            hashlib.sha256,
        )
        expected_b64 = base64.b64encode(expected_sig.digest()).decode("utf-8")

        assert headers["CB-ACCESS-SIGN"] == expected_b64

    def test_sign_post_with_body(self, auth: CoinbaseAuth) -> None:
        timestamp = "1700000000"
        method = "POST"
        path = "/api/v1/orders"
        body = '{"instrument_id":"ETH-PERP","side":"BUY","size":"0.1"}'

        headers = auth.sign(method, path, body, timestamp)

        # Verify the body is included in the signature
        message = timestamp + method + path + body
        secret_decoded = base64.b64decode(auth.api_secret)
        expected_sig = hmac.new(
            secret_decoded,
            message.encode("utf-8"),
            hashlib.sha256,
        )
        expected_b64 = base64.b64encode(expected_sig.digest()).decode("utf-8")

        assert headers["CB-ACCESS-SIGN"] == expected_b64

    def test_sign_different_bodies_produce_different_signatures(
        self, auth: CoinbaseAuth
    ) -> None:
        ts = "1700000000"
        h1 = auth.sign("POST", "/api/v1/orders", '{"side":"BUY"}', ts)
        h2 = auth.sign("POST", "/api/v1/orders", '{"side":"SELL"}', ts)
        assert h1["CB-ACCESS-SIGN"] != h2["CB-ACCESS-SIGN"]

    def test_sign_different_methods_produce_different_signatures(
        self, auth: CoinbaseAuth
    ) -> None:
        ts = "1700000000"
        h1 = auth.sign("GET", "/api/v1/orders", "", ts)
        h2 = auth.sign("DELETE", "/api/v1/orders", "", ts)
        assert h1["CB-ACCESS-SIGN"] != h2["CB-ACCESS-SIGN"]

    def test_sign_different_paths_produce_different_signatures(
        self, auth: CoinbaseAuth
    ) -> None:
        ts = "1700000000"
        h1 = auth.sign("GET", "/api/v1/orders", "", ts)
        h2 = auth.sign("GET", "/api/v1/positions", "", ts)
        assert h1["CB-ACCESS-SIGN"] != h2["CB-ACCESS-SIGN"]

    def test_sign_uppercases_method(self, auth: CoinbaseAuth) -> None:
        ts = "1700000000"
        h1 = auth.sign("get", "/api/v1/orders", "", ts)
        h2 = auth.sign("GET", "/api/v1/orders", "", ts)
        assert h1["CB-ACCESS-SIGN"] == h2["CB-ACCESS-SIGN"]
