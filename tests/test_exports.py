"""Real FFmpeg exports, selection boundaries and retention races."""
import json
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from test_nvr import setup


@pytest.fixture
def export_app(tmp_path, monkeypatch):
    main, video = setup(tmp_path, monkeypatch)
    main.init()
    video.ROOT.mkdir()
    import exports
    jobs = exports.ExportJobs()
    monkeypatch.setattr(exports, 'jobs', jobs)
    client = TestClient(main.app)
    client.post('/api/login', json={'username': 'admin', 'password': 'secret'})
    yield main, video, client, jobs
    jobs.close()


def clip(main, video, second=0, duration=4, stamp=None, codec=None, size='64x64', camera_id=1):
    if codec is None:
        encoders = subprocess.check_output(['ffmpeg', '-hide_banner', '-encoders'], stderr=subprocess.DEVNULL)
        codec = 'libx264' if b'libx264' in encoders else 'libopenh264'
    start = stamp or datetime(2020, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=second)
    path = video.ROOT / 'default' / str(camera_id) / start.strftime('%Y/%m/%d/%H') / (start.strftime('%Y%m%dT%H%M%S') + '.mp4')
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', f'testsrc2=size={size}:rate=10',
                    '-t', str(duration), '-c:v', codec, '-g', '10', '-pix_fmt', 'yuv420p', str(path)], check=True)
    with main.db() as c:
        sid = c.execute('INSERT INTO segments(camera_id,path,started_at,ended_at,size_bytes) VALUES(?,?,?,?,?)',
                        (camera_id, str(path), start.isoformat(), (start + timedelta(seconds=duration)).isoformat(), path.stat().st_size)).lastrowid
    return sid, path


def begin(client, start='2020-01-01T00:00:01Z', end='2020-01-01T00:00:03Z', mode='exact'):
    return client.post('/api/recordings/exports', json={'camera_id': 1, 'start': start, 'end': end, 'mode': mode})


def finish(client, token):
    for _ in range(500):
        value = client.get('/api/recordings/exports/' + token).json()
        if value['state'] != 'processing':
            return value
        time.sleep(.01)
    raise AssertionError('Export did not finish')


def inspect_download(client, jobs, token, tmp_path):
    directory = jobs.jobs[token]['directory']
    response = client.get(f'/api/recordings/exports/{token}/download')
    assert response.status_code == 200, response.text
    assert response.headers['content-type'] == 'video/mp4'
    assert not directory.exists()
    assert client.get('/api/recordings/exports/' + token).status_code == 404
    downloaded = tmp_path / 'download.mp4'
    downloaded.write_bytes(response.content)
    data = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_name,width,height:format=duration', '-of', 'json', str(downloaded)]))
    # Decode the entire result, rather than only trusting container metadata.
    subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-i', str(downloaded), '-f', 'null', '-'], check=True, capture_output=True)
    return data


@pytest.mark.parametrize('mode', ['exact', 'copy'])
def test_single_segment(export_app, tmp_path, mode):
    main, video, client, jobs = export_app
    clip(main, video)
    response = begin(client, mode=mode)
    assert response.status_code == 202, response.text
    token = response.json()['token']
    assert finish(client, token)['state'] == 'ready'
    data = inspect_download(client, jobs, token, tmp_path)
    assert data['streams'][0]['codec_name'] == 'h264'
    assert abs(float(data['format']['duration']) - 2) < (.3 if mode == 'exact' else 1.5)


@pytest.mark.parametrize('gap', [0, 2])
def test_multiple_segments_and_gaps(export_app, tmp_path, gap):
    main, video, client, jobs = export_app
    clip(main, video)
    clip(main, video, second=4 + gap)
    token = begin(client, end=f'2020-01-01T00:00:{7+gap:02d}Z').json()['token']
    status = finish(client, token)
    assert status['state'] == 'ready', status
    assert len(status['gaps']) == (1 if gap else 0)
    data = inspect_download(client, jobs, token, tmp_path)
    assert abs(float(data['format']['duration']) - 6) < .3


