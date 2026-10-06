$env:DOCUCHECK_AUTH_MODE = 'oidc'
$env:DOCUCHECK_OIDC_ISSUER = 'http://127.0.0.1:8080/realms/docucheck-demo'
$env:DOCUCHECK_OIDC_AUDIENCE = 'docucheck-api'
$env:DOCUCHECK_OIDC_JWKS_URL = 'http://127.0.0.1:8080/realms/docucheck-demo/protocol/openid-connect/certs'

python.exe -m uvicorn backend.app.api.main:create_app --factory --env-file .env --host 127.0.0.1 --port 8000
