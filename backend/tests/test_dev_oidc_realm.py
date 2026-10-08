import json

import httpx
import pytest

from backend.app import dev_oidc_realm as demo


def local_files(tmp_path, monkeypatch, port=None):
    env = tmp_path / '.env'
    env.write_text('DOCUCHECK_OIDC_ADMIN_PASSWORD=test-admin\n'
                   'DOCUCHECK_OIDC_DEMO_PASSWORD=test-demo\n'
                   + (f'DOCUCHECK_WEB_PORT={port}\n' if port is not None else ''),
                   encoding='utf-8')
    monkeypatch.setattr(demo, 'ENV_FILE', env)
    monkeypatch.setattr(demo, 'IMPORT_FILE', tmp_path / 'realm.json')
    monkeypatch.delenv('DOCUCHECK_WEB_PORT', raising=False)
    return env


@pytest.mark.parametrize('port', [None, '15174'])
def test_import_uses_same_local_web_port(tmp_path, monkeypatch, port):
    local_files(tmp_path, monkeypatch, port)
    client = json.loads(demo.prepare().read_text(encoding='utf-8'))['clients'][0]
    origin = f'http://127.0.0.1:{port or "15173"}'
    assert client['redirectUris'] == [origin + '/*']
    assert client['webOrigins'] == [origin]
    assert client['attributes']['post.logout.redirect.uris'] == origin
    assert client['attributes']['pkce.code.challenge.method'] == 'S256'
    assert client['directAccessGrantsEnabled'] is False


@pytest.mark.parametrize('port', ['0', '65536', '-1', '1e4', '15173/path', '１２３', '', '${PORT}'])
def test_invalid_port_fails_before_writing_secrets(tmp_path, monkeypatch, port):
    env = local_files(tmp_path, monkeypatch, port)
    before = env.read_bytes()
    with pytest.raises(ValueError, match='invalid_web_port'):
        demo.prepare()
    assert env.read_bytes() == before
    assert not demo.IMPORT_FILE.exists()


def test_sync_existing_client_preserves_identity_and_other_settings(tmp_path, monkeypatch):
    env = local_files(tmp_path, monkeypatch)
    before = env.read_bytes()
    original = {'id': 'existing', 'clientId': 'docucheck-web',
                'redirectUris': ['http://127.0.0.1:5173/*'],
                'webOrigins': ['http://127.0.0.1:5173'],
                'attributes': {'pkce.code.challenge.method': 'S256', 'keep': 'yes'},
                'directAccessGrantsEnabled': False, 'protocolMappers': [{'name': 'keep'}]}
    requests = []
    updated = None

    def handle(request):
        nonlocal updated
        requests.append((request.method, request.url.path))
        if request.url.path.endswith('/token'):
            return httpx.Response(200, json={'access_token': 'private-test-token'})
        assert request.headers['authorization'] == 'Bearer private-test-token'
        if request.method == 'PUT':
            updated = json.loads(request.content)
            return httpx.Response(204)
        if request.url.path.endswith('/clients'):
            return httpx.Response(200, json=[original])
        return httpx.Response(200, json=updated or original)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        assert demo.sync_client(client=client) == 'http://127.0.0.1:15173'
    assert updated['id'] == 'existing'
    assert updated['protocolMappers'] == original['protocolMappers']
    assert updated['attributes']['keep'] == 'yes'
    assert updated['attributes']['pkce.code.challenge.method'] == 'S256'
    assert updated['directAccessGrantsEnabled'] is False
    assert updated['redirectUris'] == ['http://127.0.0.1:15173/*']
    assert updated['webOrigins'] == ['http://127.0.0.1:15173']
    assert updated['attributes']['post.logout.redirect.uris'] == 'http://127.0.0.1:15173'
    assert env.read_bytes() == before
    assert not demo.IMPORT_FILE.exists()
    assert not any('/users' in path or '/realms/master' == path for _, path in requests)


@pytest.mark.parametrize('status', [401, 500])
def test_sync_failure_is_sanitized_and_does_not_write(tmp_path, monkeypatch, status):
    local_files(tmp_path, monkeypatch)
    requests = []

    def handle(request):
        requests.append(request.method)
        return httpx.Response(status, text='private credential response')

    with (httpx.Client(transport=httpx.MockTransport(handle)) as client,
          pytest.raises(RuntimeError, match='^oidc_client_sync_failed$')):
        demo.sync_client(client=client)
    assert requests == ['POST']
