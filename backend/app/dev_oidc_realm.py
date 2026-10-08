"""Prepare an ignored, local-only Keycloak realm import with demo accounts."""

import argparse
import json
import os
import re
import secrets
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / ".env"
IMPORT_FILE = ROOT / "data" / "oidc-import" / "docucheck-demo-realm.json"


def web_origin() -> str:
    # Port must be a literal, matching Node parseEnv; secret parsing remains unchanged.
    existing = dict(dotenv_values(ENV_FILE, interpolate=False))
    port = os.environ.get("DOCUCHECK_WEB_PORT", existing.get("DOCUCHECK_WEB_PORT", "15173"))
    if not isinstance(port, str) or not re.fullmatch(r"[0-9]{1,5}", port):
        raise ValueError("invalid_web_port")
    if not 1 <= int(port) <= 65535:
        raise ValueError("invalid_web_port")
    return f"http://127.0.0.1:{int(port)}"


def sync_client(*, client: httpx.Client | None = None) -> str:
    """Update only the existing local demo client's callbacks; never reset accounts."""
    existing = dict(dotenv_values(ENV_FILE))
    origin = web_origin()
    password = existing.get("DOCUCHECK_OIDC_ADMIN_PASSWORD")
    if not password:
        raise RuntimeError("oidc_demo_secret_missing")
    if client is None:
        with httpx.Client(timeout=10, trust_env=False, follow_redirects=False) as local:
            return _sync_client(local, origin, password)
    return _sync_client(client, origin, password)


def _sync_client(client: httpx.Client, origin: str, password: str) -> str:
    base = "http://127.0.0.1:8080"
    try:
        response = client.post(f"{base}/realms/master/protocol/openid-connect/token", data={
            "client_id": "admin-cli", "grant_type": "password",
            "username": "admin", "password": password,
        })
        response.raise_for_status()
        headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
        url = f"{base}/admin/realms/docucheck-demo/clients"
        response = client.get(url, params={"clientId": "docucheck-web"}, headers=headers)
        response.raise_for_status()
        matches = response.json()
        if len(matches) != 1 or matches[0].get("clientId") != "docucheck-web":
            raise ValueError("client_missing_or_ambiguous")
        client_url = f"{url}/{matches[0]['id']}"
        response = client.get(client_url, headers=headers)
        response.raise_for_status()
        settings = response.json()
        settings["redirectUris"] = [origin + "/*"]
        settings["webOrigins"] = [origin]
        settings.setdefault("attributes", {})["post.logout.redirect.uris"] = origin
        response = client.put(client_url, headers=headers, json=settings)
        response.raise_for_status()
        response = client.get(client_url, headers=headers)
        response.raise_for_status()
        actual = response.json()
        if (actual.get("redirectUris") != [origin + "/*"]
                or actual.get("webOrigins") != [origin]
                or actual.get("attributes", {}).get("post.logout.redirect.uris") != origin):
            raise ValueError("client_sync_verification_failed")
    except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError):
        raise RuntimeError("oidc_client_sync_failed") from None
    return origin


def _secret(name: str, existing: dict[str, str | None]) -> str:
    value = existing.get(name)
    if value:
        return value
    value = secrets.token_urlsafe(32)
    with ENV_FILE.open("a", encoding="utf-8") as stream:
        stream.write(f"\n{name}={value}\n")
    existing[name] = value
    return value


def prepare() -> Path:
    existing = dict(dotenv_values(ENV_FILE))
    origin = web_origin()
    admin_password = _secret("DOCUCHECK_OIDC_ADMIN_PASSWORD", existing)
    demo_password = _secret("DOCUCHECK_OIDC_DEMO_PASSWORD", existing)
    if not admin_password or not demo_password:
        raise RuntimeError("oidc_demo_secret_missing")
    users = []
    for index, username in enumerate(("alice", "bob", "reviewer"), start=1):
        users.append({
            "id": f"00000000-0000-4000-8000-{index:012d}",
            "username": username,
            "firstName": username.capitalize(),
            "lastName": "Demo",
            "email": f"{username}@docucheck.local",
            "enabled": True,
            "emailVerified": True,
            "credentials": [{"type": "password", "value": demo_password,
                             "temporary": False}],
        })
    realm = {
        "realm": "docucheck-demo",
        "enabled": True,
        "registrationAllowed": False,
        "resetPasswordAllowed": False,
        "displayName": "DocuCheck",
        "loginTheme": "docucheck-login",
        "internationalizationEnabled": True,
        "defaultLocale": "zh-CN",
        "supportedLocales": ["zh-CN", "en"],
        "sslRequired": "none",
        "clients": [{
            "clientId": "docucheck-web",
            "enabled": True,
            "publicClient": True,
            "standardFlowEnabled": True,
            "directAccessGrantsEnabled": False,
            "redirectUris": [origin + "/*"],
            "webOrigins": [origin],
            "attributes": {"pkce.code.challenge.method": "S256",
                           "post.logout.redirect.uris": origin},
            "protocolMappers": [{
                "name": "docucheck-api-audience",
                "protocol": "openid-connect",
                "protocolMapper": "oidc-audience-mapper",
                "consentRequired": False,
                "config": {
                    "included.custom.audience": "docucheck-api",
                    "access.token.claim": "true",
                    "id.token.claim": "false",
                },
            }],
        }],
        "users": users,
    }
    IMPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = IMPORT_FILE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(realm, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    temporary.replace(IMPORT_FILE)
    return IMPORT_FILE


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sync-client", action="store_true",
                        help="Update existing local web client callbacks without resetting users")
    args = parser.parse_args()
    if args.sync_client:
        print(f"OIDC local client callbacks synchronized: {sync_client()}")
    else:
        print(f"OIDC demo realm prepared: {prepare()}")
        print("Demo users: alice, bob, reviewer. Password is in local .env.")
