import asyncio
import importlib
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))


def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_PATH', str(tmp_path / 'data' / 'test.sqlite3'))
    monkeypatch.setenv('DEFAULT_RECORDING_PATH', str(tmp_path / 'recordings'))
    monkeypatch.setenv('AUTH_USERNAME', 'admin')
    monkeypatch.setenv('AUTH_PASSWORD', 'secret')
    for mod in ('main', 'video', 'notifications', 'db'):
        sys.modules.pop(mod, None)
    video = importlib.import_module('video')
    main = importlib.import_module('main')
    video.ROOT = tmp_path / 'recordings'
    main.ROOT = video.ROOT
    return main, video


class FakeGateway:
    def __init__(self):
        self.paths = {}
    async def reconcile(self, rows):
        self.paths = {r['id']: r for r in rows if r['enabled']}
    async def status(self, camera_id):
        return {'online': camera_id in self.paths, 'bytes_received': 1024}
    async def call(self, method, path, body=None):
        return {}
    async def close(self):
        pass


def test_camera_auth_redaction_layout_and_archive(tmp_path, monkeypatch):
    main, video = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(main, 'MediaGateway', FakeGateway)
    with TestClient(main.app) as client:
        assert client.get('/api/cameras').status_code == 401
        assert client.post('/api/login', json={'username':'admin','password':'secret'}).status_code == 200
        payload = {'name':'Door','rtsp_url':'rtsp://foo:bar@camera.local:554/live','enabled':True,'recording_enabled':False,'recording_destination':'default'}
        r = client.post('/api/cameras',json=payload)
        assert r.status_code == 201, r.text
        cid = r.json()['id']
        assert 'bar' not in str(r.json())
        assert client.get(f'/api/cameras/{cid}/status').json()['online'] is True
        assert client.post('/api/cameras',json={**payload,'recording_destination':'../outside'}).status_code == 422
        lay = {'columns':3,'tiles':[{'camera_id':cid,'x':0,'y':0,'w':2,'h':1}]}
        assert client.put('/api/layout',json=lay).status_code == 200
        assert client.get('/api/layout').json() == lay
        assert client.post(f'/api/cameras/{cid}/stop').status_code == 200
        assert client.get(f'/api/cameras/{cid}/status').json()['state'] == 'DISABLED'
        assert client.delete(f'/api/cameras/{cid}').status_code == 204
    assert (tmp_path/'data'/'test.sqlite3').stat().st_mode & 0o777 == 0o600


def test_stream_copy_segment_index_and_retention(tmp_path, monkeypatch):
    main, video = setup(tmp_path, monkeypatch)
    main.init()
    folder = video.ROOT / 'default' / '1' / '2026' / '01' / '01' / '00'
    folder.mkdir(parents=True)
    output = folder / '20260101T000000.mp4'
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i','testsrc2=size=64x64:rate=5','-t','2','-c:v','mpeg4',str(output)],check=True)
    video.index_segments(1, video.ROOT/'default'/'1')
    with main.db() as c:
        s = c.execute('SELECT * FROM segments').fetchone()
        assert s and s['size_bytes'] > 1024
    row = {'id':1,'recording_destination':'default','retention_days':1}
    video.cleanup([row],set())
    assert not output.exists()
    with main.db() as c:
        assert c.execute('SELECT COUNT(*) FROM segments').fetchone()[0] == 0


def test_gateway_reconcile_does_not_patch_unchanged_source():
    from video import MediaGateway
    async def exercise():
        g = MediaGateway()
        paths = {}
        calls = []
        async def call(method, endpoint, body=None):
            calls.append((method,endpoint))
            if endpoint.startswith('/v3/config/paths/list'):
                return {'items':[{'name':p} for p in paths]}
            name = endpoint.split('/')[-1]
            if method == 'DELETE': paths.pop(name,None)
            elif method in ('POST','PATCH'): paths[name] = body
            return {}
        g.call = call
        row = {'id':1,'rtsp_url':'rtsp://cam/live','substream_url':None,'username':None,'password':None,'enabled':1,'recording_enabled':1}
        await g.reconcile([row]);await g.reconcile([row])
        assert len([c for c in calls if c[0]=='POST']) == 1
        row['enabled']=0
        await g.reconcile([row])
        assert len([c for c in calls if c[0]=='DELETE']) == 1
        await g.close()
    asyncio.run(exercise())


