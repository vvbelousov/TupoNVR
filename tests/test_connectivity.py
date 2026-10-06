import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from test_nvr import setup, FakeGateway


def observation(ok):
    return {'ok': ok, 'checked_at': '2026-10-06T12:00:00+00:00'}


def test_connectivity_debounces_failure_recovers_and_expires(tmp_path, monkeypatch):
    _, video = setup(tmp_path, monkeypatch)
    async def exercise():
        gateway = video.MediaGateway()
        assert gateway.connectivity_status(1, now=0)['connectivity_state'] == 'UNKNOWN'
        gateway.observe_probe(1, observation(True), now=1)
        assert gateway.connectivity_status(1, now=2)['online'] is True
        gateway.observe_probe(1, observation(False), now=61)
        assert gateway.connectivity_status(1, now=62)['connectivity_state'] == 'ONLINE'
        assert gateway.next_probe[1] == 76
        gateway.observe_probe(1, observation(False), now=76)
        assert gateway.connectivity_status(1, now=77)['connectivity_state'] == 'OFFLINE'
        gateway.observe_probe(1, observation(True), now=136)
        assert gateway.connectivity_status(1, now=137)['connectivity_state'] == 'ONLINE'
        assert gateway.connectivity_status(1, now=317)['connectivity_state'] == 'UNKNOWN'
        gateway.observe_probe(2, observation(False), now=1)
        assert gateway.connectivity_status(2, now=2)['online'] is None
        gateway.observe_probe(2, observation(False), now=16)
        assert gateway.connectivity_status(2, now=17)['online'] is False
        await gateway.close()
    asyncio.run(exercise())


@pytest.mark.parametrize('recording,reader,probe_ok', [(True,False,True),(False,False,True),(False,True,True),(True,True,True),(False,False,False),(True,True,False)])
def test_source_connectivity_does_not_follow_readers_or_recording(tmp_path, monkeypatch, recording, reader, probe_ok):
    _, video = setup(tmp_path, monkeypatch)
    async def exercise():
        gateway = video.MediaGateway()
        gateway.observe_probe(1, observation(probe_ok))
        gateway.observe_probe(1, observation(probe_ok))
        async def call(*args, **kwargs):
            return {'ready': reader or recording, 'bytesReceived': 123}
        gateway.call = call
        result = await gateway.status(1)
        assert result['online'] is probe_ok
        assert result['bytes_received'] == 123
        await gateway.close()
    asyncio.run(exercise())


def test_relay_failure_does_not_change_camera_connectivity(tmp_path, monkeypatch):
    _, video = setup(tmp_path, monkeypatch)
    async def exercise():
        gateway = video.MediaGateway()
        gateway.observe_probe(1, observation(True))
        async def call(*args, **kwargs):
            raise RuntimeError('Relay down')
        gateway.call = call
        assert (await gateway.status(1))['online'] is True
        await gateway.close()
    asyncio.run(exercise())


@pytest.mark.parametrize('recording,connectivity,health', [(False,True,'PAUSED'),(True,True,'WRITING'),(True,True,'RECONNECTING'),(True,False,'RECONNECTING')])
def test_status_and_dashboard_keep_connectivity_separate(tmp_path, monkeypatch, recording, connectivity, health):
    main, video = setup(tmp_path, monkeypatch)
    class Gateway(FakeGateway):
        async def status(self, cid):
            return {'online': connectivity, 'connectivity_state': 'ONLINE' if connectivity else 'OFFLINE', 'bytes_received': 0}
    monkeypatch.setattr(main, 'MediaGateway', Gateway)
    with TestClient(main.app) as client:
        auth = ('admin', 'secret')
        created = client.post('/api/cameras', auth=auth, json={'name':'cam','rtsp_url':'rtsp://cam/live','recording_enabled':False})
        cid = created.json()['id']
        # No real recorder is started by this API contract test.
        with main.db() as connection:
            connection.execute('UPDATE cameras SET recording_enabled=? WHERE id=?', (recording,cid))
        supervisor = client.app.state.supervisor
        if health == 'WRITING':
            import time
            supervisor.procs[cid] = SimpleNamespace(returncode=None)
            supervisor.progress[cid] = {'started_tick':time.monotonic(),'last_tick':time.monotonic(),'last_progress_at':'2026-10-06T12:00:00Z'}
        supervisor.errors[cid] = 'Previous recorder failure'
        result = client.get(f'/api/cameras/{cid}/status', auth=auth).json()
        assert result['state'] == ('ONLINE' if connectivity else 'OFFLINE')
        assert result['online'] is connectivity
        assert result['recording_enabled'] is recording
        assert result['recording_health'] == health
        assert client.get('/api/dashboard',auth=auth).json()['online'] == int(connectivity)
        assert bool(client.get('/api/dashboard',auth=auth).json()['errors']) is recording
        metrics = client.get('/metrics',auth=auth).text
        assert f'camera_online{{camera_id="{cid}"}} {int(connectivity)}' in metrics
        supervisor.procs.clear()


