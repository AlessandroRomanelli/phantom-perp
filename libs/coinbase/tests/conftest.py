"""Shared fixtures for Coinbase client tests."""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization

from libs.coinbase.auth import CoinbaseAuth
from libs.coinbase.rest_client import CoinbaseRESTClient
from libs.coinbase.rate_limiter import RateLimiter


@pytest.fixture
def ec_private_key_pem() -> str:
    """Generate a fresh ES256 private key in PEM format for testing."""
    private_key = ec.generate_private_key(ec.SECP256R1())
    pem_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return pem_bytes.decode("utf-8")


@pytest.fixture
def ec_public_key(ec_private_key_pem: str):
    """Extract public key from PEM for JWT verification."""
    private_key = serialization.load_pem_private_key(
        ec_private_key_pem.encode("utf-8"), password=None,
    )
    return private_key.public_key()


@pytest.fixture
def test_api_key() -> str:
    return "test-api-key-uuid"


@pytest.fixture
def auth(test_api_key: str, ec_private_key_pem: str) -> CoinbaseAuth:
    return CoinbaseAuth(api_key=test_api_key, api_secret=ec_private_key_pem)


@pytest.fixture
def client(auth: CoinbaseAuth) -> CoinbaseRESTClient:
    return CoinbaseRESTClient(
        auth=auth,
        base_url="https://api.coinbase.com",
        rate_limiter=RateLimiter(),
        portfolio_uuid="test-portfolio-uuid",
    )