def test_camera_ids_survive_deletion_and_legacy_migration(tmp_path, monkeypatch):
    main, video = setup(tmp_path, monkeypatch)
    main.init()
    with main.db() as c:
        c.execute("INSERT INTO cameras(name,rtsp_url) VALUES('old','rtsp://cam/live')")
        c.execute('DELETE FROM cameras')
        assert c.execute("INSERT INTO cameras(name,rtsp_url) VALUES('new','rtsp://cam/live')").lastrowid == 2
        # Recreate the original schema to exercise migration of an existing DB.
        c.execute('ALTER TABLE cameras RENAME TO old_cameras')
        schema = c.execute("SELECT sql FROM sqlite_master WHERE name='old_cameras'").fetchone()[0]
        c.execute(schema.replace('"old_cameras"', 'cameras').replace(' AUTOINCREMENT', ''))
        c.execute('INSERT INTO cameras SELECT * FROM old_cameras')
        c.execute('DROP TABLE old_cameras')
        c.execute("INSERT INTO segments(camera_id,path,started_at,ended_at,size_bytes) VALUES(9,'missing','2026-01-01','2026-01-01',1)")
    (video.ROOT / 'default' / '12').mkdir(parents=True)
    main.init()
    main.init()  # Migration and sequence initialization are idempotent.
    with main.db() as c:
        assert c.execute('SELECT name FROM cameras WHERE id=2').fetchone()[0] == 'new'
        assert c.execute("INSERT INTO cameras(name,rtsp_url) VALUES('after','rtsp://cam/live')").lastrowid == 13
        c.execute("INSERT INTO layouts(name,payload) VALUES('default',?)", ('{"columns":1,"tiles":[{"camera_id":20,"x":0,"y":0,"w":1,"h":1}]}',))
    main.init()
    with main.db() as c:
        assert c.execute("INSERT INTO cameras(name,rtsp_url) VALUES('layout-safe','rtsp://cam/live')").lastrowid == 21


def test_unicode_auth_and_malformed_session(tmp_path, monkeypatch):
    main, video = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(main, 'MediaGateway', FakeGateway)
    monkeypatch.setattr(main, 'AUTH_USER', 'админ')
    monkeypatch.setattr(main, 'AUTH_PASS', 'пароль')
    with TestClient(main.app) as client:
        assert client.post('/api/login', json={'username': 'ошибка', 'password': 'пароль'}).status_code == 401
        assert client.post('/api/login', json={'username': 'админ', 'password': 'пароль'}).status_code == 200
        assert client.get('/api/cameras').status_code == 200
        client.cookies.clear()
        client.cookies.set('nvr_session', '999999999999.invalid')
        assert client.get('/api/cameras').status_code == 401
    import pytest
    request = main.Request({'type': 'http', 'headers': [(b'cookie', b'nvr_session=999999999999.\xff')]})
    with pytest.raises(main.HTTPException) as exc:
        asyncio.run(main.auth(request, None))
    assert exc.value.status_code == 401


def test_layout_rejects_duplicate_and_nonpositive_camera_ids(tmp_path, monkeypatch):
    main, _ = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(main, 'MediaGateway', FakeGateway)
    tile = {'camera_id': 1, 'x': 0, 'y': 0, 'w': 1, 'h': 1}
    with TestClient(main.app) as client:
        for tiles in ([tile, tile], [{**tile, 'camera_id': 0}]):
            assert client.put('/api/layout', auth=('admin', 'secret'), json={'columns': 2, 'tiles': tiles}).status_code == 422