def test_periodic_probe_is_bounded_shared_cached_and_secret_safe(tmp_path, monkeypatch, caplog):
    _, video = setup(tmp_path, monkeypatch)
    running = 0
    maximum = 0
    sources = []
    class Process:
        returncode = None
        async def communicate(self):
            nonlocal running, maximum
            running += 1
            maximum = max(maximum, running)
            await asyncio.sleep(.03)
            running -= 1
            self.returncode = 0
            return json.dumps({'streams':[{'codec_type':'video','codec_name':'h264','width':64,'height':64}]}).encode(), b''
    async def spawn(*args, **kwargs):
        sources.append(args[-1])
        assert kwargs['stderr'] == asyncio.subprocess.DEVNULL
        return Process()
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    async def exercise():
        gateway = video.MediaGateway()
        async def call(*args, **kwargs):
            return {'ready':False,'bytesReceived':0}
        gateway.call = call
        rows = [{'id':cid,'enabled':True,'recording_enabled':False,'rtsp_url':'rtsp://private-user:private-password@camera/live?key=private-token','username':None,'password':None} for cid in range(1,5)]
        await asyncio.gather(gateway.poll_health(rows), gateway.check(rows[0]))
        assert maximum == 2
        assert len(sources) == 4  # Operator diagnostics share the periodic probe.
        await gateway.poll_health(rows)
        await gateway.check(rows[0])
        assert len(sources) == 4
        for _ in range(3):
            await gateway.status(1)
        assert len(sources) == 4  # Browser/API reads cannot probe the source.
        assert 'private' not in json.dumps(gateway.checks)
        assert 'private' not in caplog.text
        await gateway.poll_health([{**row,'enabled':False} for row in rows])
        assert not gateway.connectivity and not gateway.check_sources
        await gateway.close()
    asyncio.run(exercise())


def test_edit_during_periodic_probe_does_not_stop_scheduler(tmp_path, monkeypatch):
    _, video = setup(tmp_path, monkeypatch)
    async def exercise():
        gateway = video.MediaGateway()
        entered = asyncio.Event()
        async def probe(row):
            entered.set()
            await asyncio.sleep(60)
        gateway._check = probe
        row = {'id':1,'enabled':True,'rtsp_url':'rtsp://cam/live','username':None,'password':None}
        poll = asyncio.create_task(gateway.poll_health([row]))
        await entered.wait()
        await gateway.invalidate_check(1)
        await asyncio.wait_for(poll, 1)
        assert not poll.cancelled()
        await gateway.close()
    asyncio.run(exercise())


def test_disabled_camera_manual_check_survives_periodic_cleanup(tmp_path, monkeypatch):
    _, video = setup(tmp_path, monkeypatch)
    async def exercise():
        gateway = video.MediaGateway()
        entered = asyncio.Event()
        finish = asyncio.Event()
        async def probe(row):
            entered.set()
            await finish.wait()
            result = observation(True)
            gateway.checks[row['id']] = result
            gateway.observe_probe(row['id'], result)
            return result
        gateway._check = probe
        row = {'id':1,'enabled':False,'rtsp_url':'rtsp://cam/live','username':None,'password':None}
        manual = asyncio.create_task(gateway.check(row))
        await entered.wait()
        await gateway.poll_health([row])
        assert not manual.done()
        finish.set()
        assert (await manual)['ok']
        await gateway.poll_health([row])
        assert not gateway.connectivity and not gateway.check_sources
        await gateway.close()
    asyncio.run(exercise())


def test_invalidated_manual_check_returns_retry_response(tmp_path, monkeypatch):
    main, _ = setup(tmp_path, monkeypatch)
    class Gateway(FakeGateway):
        async def check(self, row):
            raise asyncio.CancelledError()
    monkeypatch.setattr(main, 'MediaGateway', Gateway)
    with TestClient(main.app) as client:
        auth = ('admin','secret')
        cid = client.post('/api/cameras',auth=auth,json={'name':'cam','rtsp_url':'rtsp://cam/live','recording_enabled':False}).json()['id']
        response = client.post(f'/api/cameras/{cid}/check',auth=auth)
        assert response.status_code == 409
        assert response.json()['detail'] == 'Camera changed; retry check'
