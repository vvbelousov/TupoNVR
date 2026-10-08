"""Cleanup API regression tests using real SQLite rows and temporary files."""
import threading
import time
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from test_nvr import setup


@pytest.fixture
def cleanup_app(tmp_path, monkeypatch):
    main, video = setup(tmp_path, monkeypatch)
    main.init()
    video.ROOT.mkdir()
    client = TestClient(main.app)
    client.post('/api/login', json={'username': 'admin', 'password': 'secret'})
    return main, video, client


def recording(main, video, camera=1, minute=0, hour='2020/01/01/10'):
    stamp = hour.replace('/', '')[:8] + 'T' + hour[-2:] + f'{minute:02d}00'
    correct = video.ROOT / 'default' / str(camera) / hour / (stamp + '.mp4')
    correct.parent.mkdir(parents=True, exist_ok=True)
    correct.write_bytes(b'video' * 256)
    start = datetime.strptime(stamp, '%Y%m%dT%H%M%S').replace(tzinfo=timezone.utc)
    from datetime import timedelta
    with main.db() as c:
        sid = c.execute('INSERT INTO segments(camera_id,path,started_at,ended_at,size_bytes) VALUES(?,?,?,?,?)',
                        (camera, str(correct), start.isoformat(), (start + timedelta(minutes=5)).isoformat(), correct.stat().st_size)).lastrowid
    return sid, correct


def finish(client, preview):
    response = client.post('/api/recordings/cleanup/delete', json={'token': preview['token'], 'confirmation': 'DELETE' if preview['clear_all'] else 'confirm'})
    assert response.status_code == 202, response.text
    for _ in range(200):
        progress = client.get('/api/recordings/cleanup/progress/' + preview['token']).json()
        if progress['state'] == 'done':
            return progress
        time.sleep(.01)
    raise AssertionError('Cleanup did not complete')


@pytest.mark.parametrize('criteria,expected', [
    ({}, [0, 1, 2]), ({'camera_ids': [1]}, [0, 1]), ({'camera_ids': [1, 2]}, [0, 1, 2]),
    ({'start': '2020-01-01T10:03:00Z', 'end': '2020-01-01T10:10:00Z'}, [0, 2]),
    ({'camera_ids': [1], 'start': '2020-01-01T10:03:00Z', 'end': '2020-01-01T10:10:00Z'}, [0]),
    ({'selected': [0]}, [0]), ({'selected': [0, 2]}, [0, 2]),
])
def test_scopes_preview_and_sqlite_consistency(cleanup_app, criteria, expected):
    main, video, client = cleanup_app
    files = [recording(main, video), recording(main, video, minute=10), recording(main, video, camera=2)]
    criteria = dict(criteria)
    if 'selected' in criteria:
        criteria['recording_ids'] = [files[index][0] for index in criteria.pop('selected')]
    response = client.post('/api/recordings/cleanup/preview', json=criteria)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview['count'] == len(expected)
    assert preview['size_bytes'] == len(expected) * 1280
    assert all(path.exists() for _, path in files), 'Preview must not delete'
    queried = client.post('/api/recordings/cleanup/query?limit=1', json=criteria).json()
    assert queried['total'] == len(expected) and len(queried['recordings']) == 1
    result = finish(client, preview)
    assert result['deleted'] == len(expected) and result['failed'] == 0
    assert result['reclaimed_bytes'] == 1280 * len(expected)
    with main.db() as c:
        remaining = {row['id'] for row in c.execute('SELECT * FROM segments')}
    assert remaining == {sid for index, (sid, _) in enumerate(files) if index not in expected}
    for index, (_, path) in enumerate(files):
        assert path.exists() == (index not in expected)
    assert len(client.get('/api/recordings').json()) == len(files) - len(expected)
    resolved = client.post('/api/recordings/resolve', json={'camera_ids': [1, 2], 'time': '2020-01-01T10:03:00Z'}).json()['cameras']
    assert (resolved['1']['segment'] is None) == (0 in expected)
    assert (resolved['2']['segment'] is None) == (2 in expected)
    timelines = client.post('/api/recordings/timelines', json={'camera_ids': [1, 2], 'start': '2020-01-01T10:00:00Z', 'end': '2020-01-01T11:00:00Z'}).json()['cameras']
    assert len(timelines['1']['intervals']) == sum(index not in expected for index in (0, 1))
    assert len(timelines['2']['intervals']) == (2 not in expected)

    assert finish(client, preview) == result, 'Repeated execution is idempotent'