def test_camera_update_preserves_secret_and_status_preserves_recorder_error(tmp_path, monkeypatch):
    main, _ = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(main, 'MediaGateway', FakeGateway)
    payload = {'name': 'camera', 'rtsp_url': 'rtsp://cam/live', 'username': 'user', 'password': 'secret', 'recording_enabled': False}
    with TestClient(main.app) as client:
        client.post('/api/login', json={'username': 'admin', 'password': 'secret'})
        cid = client.post('/api/cameras', json=payload).json()['id']
        assert client.put(f'/api/cameras/{cid}', json={**payload, 'rtsp_url': '', 'password': ''}).status_code == 200
        assert main.one(cid)['password'] == 'secret'
        main.app.state.supervisor.errors[cid] = 'Storage free space below reserve'
        assert client.get(f'/api/cameras/{cid}/status').json()['last_error'] == 'Storage free space below reserve'
        assert client.get('/api/recordings?date=2026-1-1').status_code == 422


def test_gateway_failure_stops_existing_recorders(tmp_path, monkeypatch):
    main, video = setup(tmp_path, monkeypatch)
    class FailingGateway(FakeGateway):
        async def reconcile(self, rows):
            raise RuntimeError('unavailable')
    main.init()
    async def exercise():
        main.app.state.gateway = FailingGateway()
        sup = video.ProcessSupervisor(main.app.state.gateway)
        main.app.state.supervisor = sup
        main.app.state.reconcile_lock = asyncio.Lock()
        stopped = asyncio.Event()
        async def recorder():
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
        task = asyncio.create_task(recorder())
        await asyncio.sleep(0)
        sup.tasks[1] = ({'id': 1}, task)
        import pytest
        with pytest.raises(RuntimeError):
            await main.reconcile_state(main.app)
        assert stopped.is_set()
        assert not sup.tasks
    asyncio.run(exercise())


def test_cleanup_never_deletes_external_files(tmp_path, monkeypatch):
    main, video = setup(tmp_path, monkeypatch)
    main.init()
    video.ROOT.mkdir()
    external = tmp_path / 'external.mp4'
    external.write_bytes(b'private')
    linked = video.ROOT / 'linked'
    linked.symlink_to(tmp_path, target_is_directory=True)
    with main.db() as c:
        for path in (external, linked / 'external.mp4'):
            c.execute('INSERT INTO segments(camera_id,path,started_at,ended_at,size_bytes) VALUES(?,?,?,?,?)', (1, str(path), '2000-01-01T00:00:00+00:00', '2000-01-01T00:01:00+00:00', 7))
    video.cleanup([], set())
    assert external.read_bytes() == b'private'
    with main.db() as c:
        assert c.execute('SELECT COUNT(*) FROM segments').fetchone()[0] == 0


def test_index_rejects_symlink_escapes_and_invalid_durations(tmp_path, monkeypatch):
    main, video = setup(tmp_path, monkeypatch)
    main.init()
    root = video.ROOT / 'default' / '1'
    root.mkdir(parents=True)
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / '20260101T000000.mp4').write_bytes(b'x' * 2048)
    (root / 'linked').symlink_to(outside, target_is_directory=True)
    (root / '20260101T000001.mp4').write_bytes(b'x' * 2048)
    for duration in (b'nan', b'inf', b'1e300', b'-1'):
        monkeypatch.setattr(video.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess([], 0, stdout=duration))
        video.index_segments(1, root)
    with main.db() as c:
        assert c.execute('SELECT COUNT(*) FROM segments').fetchone()[0] == 0
    assert (outside / '20260101T000000.mp4').exists()


def test_cleanup_revisits_older_hours_after_storage_returns(tmp_path, monkeypatch):
    main, video = setup(tmp_path, monkeypatch)
    main.init()
    video.ROOT.mkdir()
    monkeypatch.setattr(video, 'MIN_FREE', 0)
    row = {'id': 1, 'recording_destination': 'default', 'retention_days': None}
    video.cleanup([row], set())
    folder = video.directory(row) / '2020/01/01/00'
    folder.mkdir(parents=True)
    (folder / '20200101T000000.mp4').write_bytes(b'x' * 2048)
    monkeypatch.setattr(video.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess([], 0, stdout=b'2'))
    video.cleanup([row], set())
    with main.db() as c:
        assert c.execute('SELECT COUNT(*) FROM segments').fetchone()[0] == 1


