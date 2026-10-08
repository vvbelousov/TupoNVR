"""Session authentication never exposes a browser HTTP Basic challenge."""
import pytest
from fastapi.testclient import TestClient

from test_nvr import FakeGateway, setup


def unauthorized(response):
    assert response.status_code == 401
    assert 'www-authenticate' not in response.headers


def test_startup_session_login_refresh_and_logout(tmp_path, monkeypatch):
    main, _ = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(main, 'MediaGateway', FakeGateway)
    paths = ('/api/cameras', '/api/layout', '/api/dashboard', '/api/config',
             '/api/time', '/api/storage/status', '/api/recordings', '/metrics')
    with TestClient(main.app) as client:
        for path in paths:
            unauthorized(client.get(path))
        unauthorized(client.post('/api/login', json={'username':'admin', 'password':'wrong'}))
        assert 'nvr_session' not in client.cookies
        login = client.post('/api/login', json={'username':'admin', 'password':'secret'})
        assert login.status_code == 200
        assert 'HttpOnly' in login.headers['set-cookie']
        for path in paths:
            assert client.get(path).status_code == 200
        # A fresh client with the browser's cookie models refresh/reopen.
        with TestClient(main.app) as refreshed:
            refreshed.cookies.update(client.cookies)
            assert refreshed.get('/api/config').status_code == 200
        assert client.post('/api/logout').status_code == 204
        assert 'nvr_session' not in client.cookies
        for path in paths:
            unauthorized(client.get(path))
        with TestClient(main.app) as refreshed:
            refreshed.cookies.update(client.cookies)
            unauthorized(refreshed.get('/api/config'))


@pytest.mark.parametrize('authorization', [
    'Basic YWRtaW46c2VjcmV0',  # Even correct legacy credentials are rejected.
    'Basic invalid', 'Basic', 'Bearer invalid',
])
def test_legacy_authorization_cannot_authenticate_or_challenge(tmp_path, monkeypatch, authorization):
    main, _ = setup(tmp_path, monkeypatch)
    with TestClient(main.app) as client:
        unauthorized(client.get('/api/config', headers={'Authorization':authorization}))
        assert not main.app.openapi().get('components', {}).get('securitySchemes')


def test_direct_ui_routes_are_public_shells_with_protected_apis(tmp_path, monkeypatch):
    main, _ = setup(tmp_path, monkeypatch)
    assets = tmp_path / 'ui'
    assets.mkdir()
    (assets / 'index.html').write_text('TupoNVR application shell')
    main.mount_ui(main.app, assets)
    with TestClient(main.app) as client:
        for path in ('/', '/overview', '/multiview', '/archive', '/settings', '/account'):
            response = client.get(path)
            assert response.status_code == 200
            assert response.text == 'TupoNVR application shell'
            assert 'www-authenticate' not in response.headers
        unauthorized(client.get('/api/config'))