def test_active_and_races(cleanup_app):
    main, video, client = cleanup_app
    sid, path = recording(main, video)
    preview = client.post('/api/recordings/cleanup/preview', json={'recording_ids': [sid]}).json()
    video.ACTIVE_DIRECTORIES.add(path.parent)
    assert client.delete(f'/api/recordings/{sid}').status_code == 409
    assert finish(client, preview)['active'] == 1
    assert path.exists()
    active_preview = client.post('/api/recordings/cleanup/preview', json={}).json()
    assert active_preview['count'] == 0 and active_preview['active_excluded'] == 1
    video.ACTIVE_DIRECTORIES.clear()
    preview = client.post('/api/recordings/cleanup/preview', json={}).json()
    _, new = recording(main, video, minute=10)
    assert finish(client, preview)['deleted'] == 1
    assert new.exists(), 'Preview snapshot cannot expand to new recordings'


@pytest.mark.parametrize('mode', ['missing_marker', 'wrong_marker', 'symlink_marker', 'missing_mount', 'missing_root', 'destination_symlink', 'parent_symlink', 'file_symlink', 'traversal', 'unknown'])
def test_mount_and_path_safety(cleanup_app, tmp_path, mode):
    main, video, client = cleanup_app
    sid, path = recording(main, video)
    external = tmp_path / 'outside'
    external.mkdir()
    sentinel = external / path.name
    sentinel.write_bytes(b'private')
    marker = video.ROOT / 'default' / '.nvr-storage-id'
    if 'marker' in mode or mode == 'missing_mount':
        with main.db() as c:
            c.execute("INSERT INTO destinations VALUES('default','expected')")
    if mode == 'wrong_marker':
        marker.write_text('wrong')
    elif mode == 'symlink_marker':
        marker.symlink_to(sentinel)
    elif mode == 'missing_mount':
        (video.ROOT / 'default').rename(video.ROOT / 'disconnected')
    elif mode == 'missing_root':
        video.ROOT.rename(video.ROOT.with_name('disconnected'))
    elif mode == 'destination_symlink':
        (video.ROOT / 'default').rename(video.ROOT / 'old')
        (video.ROOT / 'default').symlink_to(external, target_is_directory=True)
    elif mode == 'parent_symlink':
        path.parent.rename(path.parent.with_name('old'))
        path.parent.symlink_to(external, target_is_directory=True)
    elif mode == 'file_symlink':
        path.unlink()
        path.symlink_to(sentinel)
    elif mode in ('traversal', 'unknown'):
        unsafe = str(video.ROOT / 'default' / '..' / '..' / 'outside' / path.name) if mode == 'traversal' else str(marker)
        marker.write_text('expected')
        with main.db() as c:
            c.execute('UPDATE segments SET path=? WHERE id=?', (unsafe, sid))
    result = finish(client, client.post('/api/recordings/cleanup/preview', json={}).json())
    assert result['failed'] == 1 and result['deleted'] == 0
    assert sentinel.read_bytes() == b'private'
    with main.db() as c:
        assert c.execute('SELECT COUNT(*) FROM segments').fetchone()[0] == 1


def test_protected_mount_missing_file_and_partial_failure(cleanup_app, monkeypatch):
    main, video, client = cleanup_app
    files = [recording(main, video, minute=minute) for minute in (0, 10, 20)]
    marker = video.ROOT / 'default' / '.nvr-storage-id'
    marker.write_text('expected')
    with main.db() as c:
        c.execute("INSERT INTO destinations VALUES('default','expected')")
    files[1][1].unlink()
    import os
    unlink = os.unlink
    def fail_one(path, *args, **kwargs):
        if str(path) == files[2][1].name:
            raise PermissionError('read-only')
        return unlink(path, *args, **kwargs)
    monkeypatch.setattr(os, 'unlink', fail_one)
    result = finish(client, client.post('/api/recordings/cleanup/preview', json={}).json())
    assert (result['deleted'], result['missing'], result['failed'], result['reclaimed_bytes']) == (1, 1, 1, 1280)
    assert marker.read_text() == 'expected'
    with main.db() as c:
        assert [row['id'] for row in c.execute('SELECT id FROM segments')] == [files[2][0]]


