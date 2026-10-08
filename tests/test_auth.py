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
        copied = dict(client.cookies)
        with TestClient(main.app) as refreshed:
            refreshed.cookies.update(client.cookies)
            assert refreshed.get('/api/config').status_code == 200
        assert client.post('/api/logout').status_code == 204
        assert 'nvr_session' not in client.cookies
        for path in paths:
            unauthorized(client.get(path))
        with TestClient(main.app) as refreshed:
            refreshed.cookies.update(copied)
            unauthorized(refreshed.get('/api/config'))


def test_expiry_password_change_and_unique_sessions(tmp_path, monkeypatch):
    main, _ = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(main, 'MediaGateway', FakeGateway)
    with TestClient(main.app) as client:
        login = {'username': 'admin', 'password': 'secret'}
        client.post('/api/login', json=login)
        first = client.cookies['nvr_session']
        client.post('/api/login', json=login)
        second = client.cookies['nvr_session']
        assert first != second
        unauthorized(client.get('/api/config', headers={'Cookie': f'nvr_session={first}'}))
        expiry, fingerprint = main.sessions.entries[second]
        main.sessions.entries[second] = (0, fingerprint)
        unauthorized(client.get('/api/config'))
        main.sessions.entries[second] = (expiry, fingerprint)
        monkeypatch.setattr(main, 'AUTH_PASS', 'new-password')
        unauthorized(client.get('/api/config'))
        unauthorized(client.post('/api/login', json=login))
        assert client.post('/api/login', json={**login, 'password': 'new-password'}).status_code == 200
        assert client.get('/api/config').status_code == 200


@pytest.mark.parametrize('headers', [
    {'Origin': 'https://attacker.example'}, {'Origin': 'null'},
    {'Origin': 'https://testserver'}, {'Origin': 'http://['},
    {'Sec-Fetch-Site': 'cross-site'},
])
def test_cross_origin_writes_rejected(tmp_path, monkeypatch, headers):
    main, _ = setup(tmp_path, monkeypatch)
    with TestClient(main.app) as client:
        assert client.post('/api/login', headers=headers, json={'username': 'admin', 'password': 'secret'}).status_code == 403
        assert not client.cookies


def test_secure_cookie_attributes_and_same_origin(tmp_path, monkeypatch):
    main, _ = setup(tmp_path, monkeypatch)
    monkeypatch.setenv('COOKIE_SECURE', 'true')
    with TestClient(main.app, base_url='https://testserver') as client:
        response = client.post('/api/login', headers={'Origin': 'https://testserver'},
                               json={'username': 'admin', 'password': 'secret'})
        for value in ('HttpOnly', 'Secure', 'SameSite=strict', 'Max-Age=86400', 'Path=/'):
            assert value in response.headers['set-cookie']
        assert client.get('/api/config').status_code == 200


@pytest.mark.parametrize('peer,expected', [('127.0.0.1', 200), ('192.0.2.1', 403)])
def test_https_scheme_headers_only_from_trusted_proxy(tmp_path, monkeypatch, peer, expected):
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
    main, _ = setup(tmp_path, monkeypatch)
    application = ProxyHeadersMiddleware(main.app, trusted_hosts=['127.0.0.1'])
    with TestClient(application, client=(peer, 12345)) as client:
        response = client.post('/api/login', json={'username': 'admin', 'password': 'secret'},
                               headers={'Origin': 'https://testserver', 'X-Forwarded-Proto': 'https'})
        assert response.status_code == expected


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
