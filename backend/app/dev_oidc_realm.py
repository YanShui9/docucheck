"""Prepare an ignored, local-only Keycloak realm import with demo accounts."""

import json
import secrets
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / ".env"
IMPORT_FILE = ROOT / "data" / "oidc-import" / "docucheck-demo-realm.json"


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
        "clients": [{
            "clientId": "docucheck-web",
            "enabled": True,
            "publicClient": True,
            "standardFlowEnabled": True,
            "directAccessGrantsEnabled": False,
            "redirectUris": ["http://127.0.0.1:5173/*"],
            "webOrigins": ["http://127.0.0.1:5173"],
            "attributes": {"pkce.code.challenge.method": "S256"},
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
    print(f"OIDC demo realm prepared: {prepare()}")
    print("Demo users: alice, bob, reviewer. Password is in local .env.")