def test_full_segments_preserve_original_codec(export_app, tmp_path):
    main, video, client, jobs = export_app
    clip(main, video, codec='mpeg4')
    token = begin(client, start='2020-01-01T00:00:00Z', end='2020-01-01T00:00:04Z').json()['token']
    status = finish(client, token)
    assert status['state'] == 'ready' and status['mode'] == 'copy'
    assert inspect_download(client, jobs, token, tmp_path)['streams'][0]['codec_name'] == 'mpeg4'


@pytest.mark.parametrize('damage', ['missing', 'damaged', 'unindexed', 'symlink', 'active'])
def test_unavailable_segments(export_app, tmp_path, damage):
    main, video, client, jobs = export_app
    sid, path = clip(main, video)
    if damage == 'missing':
        path.unlink()
    elif damage == 'damaged':
        path.write_bytes(b'broken video')
    elif damage == 'unindexed':
        with main.db() as c:
            c.execute('DELETE FROM segments WHERE id=?', (sid,))
    elif damage == 'symlink':
        path.unlink()
        target = tmp_path / 'outside.mp4'
        target.write_bytes(b'private')
        path.symlink_to(target)
    else:
        video.ACTIVE_DIRECTORIES.add(path.parent)
    response = begin(client)
    if damage == 'damaged':
        token = response.json()['token']
        assert finish(client, token)['state'] == 'failed'
        assert not jobs.jobs[token]['directory'].exists()
        assert not jobs.pins
    else:
        assert response.status_code == (409 if damage == 'active' else 404)
        assert not jobs.pins and not jobs.jobs


@pytest.mark.parametrize('start,end', [
    ('2020-01-01T00:00:03Z', '2020-01-01T00:00:01Z'),
    ('2020-01-01T00:00:01Z', '2020-01-01T00:00:01Z'),
    ('2020-01-01T00:00:01', '2020-01-01T00:00:03Z'),
    ('2020-01-01T00:00:01Z', '2020-03-01T00:00:03Z'),
    ('invalid', '2020-01-01T00:00:03Z'),
])
def test_invalid_ranges(export_app, start, end):
    _, _, client, jobs = export_app
    assert begin(client, start, end).status_code == 422
    assert not jobs.jobs


def test_retention_and_manual_cleanup_skip_pinned_export(export_app, monkeypatch):
    main, video, client, jobs = export_app
    sid, path = clip(main, video)
    # Other old footage remains eligible while this job is processing.
    other, other_path = clip(main, video, second=10)
    entered, release = threading.Event(), threading.Event()
    original = jobs.run

    def blocked(args, deadline):
        entered.set()
        assert release.wait(10)
        original(args, deadline)

    monkeypatch.setattr(jobs, 'run', blocked)
    token = begin(client).json()['token']
    try:
        assert entered.wait(3)
        assert client.delete(f'/api/recordings/{sid}').status_code == 409
        preview = client.post('/api/recordings/cleanup/preview', json={'recording_ids': [sid]}).json()
        client.post('/api/recordings/cleanup/delete', json={'token': preview['token'], 'confirmation': 'confirm'})
        for _ in range(100):
            progress = client.get('/api/recordings/cleanup/progress/' + preview['token']).json()
            if progress['state'] == 'done':
                break
            time.sleep(.01)
        assert progress['active'] == 1
        monkeypatch.setattr(video, 'index_segments', lambda *args: None)
        video.cleanup([], set())
        assert path.exists() and not other_path.exists()
        assert client.get(f'/api/recordings/{other}').status_code == 404
    finally:
        release.set()
    assert finish(client, token)['state'] == 'ready'
    for _ in range(100):
        if not jobs.pins:
            break
        time.sleep(.01)
    assert not jobs.pins
    assert client.delete(f'/api/recordings/{sid}').status_code == 204


