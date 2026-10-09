"""First-run defaults, safe diagnostics and both supported network modes."""
import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_nvr import setup, FakeGateway


def test_first_run_and_invalid_source_are_secret_safe(tmp_path, monkeypatch):
    main, _ = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(main, 'MediaGateway', FakeGateway)
    with TestClient(main.app) as client:
        client.post('/api/login', json={'username': 'admin', 'password': 'secret'})
        assert client.get('/api/cameras').json() == []
        assert client.get('/api/dashboard').json()['cameras'] == 0
        assert client.get('/api/layout').json()['tiles'] == []
        for url in ('', 'http://user:private@camera/live', 'rtsp://user:private@camera:bad/live'):
            response = client.post('/api/cameras', json={'name': 'First', 'rtsp_url': url})
            assert response.status_code == 422
            assert 'private' not in response.text
        # Creation defaults remain backward compatible. Disable the source so
        # this test never starts a real recorder.
        created = client.post('/api/cameras', json={'name': 'First', 'rtsp_url': 'rtsp://camera/live', 'enabled': False})
        assert created.status_code == 201
        camera = created.json()
        assert camera['recording_enabled'] == 1
        assert camera['recording_destination'] == 'default'
        assert camera['retention_days'] == 7
        assert (tmp_path / 'recordings' / 'default').is_dir()


@pytest.mark.parametrize('reason,code', [
    ('Server returned 401 Unauthorized (authorization failed)', 'authentication_failed'),
    ('Server returned 403 Forbidden', 'authentication_failed'),
    ('Connection timed out', 'timeout'),
    ('Connection refused', 'unavailable'),
    ('No route to host', 'unavailable'),
    ('Name or service not known', 'unavailable'),
    ('Server returned 404 Not Found', 'source_not_found'),
    ('Invalid data found when processing input', 'unreadable_media'),
])
def test_ffprobe_errors_are_actionable_without_returning_source_text(tmp_path, monkeypatch, caplog, reason, code):
    _, video = setup(tmp_path, monkeypatch)
    class Process:
        returncode = 1
        async def communicate(self):
            return json.dumps({'error': {'string': reason + ' rtsp://private:password@camera/live?token=secret'}}).encode(), b''
    async def spawn(*args, **kwargs):
        assert '-show_error' in args
        assert kwargs['stderr'] == asyncio.subprocess.DEVNULL
        return Process()
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    async def exercise():
        gateway = video.MediaGateway()
        result = await gateway.check({'id': 1, 'rtsp_url': 'rtsp://private:password@camera/live?token=secret', 'username': None, 'password': None})
        assert not result['ok'] and result['code'] == code
        assert result['checked_at']
        assert 'private' not in json.dumps(result) + caplog.text
        assert 'token=secret' not in json.dumps(result) + caplog.text
        await gateway.close()
    asyncio.run(exercise())


def test_probe_timeout_kills_process(tmp_path, monkeypatch):
    _, video = setup(tmp_path, monkeypatch)
    class Process:
        returncode = None
        killed = False
        async def communicate(self):
            raise asyncio.TimeoutError()
        def kill(self):
            self.killed = True
            self.returncode = -9
        async def wait(self):
            return self.returncode
    proc = Process()
    async def spawn(*args, **kwargs):
        return proc
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    async def exercise():
        gateway = video.MediaGateway()
        result = await gateway.check({'id': 1, 'rtsp_url': 'rtsp://camera/live', 'username': None, 'password': None})
        assert result['code'] == 'timeout' and proc.killed
        await gateway.close()
    asyncio.run(exercise())


@pytest.mark.skipif(not shutil.which('docker'), reason='Docker Compose required')
@pytest.mark.parametrize('lan', [False, True])
@pytest.mark.parametrize('host', ['', '192.168.1.20', '192.168.1.20,vpn.example.org'])
def test_compose_network_modes_and_explicit_override(lan, host):
    root = Path(__file__).resolve().parents[1]
    args = ['docker', 'compose', '--env-file', '.env.example', '-f', 'docker-compose.yml']
    if lan:
        args += ['-f', 'compose.lan.yml']
    import os
    env = {**os.environ, 'WEBRTC_HOST': host, 'NVR_PORT': '9080', 'NVR_BIND': '127.0.0.1'}
    config = json.loads(subprocess.check_output(args + ['config', '--format', 'json'], cwd=root, env=env))
    gateway, app = (config['services'][name] for name in ('mediamtx', 'nvr-app'))
    assert gateway['environment']['MTX_WEBRTCADDITIONALHOSTS'] == (host or ('' if lan else '127.0.0.1'))
    assert app['restart'] == gateway['restart'] == 'unless-stopped'
    assert app['image'] == 'vvbelousov/tuponvr:0.2.1'
    assert 'build' not in app
    assert app['environment']['DATABASE_PATH'] == '/data/nvr.sqlite3'
    assert app['environment']['DEFAULT_RECORDING_PATH'] == '/recordings'
    if lan:
        assert app['network_mode'] == gateway['network_mode'] == 'host'
        assert not gateway.get('ports') and not app.get('ports')
        for name in ('MTX_APIADDRESS', 'MTX_RTSPADDRESS', 'MTX_WEBRTCADDRESS'):
            assert gateway['environment'][name].startswith('127.0.0.1:')
        assert app['environment']['MEDIAMTX_RTSP_HOST'] == '127.0.0.1'
        assert app['environment']['NVR_PORT'] == '9080'
        assert app['command'][-1].startswith('exec uvicorn')
    else:
        assert gateway['ports'][0]['protocol'] == 'udp'
        assert app['ports'][0]['host_ip'] == '127.0.0.1'


@pytest.mark.parametrize('streams', [[], [{'codec_type': 'audio', 'codec_name': 'aac'}], [{'codec_type': 'video', 'codec_name': 'unknown'}]])
def test_camera_without_usable_video(tmp_path, monkeypatch, streams):
    _, video = setup(tmp_path, monkeypatch)
    class Process:
        returncode = 0
        async def communicate(self):
            return json.dumps({'streams': streams}).encode(), b''
    async def spawn(*args, **kwargs):
        return Process()
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    async def exercise():
        gateway = video.MediaGateway()
        result = await gateway.check({'id': 1, 'rtsp_url': 'rtsp://camera/live', 'username': None, 'password': None})
        assert result['code'] == 'unsupported_media' and not result['ok']
        await gateway.close()
    asyncio.run(exercise())
