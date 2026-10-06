import asyncio
import json
from datetime import datetime

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from test_nvr import setup, FakeGateway
from policies import RecordingSchedule, recording_expected, schedule_active
from storage import StorageMonitor, probe


def schedule(days=(0,), start='22:00', end='06:00', zone='Europe/Moscow'):
    return {'timezone': zone, 'windows': [{'days': list(days), 'start': start, 'end': end}]}


@pytest.mark.parametrize('stamp,active', [('2026-10-05T18:59:00Z', False), ('2026-10-05T19:00:00Z', True), ('2026-10-06T02:59:00Z', True), ('2026-10-06T03:00:00Z', False), ('2026-10-06T19:00:00Z', False)])
def test_overnight_schedule_boundaries(stamp, active):
    assert schedule_active(schedule(), datetime.fromisoformat(stamp), zone="Europe/Moscow") is active


@pytest.mark.parametrize('stamp', ['2026-11-01T05:30:00Z', '2026-11-01T06:30:00Z'])
def test_repeated_dst_hour_is_active_twice(stamp):
    assert schedule_active(schedule((6,), '01:00', '02:00', 'America/New_York'), datetime.fromisoformat(stamp), zone='America/New_York')


def test_spring_dst_and_sunday_rollover():
    assert not schedule_active(schedule((6,), '02:00', '03:00', 'America/New_York'), datetime.fromisoformat('2026-03-08T07:15:00Z'), zone='America/New_York')
    assert schedule_active(schedule((6,), '22:00', '06:00', 'UTC'), datetime.fromisoformat('2026-10-05T01:00:00Z'), zone='UTC')
    row = {'enabled': True, 'recording_enabled': False, 'recording_schedule': None}
    assert not recording_expected(row)
    assert schedule_active(None)


@pytest.mark.parametrize('value', [schedule((7,)), schedule((True,)), schedule((0,0)), schedule(start='22:00', end='22:00'), schedule(start='24:00'), schedule(zone='Missing/Zone'), {'timezone':'UTC','windows':[]}])
def test_schedule_validation(value):
    with pytest.raises(ValidationError):
        RecordingSchedule.model_validate(value)


def test_full_day_schedule():
    value = RecordingSchedule.model_validate(schedule(range(7), '00:00', '24:00', 'UTC')).model_dump()
    assert schedule_active(value, datetime.fromisoformat('2026-10-05T23:59:59Z'))


def test_schedule_migration_api_and_legacy_updates(tmp_path, monkeypatch):
    main, _ = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(main, 'MediaGateway', FakeGateway)
    with TestClient(main.app) as client:
        credentials = ('admin','secret')
        payload = {'name':'cam','rtsp_url':'rtsp://cam/live','recording_enabled':False,'recording_schedule':schedule()}
        created = client.post('/api/cameras', auth=credentials, json=payload)
        assert created.status_code == 201, created.text
        cid = created.json()['id']
        assert created.json()['recording_schedule'] == schedule()
        del payload['recording_schedule']
        assert client.put(f'/api/cameras/{cid}', auth=credentials, json=payload).json()['recording_schedule'] == schedule()
        assert client.put(f'/api/cameras/{cid}', auth=credentials, json={**payload,'recording_schedule':None}).json()['recording_schedule'] is None
    main.init()


def test_protected_storage_missing_identity_never_creates_directory(tmp_path):
    assert probe(tmp_path, 'nas', 'expected')['reason'] == 'identity_missing'
    assert not (tmp_path / 'nas').exists()


def test_storage_identity_writability_and_prepare(tmp_path):
    nas = tmp_path / 'nas'
    nas.mkdir()
    marker = nas / '.nvr-storage-id'
    marker.write_text('wrong')
    assert not probe(tmp_path, 'nas', 'expected')['available']
    assert not (nas / '1').exists()
    marker.write_text('expected\n')
    result = probe(tmp_path, 'nas', 'expected', 1, '2026/10/06/00')
    assert result['available'] and result['writable']
    assert (nas / '1/2026/10/06/00').is_dir()
    assert not list(nas.glob('.nvr-check-*'))
    assert not probe(tmp_path, '../outside')['available']
    outside = tmp_path.parent / (tmp_path.name+'-external')
    outside.mkdir()
    (tmp_path/'linked').symlink_to(outside, target_is_directory=True)
    assert probe(tmp_path,'linked')['reason']=='unsafe_path'


