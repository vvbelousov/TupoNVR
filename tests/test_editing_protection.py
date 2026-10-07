"""Regression coverage for safe editing and explicit storage enrollment."""
import re

import pytest
from fastapi.testclient import TestClient

from test_nvr import setup, FakeGateway
from storage import marker_operation, MARKER_FILE


def test_marker_creation_is_atomic_exclusive_and_verified(tmp_path):
    destination = tmp_path / 'nas'
    destination.mkdir()
    result = marker_operation(tmp_path, 'nas', 'create', allow_local=True)
    assert result['reason'] == 'ok'
    assert re.fullmatch(r'[0-9a-f]{64}', result['identifier'])
    assert (destination / MARKER_FILE).read_text().strip() == result['identifier']
    assert marker_operation(tmp_path, 'nas', 'use_existing')['identifier'] == result['identifier']
    assert marker_operation(tmp_path, 'nas', 'create', allow_local=True)['reason'] == 'marker_exists'
    assert not list(destination.glob('.nvr-id-*'))
    assert (destination / MARKER_FILE).read_text().strip() == result['identifier']


@pytest.mark.parametrize('content', [b'', b'bad id', b'../escape', b'a'*129, b'\xff', b'good\n' + b' '*300])
def test_marker_adoption_rejects_malformed_content(tmp_path, content):
    destination = tmp_path / 'nas'
    destination.mkdir()
    (destination / MARKER_FILE).write_bytes(content)
    assert marker_operation(tmp_path,'nas','use_existing')['reason'] == 'invalid_marker'
    assert marker_operation(tmp_path,'nas','create',allow_local=True)['reason'] == 'marker_exists'
    assert (destination / MARKER_FILE).read_bytes() == content


def test_marker_operation_refuses_missing_mount_paths_and_symlinks(tmp_path):
    assert marker_operation(tmp_path,'nas','create',allow_local=True)['reason'] == 'unavailable'
    assert not (tmp_path/'nas').exists()
    destination = tmp_path/'nas'
    destination.mkdir()
    assert marker_operation(tmp_path,'nas','create')['reason'] == 'mount_confirmation_required'
    assert not (destination/MARKER_FILE).exists()
    assert marker_operation(tmp_path,'../outside','create',allow_local=True)['reason'] == 'unsafe_path'
    (destination/MARKER_FILE).symlink_to(tmp_path/'secret')
    assert marker_operation(tmp_path,'nas','use_existing')['reason'] == 'unavailable'
    destination.rename(tmp_path/'real')
    destination.symlink_to(tmp_path/'real')
    assert marker_operation(tmp_path,'nas','create',allow_local=True)['reason'] == 'unavailable'


def test_marker_permission_failure_is_sanitized(tmp_path, monkeypatch):
    import storage
    (tmp_path/'nas').mkdir()
    def denied(*args, **kwargs):
        raise PermissionError('private filesystem information')
    monkeypatch.setattr(storage.os, 'link', denied)
    result = marker_operation(tmp_path,'nas','create',allow_local=True)
    assert result == {'reason':'not_writable'}
    assert not list((tmp_path/'nas').iterdir())


def test_marker_racing_creator_is_not_overwritten(tmp_path, monkeypatch):
    import storage
    destination = tmp_path/'nas'
    destination.mkdir()
    original = storage.os.link
    def race(*args, **kwargs):
        (destination/MARKER_FILE).write_text('other-writer')
        return original(*args, **kwargs)
    monkeypatch.setattr(storage.os, 'link', race)
    assert marker_operation(tmp_path,'nas','create',allow_local=True)['reason'] == 'marker_exists'
    assert (destination/MARKER_FILE).read_text() == 'other-writer'
    assert not list(destination.glob('.nvr-id-*'))


def test_protection_api_create_adopt_mismatch_and_manual_configuration(tmp_path, monkeypatch):
    main, _ = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(main,'MediaGateway',FakeGateway)
    monkeypatch.setattr(main,'MIN_FREE',0)
    with TestClient(main.app) as client:
        path = '/api/storage/destinations/default/protection'
        body = {'action':'create','previous_expected_marker':None,'allow_local':True}
        assert client.post(path,json=body).status_code == 401
        assert client.post('/api/login', json={'username':'admin','password':'secret'}).status_code == 200
        created = client.post(path,json=body)
        assert created.status_code == 200, created.text
        identifier = created.json()['expected_marker']
        assert created.json()['ready']
        assert main.destination_markers()['default'] == identifier
        assert (main.ROOT/'default'/MARKER_FILE).read_text().strip() == identifier
        assert client.post(path,json=body).status_code == 409
        assert client.post('/api/storage/destinations/unknown/protection',json=body).status_code == 404
        assert not (main.ROOT/'unknown').exists()
        assert client.post(path,json={**body,'previous_expected_marker':identifier}).status_code == 409
        (main.ROOT/'default'/MARKER_FILE).write_text('different-id')
        adopt = {'action':'use_existing','previous_expected_marker':identifier}
        assert client.post(path,json=adopt).status_code == 409
        assert main.destination_markers()['default'] == identifier
        changed = client.post(path,json={**adopt,'replace_existing':True})
        assert changed.status_code == 200 and changed.json()['ready']
        assert changed.json()['expected_marker'] == 'different-id'
        manual = client.put('/api/storage/destinations/default',json={'expected_marker':None})
        assert manual.status_code == 200 and manual.json()['ready']
        existing = client.post(path,json=body)
        assert existing.status_code == 409 and 'already exists' in existing.json()['detail']
        assert client.post(path,json={'action':'use_existing','previous_expected_marker':None}).json()['ready']
        (main.ROOT/'default'/MARKER_FILE).unlink()
        blocked = client.post(path,json={**body,'previous_expected_marker':'different-id'})
        assert blocked.status_code == 409
        assert not (main.ROOT/'default'/MARKER_FILE).exists()
        assert main.destination_markers()['default'] == 'different-id'