def test_index_does_not_hold_write_lock_while_probing(tmp_path, monkeypatch):
    main, video = setup(tmp_path, monkeypatch)
    main.init()
    root = video.ROOT / 'default/1'
    root.mkdir(parents=True)
    for second in (0, 1):
        (root / f'20260101T00000{second}.mp4').write_bytes(b'x' * 2048)
    def probe(*a, **k):
        with main.db() as c:
            c.execute("INSERT INTO cameras(name,rtsp_url) VALUES('concurrent','rtsp://cam/live')")
        return subprocess.CompletedProcess([], 0, stdout=b'2')
    monkeypatch.setattr(video.subprocess, 'run', probe)
    video.index_segments(1, root)
    with main.db() as c:
        assert c.execute('SELECT COUNT(*) FROM segments').fetchone()[0] == 2


def test_recording_http_ranges_and_path_protection(tmp_path, monkeypatch):
    main, video = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(main, 'MediaGateway', FakeGateway)
    with TestClient(main.app) as client:
        path = video.ROOT / 'sample.mp4'
        path.write_bytes(b'0123456789')
        with main.db() as c:
            sid = c.execute('INSERT INTO segments(camera_id,path,started_at,ended_at,size_bytes) VALUES(?,?,?,?,?)', (1, str(path), '2026-01-01', '2026-01-01', 10)).lastrowid
        url = f'/api/recordings/{sid}'
        assert client.get(url).status_code == 401
        response = client.get(url, auth=('admin', 'secret'), headers={'Range': 'bytes=2-5'})
        assert response.status_code == 206
        assert response.content == b'2345'
        assert client.get(url + '/download', auth=('admin', 'secret')).content == b'0123456789'
        assert client.delete(url, auth=('admin', 'secret')).status_code == 204
        assert client.get(url, auth=('admin', 'secret')).status_code == 404


def test_hour_rotation_kills_unresponsive_recorder_and_restarts(tmp_path, monkeypatch):
    main, video = setup(tmp_path, monkeypatch)
    main.init()
    monkeypatch.setattr(video, 'MIN_FREE', 0)
    async def exercise():
        sup = video.ProcessSupervisor(None)
        class Proc:
            returncode = None
            def __init__(self):
                self.stderr = asyncio.StreamReader()
                self.stderr.feed_eof()
                self.stdout = asyncio.StreamReader()
                self.stdout.feed_eof()
                self.signals = []
                self.killed = False
            async def wait(self):
                return self.returncode
            def send_signal(self, sig):
                self.signals.append(sig)
            def kill(self):
                self.killed = True
                self.returncode = -9
        proc = Proc()
        async def spawn(*args, **kwargs):
            assert '-timeout' in args
            return proc
        waits = 0
        async def wait_for(awaitable, timeout):
            nonlocal waits
            waits += 1
            awaitable.close()
            raise asyncio.TimeoutError
        async def sleep(delay):
            raise asyncio.CancelledError
        monkeypatch.setattr(video.asyncio, 'create_subprocess_exec', spawn)
        monkeypatch.setattr(video.asyncio, 'wait_for', wait_for)
        monkeypatch.setattr(video.asyncio, 'sleep', sleep)
        import pytest
        with pytest.raises(asyncio.CancelledError):
            await sup.run({'id': 1, 'recording_destination': 'default'})
        assert waits == 2
        assert proc.killed
        assert not sup.procs
        assert not sup.errors
    asyncio.run(exercise())


def test_cross_origin_writes_are_rejected_and_proxy_https_is_supported(tmp_path, monkeypatch):
    main, _ = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(main, 'MediaGateway', FakeGateway)
    credentials = {'username': 'admin', 'password': 'secret'}
    with TestClient(main.app) as client:
        assert client.post('/api/login', json=credentials, headers={'Origin': 'https://attacker.example'}).status_code == 403
        assert client.post('/api/login', json=credentials, headers={'Origin': 'null'}).status_code == 403
        assert client.post('/api/login', json=credentials, headers={'Origin': 'https://testserver'}).status_code == 200
        assert client.put('/api/layout', json={'columns': 2, 'tiles': []}, headers={'Origin': 'http://testserver'}).status_code == 200


