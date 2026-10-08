"""Exercise the authentication boundary and WHEP protocol forwarding."""
from pathlib import Path

import httpx
import yaml
from fastapi.testclient import TestClient

from test_nvr import FakeGateway, setup
from test_auth import unauthorized

RESOURCE = '/cam_1/whep/11111111-1111-1111-1111-111111111111'


def media_client(tmp_path, monkeypatch):
    main, _ = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(main, 'MediaGateway', FakeGateway)
    calls = []

    def upstream(request):
        calls.append(request)
        if request.method == 'POST':
            return httpx.Response(201, content=b'answer', headers={'Location': RESOURCE, 'ETag': '*',
                                  'Content-Type': 'application/sdp', 'Link': '<stun:localhost>; rel="ice-server"'})
        return httpx.Response(204)

    return main, calls, httpx.MockTransport(upstream)


def test_unauthorized_media_and_authenticated_protocol(tmp_path, monkeypatch):
    main, calls, transport = media_client(tmp_path, monkeypatch)
    with TestClient(main.app) as client:
        client.app.state.media.client = httpx.AsyncClient(base_url='http://media', transport=transport)
        for method, path in [('OPTIONS', '/api/media/cam_1/whep'), ('POST', '/api/media/cam_1/whep'),
                             ('PATCH', '/api/media' + RESOURCE), ('DELETE', '/api/media' + RESOURCE)]:
            unauthorized(client.request(method, path))
        assert not calls
        client.post('/api/login', json={'username': 'admin', 'password': 'secret'})
        copied = dict(client.cookies)
        assert client.options('/api/media/cam_1/whep').status_code == 204
        response = client.post('/api/media/cam_1/whep', content='offer', headers={'Content-Type': 'application/sdp'})
        assert response.status_code == 201 and response.text == 'answer'
        assert response.headers['location'] == '/api/media' + RESOURCE
        assert response.headers['etag'] == '*'
        assert 'stun:' in response.headers['link']
        assert calls[-1].content == b'offer'
        assert 'cookie' not in calls[-1].headers
        assert client.patch(response.headers['location'], content='candidate', headers={'If-Match': '*'}).status_code == 204
        assert calls[-1].headers['if-match'] == '*'
        # Another valid login must not be able to manipulate this viewer's resource.
        other = TestClient(main.app)
        other.post('/api/login', json={'username': 'admin', 'password': 'secret'})
        assert other.delete('/api/media' + RESOURCE).status_code == 404
        other.close()
        assert client.post('/api/logout').status_code == 204
        assert calls[-1].method == 'DELETE' and calls[-1].url.path == RESOURCE
        assert not client.app.state.media.resources
        client.cookies.update(copied)
        unauthorized(client.post('/api/media/cam_1/whep', content='offer'))


def test_expired_media_is_closed_and_challenges_removed(tmp_path, monkeypatch):
    main, calls, transport = media_client(tmp_path, monkeypatch)
    with TestClient(main.app) as client:
        client.app.state.media.client = httpx.AsyncClient(base_url='http://media', transport=transport)
        client.post('/api/login', json={'username': 'admin', 'password': 'secret'})
        client.post('/api/media/cam_1/whep', content='offer')
        token = client.cookies['nvr_session']
        main.sessions.entries[token] = (0, main.sessions.entries[token][1])
        unauthorized(client.options('/api/media/cam_1/whep'))
        client.portal.call(client.app.state.media.reap)
        assert calls[-1].method == 'DELETE'
        assert not client.app.state.media.resources
        client.post('/api/login', json={'username': 'admin', 'password': 'secret'})
        client.app.state.media.client = httpx.AsyncClient(base_url='http://media', transport=httpx.MockTransport(
            lambda request: httpx.Response(401, headers={'WWW-Authenticate': 'Basic realm="media"'})))
        unauthorized(client.options('/api/media/cam_1/whep'))


def test_media_disabled_auth_and_restricted_paths(tmp_path, monkeypatch):
    main, calls, transport = media_client(tmp_path, monkeypatch)
    monkeypatch.setattr(main, 'AUTH_USER', '')
    monkeypatch.setattr(main, 'AUTH_PASS', '')
    with TestClient(main.app) as client:
        client.app.state.media.client = httpx.AsyncClient(base_url='http://media', transport=transport)
        assert client.options('/api/media/cam_1/whep').status_code == 204
        for path in ('source', 'check_1_abc', 'v3', 'cam_0'):
            assert client.post(f'/api/media/{path}/whep').status_code == 404
        assert client.post('/api/media/cam_1/whip').status_code == 404
        assert client.post('/api/media/cam_1/whep', content=b'x' * 65537).status_code == 413
        assert client.post('/api/media/cam_1/whep', content='offer').status_code == 201
        assert client.delete('/api/media' + RESOURCE).status_code == 204


def test_compose_keeps_media_tcp_private():
    root = Path(__file__).resolve().parents[1]
    compose = yaml.safe_load((root / 'docker-compose.yml').read_text())
    gateway = compose['services']['mediamtx']
    assert len(gateway['ports']) == 1 and gateway['ports'][0].endswith('/udp')
    assert '${NVR_BIND:-127.0.0.1}' in compose['services']['nvr-app']['ports'][0]
    assert gateway['networks'] == compose['services']['nvr-app']['networks'] == ['media']
    config = yaml.safe_load((root / 'mediamtx.yml').read_text())
    assert not config['hls'] and not config['rtmp'] and not config['srt']
    assert not any(p['action'] == 'publish' for user in config['authInternalUsers'] for p in user['permissions'])


def test_revocation_during_offer_and_failed_delete_retry(tmp_path, monkeypatch):
    main, calls, transport = media_client(tmp_path, monkeypatch)
    with TestClient(main.app) as client:
        client.post('/api/login', json={'username': 'admin', 'password': 'secret'})
        token = client.cookies['nvr_session']

        def interrupted_offer(request):
            calls.append(request)
            if request.method == 'POST':
                main.sessions.revoke(token)
                return httpx.Response(201, headers={'Location': RESOURCE}, content='answer')
            return httpx.Response(503)

        client.app.state.media.client = httpx.AsyncClient(base_url='http://media', transport=httpx.MockTransport(interrupted_offer))
        unauthorized(client.post('/api/media/cam_1/whep', content='offer'))
        assert calls[-1].method == 'DELETE'
        assert RESOURCE in client.app.state.media.resources, 'Failed deletion must remain queued'
        client.app.state.media.client = httpx.AsyncClient(base_url='http://media', transport=transport)
        client.portal.call(client.app.state.media.reap)
        assert not client.app.state.media.resources