def test_external_deletion_during_export_fails_and_unpins(export_app, monkeypatch):
    main, video, client, jobs = export_app
    _, path = clip(main, video)
    original = jobs.run

    def removed(args, deadline):
        path.unlink(missing_ok=True)
        original(args, deadline)

    monkeypatch.setattr(jobs, 'run', removed)
    token = begin(client).json()['token']
    assert finish(client, token)['state'] == 'failed'
    assert not jobs.pins and not jobs.jobs[token]['directory'].exists()


@pytest.mark.parametrize('start,request_start,request_end', [
    ('2020-01-01T23:59:58+00:00', '2020-01-02T02:59:59+03:00', '2020-01-02T03:00:01+03:00'),
    ('2026-11-01T05:59:58+00:00', '2026-11-01T01:59:59-04:00', '2026-11-01T01:00:01-05:00'),
    ('2026-03-08T06:59:58+00:00', '2026-03-08T01:59:59-05:00', '2026-03-08T03:00:01-04:00'),
])
def test_midnight_and_dst_absolute_boundaries(export_app, tmp_path, start, request_start, request_end):
    main, video, client, jobs = export_app
    clip(main, video, stamp=datetime.fromisoformat(start))
    token = begin(client, request_start, request_end).json()['token']
    assert finish(client, token)['state'] == 'ready'
    assert abs(float(inspect_download(client, jobs, token, tmp_path)['format']['duration']) - 2) < .3


def test_incompatible_resolution_and_expiry(export_app):
    main, video, client, jobs = export_app
    clip(main, video)
    clip(main, video, second=4, size='128x96')
    token = begin(client, end='2020-01-01T00:00:07Z').json()['token']
    assert finish(client, token)['state'] == 'failed'
    assert not jobs.pins
    jobs.jobs[token]['created'] -= 3601
    jobs.reap()
    assert client.get('/api/recordings/exports/' + token).status_code == 404


def test_auth_and_not_ready(export_app, monkeypatch):
    main, video, client, jobs = export_app
    clip(main, video)
    anonymous = TestClient(main.app)
    assert begin(anonymous).status_code == 401
    entered, release = threading.Event(), threading.Event()
    original = jobs.run

    def blocked(args, deadline):
        entered.set()
        assert release.wait(10)
        original(args, deadline)

    monkeypatch.setattr(jobs, 'run', blocked)
    token = begin(client).json()['token']
    try:
        assert entered.wait(3)
        assert anonymous.get('/api/recordings/exports/' + token).status_code == 401
        assert client.get('/api/recordings/exports/' + token + '/download').status_code == 409
    finally:
        release.set()
    assert finish(client, token)['state'] == 'ready'


def test_shutdown_stops_ffmpeg_and_removes_temporary_files(export_app, monkeypatch):
    main, video, client, jobs = export_app
    clip(main, video)
    original = jobs.run
    entered = threading.Event()

    def slow(args, deadline):
        # A real long-running child ensures shutdown exercises process termination.
        entered.set()
        original(['-re', '-f', 'lavfi', '-i', 'testsrc2=size=64x64:rate=1', '-t', '120', '-f', 'null', '-'], deadline)

    monkeypatch.setattr(jobs, 'run', slow)
    token = begin(client).json()['token']
    assert entered.wait(3)
    deadline = time.monotonic() + 3
    while not jobs.processes and time.monotonic() < deadline:
        time.sleep(.01)
    directory = jobs.jobs[token]['directory']
    jobs.close()
    assert not jobs.pins and not jobs.jobs and not jobs.processes and not directory.exists()
    assert begin(client).status_code == 503


def test_restart_removes_abandoned_exports(export_app):
    main, _, _, jobs = export_app
    from db import PATH
    directory = PATH.parent / 'exports' / 'job-abandoned'
    directory.mkdir(parents=True)
    (directory / 'partial.mp4').write_bytes(b'partial')
    jobs.startup()
    assert not directory.exists()


def test_overlaps_are_not_duplicated(export_app, tmp_path):
    main, video, client, jobs = export_app
    clip(main, video)
    clip(main, video, second=2)
    token = begin(client, end='2020-01-01T00:00:05Z').json()['token']
    status = finish(client, token)
    assert status['state'] == 'ready' and not status['gaps']
    assert abs(float(inspect_download(client, jobs, token, tmp_path)['format']['duration']) - 4) < .3