def test_validation_auth_confirmation_and_invalid_ids(cleanup_app):
    main, video, client = cleanup_app
    recording(main, video)
    for criteria in ({'recording_ids': [-1]}, {'recording_ids': []}, {'camera_ids': [0]}, {'path': '/tmp/foo'}, {'start': '2020-01-01T00:00:00Z'}, {'start': '2020-01-01', 'end': '2021-01-01'}, {'start': '2021-01-01T00:00:00Z', 'end': '2020-01-01T00:00:00Z'}):
        assert client.post('/api/recordings/cleanup/preview', json=criteria).status_code == 422
    preview = client.post('/api/recordings/cleanup/preview', json={}).json()
    assert client.post('/api/recordings/cleanup/delete', json={'token': preview['token'], 'confirmation': 'confirm'}).status_code == 422
    assert client.post('/api/recordings/cleanup/delete', json={'token': 'invalid', 'confirmation': 'DELETE'}).status_code == 410
    preview = client.post('/api/recordings/cleanup/preview', json={'recording_ids': [999]}).json()
    assert preview['count'] == 0
    assert preview['not_found'] == 1
    assert finish(client, preview)['missing'] == 1
    client.post('/api/logout')
    assert client.delete('/api/recordings/1').status_code == 401
    for action in ('preview', 'query', 'delete'):
        assert client.post('/api/recordings/cleanup/' + action, json={}).status_code == 401
    assert client.get('/api/recordings/cleanup/progress/invalid').status_code == 401


def test_concurrent_retention_and_cleanup_share_lock(cleanup_app, monkeypatch):
    main, video, client = cleanup_app
    files = [recording(main, video, minute=minute) for minute in (0, 10, 20)]
    monkeypatch.setattr(video, 'index_segments', lambda *args: None)
    preview = client.post('/api/recordings/cleanup/preview', json={}).json()
    retention = threading.Thread(target=video.cleanup, args=([], set()))
    retention.start()
    result = finish(client, preview)
    retention.join(5)
    assert not retention.is_alive()
    assert result['deleted'] + result['missing'] == 3 and result['failed'] == 0
    assert all(not path.exists() for _, path in files)
    with main.db() as c:
        assert c.execute('SELECT COUNT(*) FROM segments').fetchone()[0] == 0


def test_closed_preview_exclusion_and_missing_parent(cleanup_app):
    main, video, client = cleanup_app
    sid, path = recording(main, video)
    video.ACTIVE_DIRECTORIES.add(path.parent)
    preview = client.post('/api/recordings/cleanup/preview', json={}).json()
    video.ACTIVE_DIRECTORIES.clear()
    result = finish(client, preview)
    assert result['deleted'] == 0 and result['active'] == 1
    assert path.exists(), 'Preview exclusions cannot expand after confirmation'
    path.unlink()
    path.parent.rmdir()
    preview = client.post('/api/recordings/cleanup/preview', json={'recording_ids': [sid]}).json()
    assert finish(client, preview)['missing'] == 1
    with main.db() as c:
        assert c.execute('SELECT COUNT(*) FROM segments').fetchone()[0] == 0


def test_failed_sqlite_commit_recovers_missing_metadata(cleanup_app, monkeypatch):
    main, video, client = cleanup_app
    _, path = recording(main, video)
    preview = client.post('/api/recordings/cleanup/preview', json={}).json()
    import recording_cleanup
    remove = recording_cleanup.remove_segment
    def crash_after_unlink(*args):
        result = remove(*args)
        if result[0] == 'deleted':
            raise RuntimeError('Simulated crash between unlink and SQLite commit')
        return result
    monkeypatch.setattr(recording_cleanup, 'remove_segment', crash_after_unlink)
    result = finish(client, preview)
    assert result['failed'] == 1 and not path.exists()
    with main.db() as c:
        assert c.execute('SELECT COUNT(*) FROM segments').fetchone()[0] == 1
    monkeypatch.setattr(recording_cleanup, 'remove_segment', remove)
    preview = client.post('/api/recordings/cleanup/preview', json={}).json()
    result = finish(client, preview)
    assert result['missing'] == 1 and result['reclaimed_bytes'] == 0
    with main.db() as c:
        assert c.execute('SELECT COUNT(*) FROM segments').fetchone()[0] == 0