def test_storage_monitor_recovers_and_exposes_reserve(tmp_path):
    async def exercise():
        monitor = StorageMonitor(tmp_path,0)
        try:
            await monitor.refresh({'nas'}, {'nas':'disk1'})
            assert not monitor.status('nas')['ready']
            (tmp_path/'nas').mkdir()
            (tmp_path/'nas/.nvr-storage-id').write_text('disk1')
            await monitor.refresh({'nas'}, {'nas':'disk1'})
            assert monitor.status('nas')['ready']
            monitor.minimum_free=10**30
            await monitor.refresh({'nas'}, {'nas':'disk1'})
            assert monitor.status('nas')['reason']=='low_space'
            (tmp_path/'nas/.nvr-storage-id').unlink()
            assert not await monitor.prepare({'id':1,'recording_destination':'nas'},'2026/10/06/00')
        finally:
            await monitor.close()
    asyncio.run(exercise())


def test_cleanup_preserves_unavailable_destination_metadata(tmp_path, monkeypatch):
    main, video = setup(tmp_path, monkeypatch)
    main.init()
    video.ROOT.mkdir()
    path = video.ROOT/'nas/1/2020/01/01/00/20200101T000000.mp4'
    with main.db() as c:
        c.execute('INSERT INTO segments(camera_id,path,started_at,ended_at,size_bytes) VALUES(?,?,?,?,?)',(1,str(path),'2020-01-01T00:00:00+00:00','2020-01-01T00:01:00+00:00',1000))
    video.cleanup([],set(),{'nas'})
    with main.db() as c:
        assert c.execute('SELECT COUNT(*) FROM segments').fetchone()[0]==1


def test_health_requires_real_progress_and_suppresses_intentional_pauses(tmp_path, monkeypatch):
    _, video = setup(tmp_path, monkeypatch)
    sup = video.ProcessSupervisor(None)
    class Proc:
        returncode=None
    sup.procs[1]=Proc()
    sup.progress[1]={'started_tick':100,'last_tick':None,'last_progress_at':None}
    assert sup.health(1,True,now=110)['recording_health']=='STARTING'
    assert sup.health(1,True,now=131)['recording_health']=='STALLED'
    sup.progress[1]['last_tick']=130
    assert sup.health(1,True,now=131)['recording_health']=='WRITING'
    assert sup.health(1,False,'SCHEDULED_PAUSE',now=131)['recording_health']=='SCHEDULED_PAUSE'
    sup.procs[1].returncode=1
    assert sup.health(1,True,now=131)['recording_health']=='RECONNECTING'


def test_progress_parser_clears_error_only_on_advance(tmp_path, monkeypatch):
    _, video = setup(tmp_path, monkeypatch)
    async def exercise():
        sup=video.ProcessSupervisor(None)
        class Proc:
            stdout=asyncio.StreamReader()
        proc=Proc()
        proc.stdout.feed_data(b'frame=2\nout_time_us=100000\nprogress=continue\n')
        proc.stdout.feed_eof()
        sup.progress[1]={'frames':0,'out_time_us':0,'last_tick':None,'last_progress_at':None}
        sup.errors[1]='Recorder error'
        await sup.drain_progress(1,proc)
        assert sup.progress[1]['frames']==2
        assert sup.progress[1]['last_tick'] is not None
        assert 1 not in sup.errors
    asyncio.run(exercise())


