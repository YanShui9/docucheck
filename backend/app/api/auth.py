"""OIDC access-token verification for the HTTP boundary."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from urllib.parse import urlparse

import jwt


class AuthenticationError(ValueError):
    def __init__(self, code: str = "unauthorized"):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Principal:
    issuer: str
    subject: str
    name: str = ""

    @property
    def key(self) -> str:
        return hashlib.sha256(
            f"{self.issuer}\0{self.subject}".encode(),
        ).hexdigest()


class LocalAuthenticator:
    """Compatibility mode for loopback-only M3 development and existing tests."""

    enabled = False

    def authenticate(self, authorization: str | None) -> Principal:
        return Principal("local", "local")


class OidcAuthenticator:
    enabled = True

    def __init__(self, *, issuer: str, audience: str, jwks_url: str,
                 jwk_client=None):
        if not issuer or not audience or not jwks_url:
            raise ValueError("oidc_configuration_missing")
        for url in (issuer, jwks_url):
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("oidc_url_invalid")
            if parsed.scheme == "http" and parsed.hostname not in {
                    "localhost", "127.0.0.1", "::1"}:
                raise ValueError("oidc_https_required")
        self.issuer = issuer.rstrip("/")
        self.audience = audience
        self.jwks = jwk_client or jwt.PyJWKClient(jwks_url, timeout=5)

    def authenticate(self, authorization: str | None) -> Principal:
        if not authorization:
            raise AuthenticationError()
        scheme, separator, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not separator or not token.strip():
            raise AuthenticationError()
        try:
            signing_key = self.jwks.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token, signing_key.key, algorithms=["RS256"],
                issuer=self.issuer, audience=self.audience,
                options={"require": ["iss", "sub", "aud", "iat", "exp"]},
                leeway=10,
            )
        except (jwt.PyJWTError, ValueError, TypeError) as exc:
            raise AuthenticationError("invalid_token") from exc
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject or len(subject) > 255:
            raise AuthenticationError("invalid_token")
        name = claims.get("preferred_username") or claims.get("name") or subject
        return Principal(self.issuer, subject,
                         name if isinstance(name, str) else subject)


def authenticator_from_env():
    mode = os.getenv("DOCUCHECK_AUTH_MODE", "local").lower()
    if mode == "local":
        return LocalAuthenticator()
    if mode != "oidc":
        raise ValueError("invalid_auth_mode")
    return OidcAuthenticator(
        issuer=os.getenv("DOCUCHECK_OIDC_ISSUER", ""),
        audience=os.getenv("DOCUCHECK_OIDC_AUDIENCE", ""),
        jwks_url=os.getenv("DOCUCHECK_OIDC_JWKS_URL", ""),
    )