def test_indexing_closed_segments_does_not_expose_open_segment(cleanup_app, monkeypatch):
    main, video, client = cleanup_app
    import subprocess
    hour = datetime.now(timezone.utc).strftime('%Y/%m/%d/%H')
    folder = video.ROOT / 'default' / '1' / hour
    folder.mkdir(parents=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H')
    for suffix in ('0000', '1000'):
        (folder / (stamp + suffix + '.mp4')).write_bytes(b'x' * 2048)
    video.ACTIVE_DIRECTORIES.add(folder)
    monkeypatch.setattr(video.subprocess, 'run', lambda *args, **kwargs: subprocess.CompletedProcess([], 0, stdout=b'5'))
    video.index_segments(1, video.ROOT / 'default' / '1', active=True)
    with main.db() as c:
        rows = c.execute('SELECT * FROM segments').fetchall()
    assert len(rows) == 1 and rows[0]['path'].endswith('0000.mp4')
    preview = client.post('/api/recordings/cleanup/preview', json={}).json()
    assert preview['count'] == 0 and preview['active_excluded'] == 1
    video.ACTIVE_DIRECTORIES.clear()


def test_concurrent_manual_operations_and_expired_previews(cleanup_app):
    main, video, client = cleanup_app
    files = [recording(main, video, minute=minute) for minute in (0, 10, 20)]
    previews = [client.post('/api/recordings/cleanup/preview', json={}).json() for _ in range(3)]
    main.cleanup_jobs[previews[2]['token']]['created'] -= 901
    assert client.post('/api/recordings/cleanup/delete', json={'token': previews[2]['token'], 'confirmation': 'DELETE'}).status_code == 410
    for preview in previews[:2]:
        assert client.post('/api/recordings/cleanup/delete', json={'token': preview['token'], 'confirmation': 'DELETE'}).status_code == 202
    results = [finish(client, preview) for preview in previews[:2]]
    assert sum(result['deleted'] for result in results) == 3
    assert sum(result['missing'] for result in results) == 3
    assert sum(result['failed'] for result in results) == 0
    assert all(not path.exists() for _, path in files)


def test_large_clear_all_reports_progress_with_real_files(cleanup_app):
    main, video, client = cleanup_app
    from datetime import timedelta
    start = datetime(2020, 1, 1, 10, tzinfo=timezone.utc)
    folder = video.ROOT / 'default/1/2020/01/01/10'
    folder.mkdir(parents=True)
    records = []
    for index in range(1001):
        stamp = start + timedelta(seconds=index)
        path = folder / (stamp.strftime('%Y%m%dT%H%M%S') + '.mp4')
        path.write_bytes(b'x' * 128)
        records.append((1, str(path), stamp.isoformat(), (stamp + timedelta(seconds=1)).isoformat(), 128))
    with main.db() as c:
        c.executemany('INSERT INTO segments(camera_id,path,started_at,ended_at,size_bytes) VALUES(?,?,?,?,?)', records)
    preview = client.post('/api/recordings/cleanup/preview', json={}).json()
    assert preview['count'] == 1001 and preview['size_bytes'] == 128128
    assert client.post('/api/recordings/cleanup/delete', json={'token': preview['token'], 'confirmation': 'DELETE'}).status_code == 202
    progress = client.get('/api/recordings/cleanup/progress/' + preview['token']).json()
    assert progress['state'] == 'running' and progress['processed'] < progress['total']
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        progress = client.get('/api/recordings/cleanup/progress/' + preview['token']).json()
        if progress['state'] == 'done':
            break
        time.sleep(.05)
    assert progress['state'] == 'done'
    assert progress['processed'] == progress['deleted'] == 1001
    assert progress['reclaimed_bytes'] == 128128 and progress['failed'] == 0
    assert not list(folder.glob('*.mp4'))
    with main.db() as c:
        assert c.execute('SELECT COUNT(*) FROM segments').fetchone()[0] == 0


def test_sqlite_failure_reports_actual_unlinked_bytes_and_recovers(cleanup_app):
    main, video, client = cleanup_app
    _, path = recording(main, video)
    with main.db() as c:
        c.execute("CREATE TRIGGER fail_cleanup BEFORE DELETE ON segments BEGIN SELECT RAISE(FAIL,'simulated database failure'); END")
    preview = client.post('/api/recordings/cleanup/preview', json={}).json()
    result = finish(client, preview)
    assert result['failed'] == 1 and result['deleted'] == 0
    assert result['reclaimed_bytes'] == 1280 and not path.exists()
    with main.db() as c:
        assert c.execute('SELECT COUNT(*) FROM segments').fetchone()[0] == 1
        c.execute('DROP TRIGGER fail_cleanup')
    preview = client.post('/api/recordings/cleanup/preview', json={}).json()
    assert finish(client, preview)['missing'] == 1
    with main.db() as c:
        assert c.execute('SELECT COUNT(*) FROM segments').fetchone()[0] == 0