def test_alert_debounce_cooldown_recovery_and_redaction(tmp_path, monkeypatch):
    main, _ = setup(tmp_path,monkeypatch)
    main.init()
    monkeypatch.setenv('WEBHOOK_URL','https://example.invalid/private?token=secret')
    monkeypatch.setenv('WEBHOOK_DEBOUNCE_SECONDS','10')
    monkeypatch.setenv('WEBHOOK_COOLDOWN_SECONDS','30')
    hooks=main.Webhooks()
    failure={'kind':'recording','camera_id':1,'destination':'default','reason':'stalled','expected':True,'failing':True,'rtsp_url':'rtsp://secret','password':'secret'}
    hooks.observe({'camera:1':failure},1000)
    hooks.observe({'camera:1':failure},1009)
    assert hooks.status()['pending']==0
    hooks.observe({'camera:1':failure},1010)
    hooks.observe({'camera:1':failure},1011)
    assert hooks.status()['pending']==1
    hooks.observe({'camera:1':{**failure,'failing':False,'reason':'writing'}},1012)
    hooks.observe({'camera:1':failure},1013)
    hooks.observe({'camera:1':failure},1023)
    assert hooks.status()['pending']==2
    hooks.observe({'camera:1':failure},1040)
    assert hooks.status()['pending']==3
    with main.db() as c:
        payloads=[json.loads(r[0]) for r in c.execute('SELECT payload FROM webhook_events ORDER BY created_at')]
    assert [p['state'] for p in payloads]==['failure','recovery','failure']
    assert 'secret' not in json.dumps(payloads)
    asyncio.run(hooks.close())


def test_webhook_bounded_retry_persistent_outbox_and_bearer(tmp_path,monkeypatch):
    main,_=setup(tmp_path,monkeypatch)
    main.init()
    monkeypatch.setenv('WEBHOOK_URL','http://receiver.invalid/events')
    monkeypatch.setenv('WEBHOOK_TOKEN','private-token')
    monkeypatch.setenv('WEBHOOK_DEBOUNCE_SECONDS','0')
    async def exercise():
        hooks=main.Webhooks()
        hooks.observe({'storage:nas':{'kind':'storage','destination':'nas','reason':'identity_missing','expected':True,'failing':True}},1000)
        await hooks.close()
        hooks=main.Webhooks()  # Outbox survives worker/application recreation.
        requests=[]
        def handler(request):
            requests.append(request)
            return httpx.Response(503)
        await hooks.client.aclose()
        hooks.client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        for stamp in (1000,1010,1030):
            await hooks.deliver_one(stamp)
        assert len(requests)==3
        assert hooks.status()['failed']==1
        await hooks.deliver_one(1100)
        assert len(requests)==3
        assert all(r.headers['Authorization']=='Bearer private-token' for r in requests)
        assert len({r.headers['X-NVR-Event-ID'] for r in requests})==1
        await hooks.close()
    asyncio.run(exercise())


def test_intentional_pause_resolves_instead_of_claiming_recovery(tmp_path,monkeypatch):
    main,_=setup(tmp_path,monkeypatch)
    main.init()
    monkeypatch.setenv('WEBHOOK_URL','http://receiver.invalid')
    monkeypatch.setenv('WEBHOOK_DEBOUNCE_SECONDS','0')
    hooks=main.Webhooks()
    failure={'kind':'recording','camera_id':1,'reason':'stalled','failing':True,'expected':True}
    hooks.observe({'camera:1':failure},1000)
    hooks.observe({'camera:1':{**failure,'failing':False,'expected':False,'reason':'scheduled_pause'}},1001)
    with main.db() as c:
        assert json.loads(c.execute('SELECT payload FROM webhook_events ORDER BY created_at DESC LIMIT 1').fetchone()[0])['state']=='resolved'
    asyncio.run(hooks.close())


def insert_segment(main,cid,start,end):
    with main.db() as c:
        return c.execute('INSERT INTO segments(camera_id,path,started_at,ended_at,size_bytes) VALUES(?,?,?,?,?)',(cid,f'/missing/{cid}-{start}.mp4',start,end,2000)).lastrowid


