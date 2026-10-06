"""OIDC access tokens must be checked before any protected API call."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from backend.app.api.auth import AuthenticationError, OidcAuthenticator


class SigningKeys:
    def __init__(self, public_key):
        self.public_key = public_key

    def get_signing_key_from_jwt(self, token):
        return SimpleNamespace(key=self.public_key)


def test_oidc_access_token_validates_signature_issuer_audience_and_expiry():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(UTC)
    verifier = OidcAuthenticator(
        issuer="http://localhost:8080/realms/docucheck-demo",
        audience="docucheck-api",
        jwks_url="http://localhost:8080/realms/docucheck-demo/protocol/openid-connect/certs",
        jwk_client=SigningKeys(private_key.public_key()),
    )

    def bearer(**overrides):
        claims = {
            "iss": "http://localhost:8080/realms/docucheck-demo",
            "sub": "alice-123", "aud": "docucheck-api",
            "iat": now, "exp": now + timedelta(minutes=5),
        }
        claims.update(overrides)
        return "Bearer " + jwt.encode(claims, private_key, algorithm="RS256")

    principal = verifier.authenticate(bearer())
    assert principal.subject == "alice-123"
    assert len(principal.key) == 64

    for token in (
        bearer(aud="another-api"),
        bearer(iss="http://wrong.example/realm"),
        bearer(exp=now - timedelta(seconds=30)),
        "Bearer " + jwt.encode({"sub": "alice-123"}, "a" * 32, algorithm="HS256"),
        None,
    ):
        with pytest.raises(AuthenticationError):
            verifier.authenticate(token)