def test_stop_returns_saved_state_during_gateway_outage(tmp_path, monkeypatch):
    main, _ = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(main, 'MediaGateway', FakeGateway)
    async def unavailable(rows):
        raise RuntimeError('unavailable')
    with TestClient(main.app) as client:
        client.post('/api/login', json={'username': 'admin', 'password': 'secret'})
        cid = client.post('/api/cameras', json={'name': 'cam', 'rtsp_url': 'rtsp://cam/live', 'recording_enabled': False}).json()['id']
        monkeypatch.setattr(main.app.state.gateway, 'reconcile', unavailable)
        response = client.post(f'/api/cameras/{cid}/stop')
        assert response.status_code == 200
        assert response.json()['enabled'] == 0


def test_reconciliation_serializes_gateway_and_recorder_updates(tmp_path, monkeypatch):
    main, _ = setup(tmp_path, monkeypatch)
    async def exercise():
        entered = asyncio.Event()
        release = asyncio.Event()
        events = []
        snapshots = iter(([{'id': 1, 'enabled': True, 'recording_enabled': False, 'recording_destination': 'default'}], [{'id': 2, 'enabled': True, 'recording_enabled': False, 'recording_destination': 'default'}]))
        monkeypatch.setattr(main, 'rows', lambda: next(snapshots))
        class Gateway:
            async def reconcile(self, rows):
                cid = rows[0]['id']
                events.append(('gateway', cid))
                if cid == 1:
                    entered.set()
                    await release.wait()
        class Supervisor:
            async def apply(self, rows, start=True):
                events.append(('start' if start else 'stop', rows[0]['id']))
        main.app.state.reconcile_lock = asyncio.Lock()
        main.app.state.gateway = Gateway()
        main.app.state.supervisor = Supervisor()
        first = asyncio.create_task(main.reconcile_state(main.app))
        await entered.wait()
        second = asyncio.create_task(main.reconcile_state(main.app))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(first, second)
        assert events == [('stop', 1), ('gateway', 1), ('start', 1), ('stop', 2), ('gateway', 2), ('start', 2)]
    asyncio.run(exercise())


def test_metadata_edits_do_not_interrupt_recording(tmp_path, monkeypatch):
    _, video = setup(tmp_path, monkeypatch)
    async def exercise():
        sup = video.ProcessSupervisor(None)
        async def run(row):
            await asyncio.Event().wait()
        monkeypatch.setattr(sup, 'run', run)
        row = {'id': 1, 'name': 'Old', 'enabled': True, 'recording_enabled': True, 'rtsp_url': 'rtsp://cam/live', 'username': None, 'password': None, 'recording_destination': 'default', 'retention_days': 7}
        await sup.apply([row])
        await asyncio.sleep(0)
        task = sup.tasks[1][1]
        await sup.apply([{**row, 'name': 'New', 'retention_days': 30}])
        assert sup.tasks[1][1] is task
        await sup.apply([{**row, 'rtsp_url': 'rtsp://cam/other'}])
        assert task.cancelled()
        assert sup.tasks[1][1] is not task
        await sup.stop()
    asyncio.run(exercise())


def test_probe_failures_do_not_delete_valid_footage(tmp_path, monkeypatch):
    import os
    main, video = setup(tmp_path, monkeypatch)
    main.init()
    root = video.ROOT / 'default/1'
    root.mkdir(parents=True)
    path = root / '20200101T000000.mp4'
    path.write_bytes(b'x' * 2048)
    os.utime(path, (1, 1))
    failures = [FileNotFoundError('ffprobe unavailable'), subprocess.TimeoutExpired('ffprobe', 10), subprocess.CalledProcessError(1, 'ffprobe', stderr=b'I/O error')]
    for error in failures:
        def fail(*args, **kwargs):
            raise error
        monkeypatch.setattr(video.subprocess, 'run', fail)
        video.index_segments(1, root)
        assert path.exists()
    def corrupt(*args, **kwargs):
        raise subprocess.CalledProcessError(1, 'ffprobe', stderr=b'moov atom not found')
    monkeypatch.setattr(video.subprocess, 'run', corrupt)
    video.index_segments(1, root)
    assert not path.exists()