def test_timeline_seeking_and_cross_midnight_neighbors(tmp_path,monkeypatch):
    main,_=setup(tmp_path,monkeypatch)
    monkeypatch.setattr(main,'MediaGateway',FakeGateway)
    # This API test deliberately uses metadata without real MP4 files.
    # Retention's removal of missing files is exercised separately.
    monkeypatch.setattr(main, 'cleanup', lambda *args: None)
    with TestClient(main.app) as client:
        auth=('admin','secret')
        first=insert_segment(main,1,'2026-10-05T23:50:00+00:00','2026-10-06T00:00:00+00:00')
        second=insert_segment(main,1,'2026-10-06T00:02:00+00:00','2026-10-06T00:12:00+00:00')
        insert_segment(main,2,'2026-10-06T00:00:00+00:00','2026-10-06T00:20:00+00:00')
        response=client.get('/api/recordings/timeline',auth=auth,params={'camera_id':1,'start':'2026-10-05T23:45:00Z','end':'2026-10-06T00:15:00Z'})
        assert response.status_code==200,response.text
        value=response.json()
        assert len(value['intervals'])==2
        assert len(value['gaps'])==3
        response=client.get('/api/recordings/at',auth=auth,params={'camera_id':1,'time':'2026-10-06T03:05:00+03:00'})
        assert response.json()['segment']['id']==second
        assert response.json()['seek_seconds']==180
        assert client.get('/api/recordings/at',auth=auth,params={'camera_id':1,'time':'2026-10-06T00:01:00Z'}).status_code==404
        nxt=client.get(f'/api/recordings/{first}/adjacent',auth=auth).json()
        assert nxt['segment']['id']==second
        assert nxt['gap_seconds']==120
        assert client.get(f'/api/recordings/{second}/adjacent',auth=auth).json()['segment'] is None
        assert client.get(f'/api/recordings/{second}/adjacent?direction=previous',auth=auth).json()['segment']['id']==first
        assert client.get('/api/recordings/timeline',auth=auth,params={'camera_id':1,'start':'2026-10-05T00:00:00','end':'2026-10-06T00:00:00Z'}).status_code==422
        assert client.get(f'/api/recordings/{first}/adjacent').status_code==401


def test_timeline_merges_overlap_and_adjacent_seeks_past_overlap(tmp_path,monkeypatch):
    main,_=setup(tmp_path,monkeypatch)
    main.init()
    first=insert_segment(main,1,'2026-10-06T00:00:00+00:00','2026-10-06T00:10:00+00:00')
    insert_segment(main,1,'2026-10-06T00:09:00+00:00','2026-10-06T00:20:00+00:00')
    value=main.timeline(1,datetime.fromisoformat('2026-10-06T00:00:00Z'),datetime.fromisoformat('2026-10-06T00:30:00Z'))
    assert len(value['intervals'])==1
    assert main.adjacent(first)['seek_seconds']==60


def test_destination_api_and_metrics(tmp_path,monkeypatch):
    main,_=setup(tmp_path,monkeypatch)
    monkeypatch.setattr(main,'MediaGateway',FakeGateway)
    with TestClient(main.app) as client:
        auth=('admin','secret')
        response=client.put('/api/storage/destinations/nas',auth=auth,json={'expected_marker':'disk1'})
        assert response.status_code==200,response.text
        assert response.json()['ready'] is False
        assert not (main.ROOT/'nas').exists()
        (main.ROOT/'nas').mkdir()
        (main.ROOT/'nas/.nvr-storage-id').write_text('disk1')
        assert client.put('/api/storage/destinations/nas',auth=auth,json={'expected_marker':'disk1'}).json()['ready']
        assert 'storage_destination_ready{destination="nas"} 1' in client.get('/metrics',auth=auth).text
        assert client.get('/api/notifications/status',auth=auth).json()['enabled'] is False
        assert client.put('/api/storage/destinations/nas',auth=auth,json={'expected_marker':'../escape'}).status_code==422


def test_storage_probe_spawn_failure_fails_closed(tmp_path, monkeypatch):
    async def cannot_spawn(*args, **kwargs):
        raise OSError('process limit')
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', cannot_spawn)
    async def exercise():
        monitor = StorageMonitor(tmp_path, 0)
        await monitor.refresh({'nas'}, {'nas': 'disk'})
        assert not monitor.status('nas')['ready']
        assert monitor.status('nas')['reason'] == 'check_failed'
        assert not await monitor.available('nas')
        await monitor.close()
    asyncio.run(exercise())


def test_storage_probe_timeout_does_not_accumulate_processes(tmp_path, monkeypatch):
    class StuckProcess:
        returncode = None
        killed = False
        async def communicate(self):
            await asyncio.sleep(10)
        def kill(self):
            self.killed = True
        async def wait(self):
            await asyncio.sleep(10)
    process = StuckProcess()
    starts = []
    async def spawn(*args, **kwargs):
        starts.append(args)
        return process
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    async def exercise():
        monitor = StorageMonitor(tmp_path, 0, timeout=.01)
        await monitor.refresh({'nas'}, {'nas': 'disk'})
        await monitor.refresh({'nas'}, {'nas': 'disk'})
        assert process.killed
        assert len(starts) == 1
        assert not monitor.status('nas')['ready']
        await monitor.close()
    asyncio.run(exercise())


