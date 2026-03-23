"""Tests for ES256 JWT authentication."""

import inspect

import jwt as pyjwt
import pytest

from libs.coinbase.auth import CoinbaseAuth


class TestCoinbaseAuth:
    def test_sign_generates_valid_jwt(
        self, auth: CoinbaseAuth, ec_public_key, test_api_key: str,
    ) -> None:
        headers = auth.sign("GET", "/api/v3/brokerage/products")
        assert "Authorization" in headers
        assert headers["Authorization"].startswith("Bearer ")
        token = headers["Authorization"].split(" ", 1)[1]
        decoded = pyjwt.decode(token, ec_public_key, algorithms=["ES256"])
        assert decoded["sub"] == test_api_key
        assert decoded["iss"] == "cdp"
        assert decoded["uri"] == "GET api.coinbase.com/api/v3/brokerage/products"

    def test_sign_uri_format_includes_method_and_host(
        self, auth: CoinbaseAuth, ec_public_key,
    ) -> None:
        headers = auth.sign("POST", "/api/v3/brokerage/orders")
        token = headers["Authorization"].split(" ", 1)[1]
        decoded = pyjwt.decode(token, ec_public_key, algorithms=["ES256"])
        assert decoded["uri"] == "POST api.coinbase.com/api/v3/brokerage/orders"

    def test_sign_method_uppercased(
        self, auth: CoinbaseAuth, ec_public_key,
    ) -> None:
        headers = auth.sign("get", "/api/v3/brokerage/products")
        token = headers["Authorization"].split(" ", 1)[1]
        decoded = pyjwt.decode(token, ec_public_key, algorithms=["ES256"])
        assert decoded["uri"].startswith("GET ")

    def test_sign_has_exp_and_nbf(
        self, auth: CoinbaseAuth, ec_public_key,
    ) -> None:
        headers = auth.sign("GET", "/api/v3/brokerage/products")
        token = headers["Authorization"].split(" ", 1)[1]
        decoded = pyjwt.decode(token, ec_public_key, algorithms=["ES256"])
        assert decoded["exp"] - decoded["nbf"] == 120

    def test_sign_has_kid_in_header(
        self, auth: CoinbaseAuth, test_api_key: str,
    ) -> None:
        headers = auth.sign("GET", "/api/v3/brokerage/products")
        token = headers["Authorization"].split(" ", 1)[1]
        jwt_headers = pyjwt.get_unverified_header(token)
        assert jwt_headers["kid"] == test_api_key
        assert "nonce" in jwt_headers

    def test_pem_newline_handling(
        self, ec_private_key_pem: str, test_api_key: str,
    ) -> None:
        """PEM with literal \\n strings (as stored in env vars) should work."""
        escaped_pem = ec_private_key_pem.replace("\n", "\\n")
        auth = CoinbaseAuth(api_key=test_api_key, api_secret=escaped_pem)
        headers = auth.sign("GET", "/api/v3/brokerage/products")
        assert "Authorization" in headers

    def test_constructor_no_passphrase_param(self) -> None:
        """Verify passphrase is not accepted (removed from auth)."""
        sig = inspect.signature(CoinbaseAuth.__init__)
        params = list(sig.parameters.keys())
        assert "passphrase" not in params