def test_concurrency_limit_and_shared_pins(export_app, monkeypatch):
    main, video, client, jobs = export_app
    _, path = clip(main, video)
    release = threading.Event()
    original = jobs.run

    def blocked(args, deadline):
        assert release.wait(10)
        original(args, deadline)

    monkeypatch.setattr(jobs, 'run', blocked)
    first = begin(client).json()['token']
    second = begin(client).json()['token']
    try:
        assert jobs.pins[path] == 2
        assert begin(client).status_code == 429
    finally:
        release.set()
    assert finish(client, first)['state'] == 'ready'
    assert finish(client, second)['state'] == 'ready'
    assert not jobs.pins


def test_missing_later_file_cannot_leak_partial_pins(export_app):
    main, video, client, jobs = export_app
    clip(main, video)
    _, path = clip(main, video, second=4)
    path.unlink()
    assert begin(client, end='2020-01-01T00:00:07Z').status_code == 404
    assert not jobs.pins and not jobs.jobs


def test_timeout_releases_pins_and_ready_expiry(export_app, monkeypatch):
    main, video, client, jobs = export_app
    clip(main, video)
    original = jobs.run

    def timeout(args, deadline):
        raise subprocess.TimeoutExpired('ffmpeg', 900)

    monkeypatch.setattr(jobs, 'run', timeout)
    token = begin(client).json()['token']
    assert finish(client, token)['state'] == 'failed'
    assert not jobs.pins
    monkeypatch.setattr(jobs, 'run', original)
    token = begin(client).json()['token']
    assert finish(client, token)['state'] == 'ready'
    directory = jobs.jobs[token]['directory']
    jobs.jobs[token]['created'] -= 3601
    jobs.reap()
    assert not directory.exists()
    assert client.get('/api/recordings/exports/' + token).status_code == 404


@pytest.mark.parametrize('second,end,duration', [(None,4,4),(4,8,8)])
def test_whole_h264_segments_do_not_add_timestamp_padding(export_app, tmp_path, second, end, duration):
    main, video, client, jobs = export_app
    clip(main, video)
    if second is not None:
        clip(main, video, second=second)
    token = begin(client, start='2020-01-01T00:00:00Z', end=f'2020-01-01T00:00:{end:02d}Z').json()['token']
    assert finish(client, token)['state'] == 'ready'
    assert abs(float(inspect_download(client, jobs, token, tmp_path)['format']['duration']) - duration) < .15