def test_invalid_marker_encoding_is_unavailable(tmp_path):
    (tmp_path / 'nas').mkdir()
    (tmp_path / 'nas' / '.nvr-storage-id').write_bytes(b'\xff')
    assert not probe(tmp_path, 'nas', 'disk')['available']


def test_storage_alert_suppresses_duplicate_camera_and_schedule_pauses(tmp_path, monkeypatch):
    main, video = setup(tmp_path, monkeypatch)
    monitor = StorageMonitor(tmp_path, 0)
    row = {'id': 1, 'enabled': True, 'recording_enabled': True, 'recording_schedule': None, 'recording_destination': 'nas'}
    monitor.statuses['nas'] = {'_tick': 0}  # Missing/stale state is not ready.
    observations = main.health_observations([row], video.ProcessSupervisor(None), monitor)
    assert observations['storage:nas']['failing']
    assert not observations['camera:1']['failing']
    row['recording_enabled'] = False
    observations = main.health_observations([row], video.ProcessSupervisor(None), monitor)
    assert not any(o['failing'] for o in observations.values())


def test_successful_webhook_delivery_preserves_failure_recovery_order(tmp_path, monkeypatch):
    main, _ = setup(tmp_path, monkeypatch)
    main.init()
    monkeypatch.setenv('WEBHOOK_URL', 'http://receiver.invalid')
    monkeypatch.setenv('WEBHOOK_DEBOUNCE_SECONDS', '0')
    async def exercise():
        hooks = main.Webhooks()
        failure = {'kind': 'recording', 'camera_id': 1, 'reason': 'stalled', 'expected': True, 'failing': True}
        hooks.observe({'camera:1': failure}, 1000)
        hooks.observe({'camera:1': {**failure, 'failing': False}}, 1001)
        sent = []
        def receiver(request):
            sent.append(json.loads(request.content))
            return httpx.Response(503 if len(sent) == 1 else 204)
        await hooks.client.aclose()
        hooks.client = httpx.AsyncClient(transport=httpx.MockTransport(receiver))
        await hooks.deliver_one(1001)
        await hooks.deliver_one(1002)
        assert len(sent) == 1  # Recovery waits for the failure retry.
        await hooks.deliver_one(1011)
        await hooks.deliver_one(1012)
        assert [p['state'] for p in sent] == ['failure', 'failure', 'recovery']
        assert sent[0]['id'] == sent[1]['id']
        assert hooks.status()['pending'] == hooks.status()['failed'] == 0
        await hooks.close()
    asyncio.run(exercise())


def test_active_diagnostics_share_probe_hide_secrets_and_clean_timeout(tmp_path, monkeypatch):
    _, video = setup(tmp_path, monkeypatch)
    processes = []
    class Process:
        returncode = None
        killed = False
        async def communicate(self):
            await asyncio.sleep(.01)
            if len(processes) > 1:
                raise asyncio.TimeoutError()
            self.returncode = 0
            return b'{"streams":[{"codec_type":"video","codec_name":"h264","width":640,"height":480,"avg_frame_rate":"25/1"}]}', b''
        def kill(self):
            self.killed = True
        async def wait(self):
            self.returncode = -9
    arguments = []
    async def spawn(*args, **kwargs):
        arguments.append(args)
        process = Process()
        processes.append(process)
        return process
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    async def exercise():
        gateway = video.MediaGateway()
        calls = []
        async def call(method, path, body=None):
            calls.append((method, path))
            return {}
        gateway.call = call
        row = {'id': 1, 'rtsp_url': 'rtsp://private-user:private-password@camera/live?private-key=1', 'username': None, 'password': None}
        results = await asyncio.gather(gateway.check(row), gateway.check(row))
        assert len(processes) == 1
        assert results[0] == results[1]
        assert results[0]['ok'] and results[0]['video']['width'] == 640
        assert 'private' not in json.dumps(results)
        assert arguments[0][-1] == row['rtsp_url']
        assert arguments[0][arguments[0].index('-v')+1] == 'quiet'
        gateway.connectivity[1]['_tick'] -= 11
        failed = await gateway.check(row)
        assert not failed['ok'] and failed['video'] is None
        assert processes[-1].killed
        assert not gateway.check_tasks
        assert calls == []  # Diagnostics no longer activate MediaMTX paths.
        await gateway.close()
    asyncio.run(exercise())