def test_protection_api_missing_destination_is_not_created(tmp_path, monkeypatch):
    main, _ = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(main,'MediaGateway',FakeGateway)
    with TestClient(main.app) as client:
        assert client.post('/api/login', json={'username':'admin','password':'secret'}).status_code == 200
        client.put('/api/storage/destinations/nas',json={'expected_marker':'expected'})
        assert not (main.ROOT/'nas').exists()
        result = client.post('/api/storage/destinations/nas/protection',json={'action':'use_existing','previous_expected_marker':'expected','replace_existing':True})
        assert result.status_code == 400
        assert main.destination_markers()['nas'] == 'expected'
        assert not (main.ROOT/'nas').exists()


@pytest.mark.parametrize('embedded', [False, True])
def test_camera_edit_loads_safe_values_and_partial_changes_preserve_settings(tmp_path, monkeypatch, embedded, caplog):
    main, video = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(main,'MediaGateway',FakeGateway)
    schedule = {'timezone':'UTC','windows':[{'days':[0,1], 'start':'09:00','end':'17:00'}]}
    payload = {'name':'Camera','rtsp_url':'rtsp://user:stored-secret@cam/live?token=private-token' if embedded else 'rtsp://cam/live?token=private-token',
               'username':None if embedded else 'user','password':None if embedded else 'stored-secret','substream_url':'rtsp://sub:sub-secret@cam/small?auth=private-sub-token',
               'enabled':True,'recording_enabled':False,'recording_destination':'nas','retention_days':30,'description':'Description','recording_schedule':schedule}
    with TestClient(main.app) as client:
        assert client.post('/api/login', json={'username':'admin','password':'secret'}).status_code == 200
        created = client.post('/api/cameras',json=payload)
        cid = created.json()['id']
        assert 'stored-secret' not in created.text
        edit = client.get(f'/api/cameras/{cid}/edit')
        assert edit.status_code == 200
        fields = edit.json()
        assert fields['rtsp_url'] == 'rtsp://cam/live'
        assert fields['substream_url'] == 'rtsp://cam/small'
        assert fields['username'] == 'user'
        assert fields['recording_schedule'] == schedule
        for key in ('name','description','retention_days','recording_destination','recording_enabled','enabled'):
            assert fields[key] == payload[key]
        assert fields['has_url_options'] and 'password' not in fields
        assert all(secret not in edit.text for secret in ('stored-secret','sub-secret','private-token','private-sub-token'))
        before = dict(main.one(cid))
        assert client.patch(f'/api/cameras/{cid}',json={'name':'Renamed'}).status_code == 200
        after = dict(main.one(cid))
        assert after == {**before,'name':'Renamed'}
        assert client.put(f'/api/cameras/{cid}',json={'description':'Changed'}).status_code == 200
        assert client.patch(f'/api/cameras/{cid}',json={'username':'renamed-user','password':''}).status_code == 200
        assert url_secret(video.source_url(main.one(cid))) == 'stored-secret'
        changed = client.patch(f'/api/cameras/{cid}',json={'rtsp_url':'rtsp://cam/new','substream_url':'rtsp://cam/new-small','password':''})
        assert changed.status_code == 200
        row = main.one(cid)
        assert url_secret(video.source_url(row)) == 'stored-secret'
        assert 'private-token' in video.source_url(row) and '/new?' in video.source_url(row)
        assert 'sub-secret' in row['substream_url'] and 'private-sub-token' in row['substream_url']
        response = client.patch(f'/api/cameras/{cid}',json={'password':'replacement-secret'})
        assert response.status_code == 200
        assert url_secret(video.source_url(main.one(cid))) == 'replacement-secret'
        assert 'replacement-secret' not in response.text
        assert client.patch(f'/api/cameras/{cid}',json={'username':'new-user'}).status_code == 200
        assert main.one(cid)['username'] == 'new-user'
        assert url_secret(video.source_url(main.one(cid))) == 'replacement-secret'
        assert client.patch(f'/api/cameras/{cid}',json={'substream_url':None}).status_code == 200
        assert main.one(cid)['substream_url'] is None
        invalid = client.patch(f'/api/cameras/{cid}',json={'rtsp_url':'invalid://user:private-error@cam','password':'secret-error'})
        assert invalid.status_code == 422 and 'private-error' not in invalid.text and 'secret-error' not in invalid.text
        assert 'stored-secret' not in caplog.text and 'replacement-secret' not in caplog.text


def url_secret(url):
    from urllib.parse import urlsplit, unquote
    return unquote(urlsplit(url).password or '')