def test_stream_copy_rejects_corrupt_packet_payload(export_app):
    main, video, client, jobs = export_app
    _, path = clip(main, video)
    packet = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                                                '-show_packets', '-show_entries', 'packet=pos,size', '-of', 'json', str(path)]))['packets'][0]
    # Preserve container metadata and packet headers; damage the compressed frame.
    data = bytearray(path.read_bytes())
    position, size = int(packet['pos']), int(packet['size'])
    data[position+size//2:position+size] = b'\x00' * (size-size//2)
    path.write_bytes(data)
    token = begin(client, start='2020-01-01T00:00:00Z', end='2020-01-01T00:00:04Z', mode='copy').json()['token']
    assert finish(client, token)['state'] == 'failed'
    assert not jobs.pins and not jobs.jobs[token]['directory'].exists()


def camera_clip(main, video, cid, second=0, damage=False):
    sid, path = clip(main, video, second=second, camera_id=cid)
    if damage:
        path.write_bytes(b'broken')
    return sid, path


def multi(client, ids, mode='exact', end='2020-01-01T00:00:03Z'):
    return client.post('/api/recordings/exports', json={'camera_ids': ids,
                       'start': '2020-01-01T00:00:01Z', 'end': end, 'mode': mode})


@pytest.mark.parametrize('mode', ['exact', 'copy'])
def test_multi_camera_zip_manifest_and_independent_videos(export_app, tmp_path, mode):
    import io
    import zipfile
    main, video, client, jobs = export_app
    camera_clip(main, video, 1)
    camera_clip(main, video, 2)
    response = multi(client, [1, 2], mode)
    assert response.status_code == 202, response.text
    token = response.json()['token']
    status = finish(client, token)
    assert status['state'] == 'ready' and not status['partial']
    assert status['format'] == 'zip' and status['completed'] == status['total'] == 2
    directory = jobs.jobs[token]['directory']
    response = client.get(f'/api/recordings/exports/{token}/download')
    assert response.headers['content-type'] == 'application/zip'
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        assert manifest['start'] == '2020-01-01T00:00:01+00:00'
        assert manifest['end'] == '2020-01-01T00:00:03+00:00'
        assert manifest['mode'] == mode
        assert [c['camera_id'] for c in manifest['cameras']] == [1, 2]
        for camera in manifest['cameras']:
            assert camera['state'] == 'ready' and camera['gaps'] == []
            filename = camera['filename']
            assert '/' not in filename and '\\' not in filename and filename.endswith('.mp4')
            output = tmp_path / filename
            output.write_bytes(archive.read(filename))
            subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-i', str(output), '-f', 'null', '-'], check=True, capture_output=True)
    assert not jobs.pins and not directory.exists()


@pytest.mark.parametrize('failure', ['no-footage', 'damaged', 'missing'])
def test_multi_partial_failure_and_manifest_gaps(export_app, failure):
    import io
    import zipfile
    main, video, client, jobs = export_app
    camera_clip(main, video, 1)
    _, path = camera_clip(main, video, 2, second=6, damage=failure == 'damaged')
    if failure == 'no-footage':
        with main.db() as c:
            c.execute("INSERT INTO cameras(id,name,rtsp_url) VALUES(2,'Empty','rtsp://camera/live')")
            c.execute('DELETE FROM segments WHERE camera_id=2')
    elif failure == 'missing':
        path.unlink()
    token = multi(client, [1, 2], end='2020-01-01T00:00:09Z').json()['token']
    status = finish(client, token)
    assert status['state'] == 'ready' and status['partial']
    assert status['cameras'][1]['state'] == 'failed' and status['cameras'][1]['error']
    assert status['cameras'][0]['gaps'] == [{'start': '2020-01-01T00:00:04+00:00', 'end': '2020-01-01T00:00:09+00:00'}]
    response = client.get(f'/api/recordings/exports/{token}/download')
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        assert manifest['cameras'] == status['cameras']
        assert len(archive.namelist()) == 2
    assert not jobs.pins


@pytest.mark.parametrize('ids', [[], [1, 1], [1, 999], [0], [-1], list(range(1, 66))])
def test_invalid_camera_selection(export_app, ids):
    main, video, client, jobs = export_app
    clip(main, video)
    assert multi(client, ids).status_code == 422
    assert not jobs.pins and not jobs.jobs


def test_ambiguous_camera_request_and_all_empty(export_app):
    main, _, client, jobs = export_app
    body = {'start': '2020-01-01T00:00:01Z', 'end': '2020-01-01T00:00:03Z'}
    assert client.post('/api/recordings/exports', json=body).status_code == 422
    assert client.post('/api/recordings/exports', json={**body, 'camera_id': 1, 'camera_ids': [1]}).status_code == 422
    with main.db() as c:
        c.execute("INSERT INTO cameras(id,name,rtsp_url) VALUES(1,'One','rtsp://one/live'),(2,'Two','rtsp://two/live')")
    assert multi(client, [1, 2]).status_code == 404
    assert not jobs.jobs


def test_all_multi_camera_processing_failures_cleanup(export_app):
    main, video, client, jobs = export_app
    camera_clip(main, video, 1, damage=True)
    camera_clip(main, video, 2, damage=True)
    token = multi(client, [1, 2]).json()['token']
    assert finish(client, token)['state'] == 'failed'
    assert not jobs.pins and not jobs.jobs[token]['directory'].exists()


def test_multi_retention_pins_all_cameras_and_expiry(export_app, monkeypatch):
    main, video, client, jobs = export_app
    first, first_path = camera_clip(main, video, 1)
    second, second_path = camera_clip(main, video, 2)
    release, entered = threading.Event(), threading.Event()
    original = jobs.run
    def blocked(args, deadline):
        entered.set()
        assert release.wait(10)
        original(args, deadline)
    monkeypatch.setattr(jobs, 'run', blocked)
    token = multi(client, [1, 2]).json()['token']
    try:
        assert entered.wait(3)
        assert jobs.pins[first_path] == jobs.pins[second_path] == 1
        assert client.delete(f'/api/recordings/{first}').status_code == 409
        assert client.delete(f'/api/recordings/{second}').status_code == 409
        monkeypatch.setattr(video, 'index_segments', lambda *args: None)
        video.cleanup([], set())
        assert first_path.exists() and second_path.exists()
    finally:
        release.set()
    assert finish(client, token)['state'] == 'ready'
    assert not jobs.pins
    directory = jobs.jobs[token]['directory']
    jobs.jobs[token]['created'] -= 3601
    jobs.reap()
    assert not directory.exists() and not jobs.jobs


def test_insufficient_space_rejects_before_pinning(export_app, monkeypatch):
    from collections import namedtuple
    import exports
    main, video, client, jobs = export_app
    clip(main, video)
    usage = namedtuple('usage', 'total used free')
    monkeypatch.setattr(exports.shutil, 'disk_usage', lambda _: usage(100, 99, 1))
    assert begin(client).status_code == 507
    assert not jobs.pins and not jobs.jobs


@pytest.mark.parametrize('stamp,start,end', [
    ('2020-01-01T23:59:58+00:00', '2020-01-02T02:59:59+03:00', '2020-01-02T03:00:01+03:00'),
    ('2026-11-01T05:59:58+00:00', '2026-11-01T01:59:59-04:00', '2026-11-01T01:00:01-05:00'),
    ('2026-03-08T06:59:58+00:00', '2026-03-08T01:59:59-05:00', '2026-03-08T03:00:01-04:00'),
])
def test_multi_camera_midnight_dst_manifest(export_app, stamp, start, end):
    import io
    import zipfile
    main, video, client, jobs = export_app
    for cid in (1, 2):
        clip(main, video, stamp=datetime.fromisoformat(stamp), camera_id=cid)
    response = client.post('/api/recordings/exports', json={'camera_ids': [1, 2], 'start': start, 'end': end})
    assert response.status_code == 202
    token = response.json()['token']
    assert finish(client, token)['state'] == 'ready'
    response = client.get(f'/api/recordings/exports/{token}/download')
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        assert datetime.fromisoformat(manifest['start']) == datetime.fromisoformat(start)
        assert datetime.fromisoformat(manifest['end']) == datetime.fromisoformat(end)
        assert datetime.fromisoformat(manifest['end']) - datetime.fromisoformat(manifest['start']) == timedelta(seconds=2)
        assert all(camera['state'] == 'ready' for camera in manifest['cameras'])
    assert not jobs.pins


def test_zip_packaging_failure_cleans_files_and_pins(export_app, monkeypatch):
    import zipfile
    main, video, client, jobs = export_app
    for cid in (1, 2):
        camera_clip(main, video, cid)
    def unavailable(*args, **kwargs):
        raise OSError('Disk full')
    monkeypatch.setattr(zipfile.ZipFile, 'write', unavailable)
    token = multi(client, [1, 2]).json()['token']
    status = finish(client, token)
    assert status['state'] == 'failed' and status['error']
    assert not jobs.jobs[token]['directory'].exists() and not jobs.pins


def test_list_with_one_camera_keeps_mp4_workflow(export_app, tmp_path):
    main, video, client, jobs = export_app
    clip(main, video)
    token = multi(client, [1]).json()['token']
    status = finish(client, token)
    assert status['format'] == 'mp4' and status['state'] == 'ready'
    inspect_download(client, jobs, token, tmp_path)