def test_changed_camera_invalidates_inflight_diagnostic(tmp_path, monkeypatch):
    main, video = setup(tmp_path, monkeypatch)
    async def exercise():
        gateway = video.MediaGateway()
        finished = asyncio.Event()
        async def old_probe():
            try:
                await asyncio.sleep(60)
            finally:
                finished.set()
        gateway.check_tasks[1] = asyncio.create_task(old_probe())
        gateway.checks[1] = {'ok': True}
        await asyncio.sleep(0)
        await main.invalidate_diagnostics(gateway, 1)
        assert finished.is_set()
        assert 1 not in gateway.checks and 1 not in gateway.check_tasks
        await gateway.close()
    asyncio.run(exercise())


def test_dashboard_does_not_count_stalled_or_paused_processes_as_writing(tmp_path, monkeypatch):
    import time
    from types import SimpleNamespace
    main, video = setup(tmp_path, monkeypatch)
    main.init()
    main.ROOT.mkdir()
    with main.db() as c:
        cid = c.execute('INSERT INTO cameras(name,rtsp_url) VALUES(?,?)', ('cam', 'rtsp://cam/live')).lastrowid
    sup = video.ProcessSupervisor(None)
    sup.procs[cid] = SimpleNamespace(returncode=None)
    sup.progress[cid] = {'started_tick': time.monotonic()-90, 'last_tick': time.monotonic()-60}
    main.app.state.supervisor = sup
    main.app.state.gateway = FakeGateway()
    main.app.state.storage = None
    request = SimpleNamespace(app=main.app)
    result = asyncio.run(main.dashboard(request))
    assert result['recording'] == 1 and result['writing'] == 0
    sup.progress[cid]['last_tick'] = time.monotonic()
    assert asyncio.run(main.dashboard(request))['writing'] == 1
    with main.db() as c:
        c.execute('UPDATE cameras SET recording_enabled=0 WHERE id=?', (cid,))
    assert asyncio.run(main.dashboard(request))['writing'] == 0


def test_shared_schedule_transition_stops_recorders_together(tmp_path, monkeypatch):
    _, video = setup(tmp_path, monkeypatch)
    async def exercise():
        supervisor = video.ProcessSupervisor(None)
        cancelled = set()
        both_stopping = asyncio.Event()
        async def recorder(cid):
            try:
                await asyncio.sleep(60)
            finally:
                cancelled.add(cid)
                if len(cancelled) == 2:
                    both_stopping.set()
                await both_stopping.wait()
        for cid in (1, 2):
            supervisor.tasks[cid] = ({'id': cid}, asyncio.create_task(recorder(cid)))
        await asyncio.sleep(0)
        await asyncio.wait_for(supervisor.apply([]), 1)
        assert cancelled == {1, 2}
        assert not supervisor.tasks
    asyncio.run(exercise())


def test_default_ui_language_is_english_and_public_before_login(tmp_path, monkeypatch):
    monkeypatch.delenv('DEFAULT_LANGUAGE', raising=False)
    main, _ = setup(tmp_path, monkeypatch)
    with TestClient(main.app) as client:
        assert client.get('/api/language').json() == {'default_language': 'en'}
        assert client.get('/api/config').status_code == 401


def test_default_ui_language_can_be_russian(tmp_path, monkeypatch):
    monkeypatch.setenv('DEFAULT_LANGUAGE', 'ru')
    main, _ = setup(tmp_path, monkeypatch)
    with TestClient(main.app) as client:
        assert client.get('/api/language').json() == {'default_language': 'ru'}


def test_invalid_default_ui_language_fails_clearly(tmp_path, monkeypatch):
    monkeypatch.setenv('DEFAULT_LANGUAGE', 'fr')
    with pytest.raises(RuntimeError, match='DEFAULT_LANGUAGE must be en or ru'):
        setup(tmp_path, monkeypatch)
