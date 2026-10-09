"""Bootstrap, portable identity, credential safety and atomic imports."""
import json
import sqlite3

import pytest
import yaml
from fastapi.testclient import TestClient
from test_nvr import setup, FakeGateway


def document(*cameras):
    return yaml.safe_dump({'version': 1, 'cameras': list(cameras)}, sort_keys=False)


def entry(key='door', **fields):
    return {'key': key, 'name': 'Door', 'rtsp_url': 'rtsp://cam/live', 'recording_enabled': False, **fields}


@pytest.fixture
def env(tmp_path, monkeypatch):
    main, _ = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(main, 'MediaGateway', FakeGateway)
    import camera_yaml
    monkeypatch.setattr(camera_yaml, 'BOOTSTRAP_PATH', tmp_path / 'cameras.yaml')
    with TestClient(main.app) as client:
        client.post('/api/login', json={'username': 'admin', 'password': 'secret'})
        yield main, client, camera_yaml


def preview(client, text):
    response = client.post('/api/camera-config/preview', content=text, headers={'Content-Type': 'application/yaml'})
    assert response.status_code == 200, response.text
    return response.json()


def apply(client, text, fingerprint=None):
    fingerprint = fingerprint or preview(client, text).get('fingerprint', '')
    return client.post('/api/camera-config/apply', content=text, headers={'X-Confirm-Import': 'true', 'X-Import-Fingerprint': fingerprint})


def test_import_export_rename_partial_roundtrip_and_restart(env):
    main, client, service = env
    schedule = {'timezone': 'UTC', 'windows': [{'days': [0, 1], 'start': '09:00', 'end': '18:00'}]}
    text = document(entry(username='admin', password='camera-secret', substream_url='rtsp://cam/small',
                          description='Entrance', retention_days=14, recording_schedule=schedule, recording_destination='nas'))
    p = preview(client, text)
    assert len(p['create']) == 1 and not p['errors']
    assert 'camera-secret' not in json.dumps(p)
    assert apply(client, text, p['fingerprint']).json()['created'] == 1
    cid = main.rows()[0]['id']
    exported = client.get('/api/camera-config/export?include_credentials=true')
    assert 'camera-secret' in exported.text and exported.headers['cache-control'] == 'no-store'
    assert yaml.safe_load(exported.text)['cameras'][0]['key'] == 'door'
    assert 'id' not in yaml.safe_load(exported.text)['cameras'][0]
    assert apply(client, exported.text).json()['unchanged'] == 1
    assert apply(client, exported.text).json()['unchanged'] == 1
    assert preview(client, document({'key':'door','name':'Renamed'}))['update'][0]['changes']['name']['after'] == 'Renamed'
    assert apply(client, document({'key':'door','name':'Renamed','password':'','username':''})).json()['updated'] == 1
    assert main.one(cid)['password'] == 'camera-secret'
    assert main.one(cid)['retention_days'] == 14
    assert json.loads(main.one(cid)['recording_schedule']) == schedule
    main.init()
    assert main.rows()[0]['key'] == 'door'
    assert len(main.rows()) == 1
    # Import a subset; absence never deletes cameras.
    apply(client, document(entry('other')))
    apply(client, document({'key':'door','description':'updated'}))
    assert len(main.rows()) == 2


def test_redacted_export_preserves_embedded_and_explicit_secrets(env, caplog):
    main, client, _ = env
    text = document(entry(rtsp_url='rtsp://url-user:url-secret@cam/live?token=query-secret',
                          substream_url='rtsp://sub-user:sub-secret@cam/small?token=sub-token', username='admin',password='stored-secret'))
    apply(client,text)
    exported = client.get('/api/camera-config/export')
    assert exported.headers['cache-control'] == 'no-store'
    for secret in ('url-user','url-secret','query-secret','sub-user','sub-secret','sub-token','stored-secret','admin'):
        assert secret not in exported.text
    assert 'username' not in exported.text and 'password' not in exported.text
    p = preview(client, exported.text)
    assert len(p['unchanged']) == 1
    assert apply(client, exported.text).json()['unchanged'] == 1
    assert 'url-secret' in main.rows()[0]['rtsp_url']
    assert all(secret not in caplog.text for secret in ('url-secret','query-secret','stored-secret'))


@pytest.mark.parametrize('text', ['', 'bad: [', 'version: 2\ncameras: []', 'version: true\ncameras: []',
    'version: 1\ncameras: !!python/object:foo {}', 'version: 1\ncameras: &x [*x]',
    'version: 1\nversion: 1\ncameras: []', document(entry(enabled='yes')),
    document(entry(rtsp_url='file:///etc/passwd')), document(entry(recording_destination='../escape')),
    document(entry(username=['bad'])), document(entry(password='bad\nvalue')),
    document(entry(unknown='no')), document(entry(retention_days=True)), document(entry(recording_schedule={'windows':[{'days':[0],'start':'bad','end':'18:00'}]})),
    document(entry(recording_schedule={'windows':[{'days':[0],'start':'09:00','end':'18:00','typo':1}]})),
    document(entry('bad/key')), document(entry(username='admin')), document(entry(username='admin',password=''))])
def test_invalid_documents_are_atomic_and_sanitized(env, text):
    main, client, _ = env
    p = preview(client, text)
    assert p['errors'] and 'fingerprint' not in p
    assert 'input' not in json.dumps(p) and 'bad\nvalue' not in json.dumps(p)
    assert apply(client, text, 'bad').status_code == 422
    assert not main.rows()


def test_full_validation_duplicates_conflicts_confirmation_and_limits(env):
    main, client, _ = env
    invalid = document(entry(),entry('second',rtsp_url='invalid://private:secret@host'))
    p = preview(client, invalid)
    assert p['errors'] and len(p['create']) == 1 and 'private' not in json.dumps(p)
    assert apply(client, invalid, 'bad').status_code == 422
    assert not main.rows()
    assert preview(client, document(entry(),entry()))['conflicts']
    assert apply(client, document(entry(),entry()), 'bad').status_code == 422
    assert client.post('/api/camera-config/apply', content=document(entry())).status_code == 422
    assert client.post('/api/camera-config/preview',content=b'a'*(1024*1024+1)).status_code == 413
    assert preview(client,b'\xff')['errors']
    assert preview(client,document())['fingerprint']


def test_stale_preview_changed_document_and_transaction_rollback(env, monkeypatch):
    main, client, service = env
    text = document(entry())
    fingerprint = preview(client, text)['fingerprint']
    assert apply(client, document(entry(name='Other')), fingerprint).status_code == 409
    apply(client, text, fingerprint)
    fingerprint = preview(client, document({'key':'door','name':'New'}))['fingerprint']
    client.patch(f"/api/cameras/{main.rows()[0]['id']}",json={'description':'changed'})
    assert apply(client,document({'key':'door','name':'New'}),fingerprint).status_code == 409
    original = service.write
    def failing(c, writes):
        original(c, writes[:1])
        raise sqlite3.OperationalError('failure')
    monkeypatch.setattr(service,'write',failing)
    response = apply(client,document(entry('second'),entry('third')))
    assert response.status_code == 503
    assert len(main.rows()) == 1


def test_runtime_failure_reports_committed_configuration(env, monkeypatch):
    main, client, _ = env
    async def fail(request):
        raise RuntimeError('private-secret')
    monkeypatch.setattr(main,'reconcile',fail)
    response=apply(client,document(entry()))
    assert response.status_code == 200 and response.json()['runtime_pending']
    assert len(main.rows()) == 1


def test_unauthorized_endpoints(env):
    _, client, _ = env
    client.cookies.clear()
    assert client.get('/api/camera-config/export?include_credentials=true').status_code == 401
    assert client.post('/api/camera-config/preview',content=document()).status_code == 401
    assert client.post('/api/camera-config/apply',content=document()).status_code == 401


def test_existing_records_receive_stable_keys_without_changing_ids(tmp_path, monkeypatch):
    main, _ = setup(tmp_path, monkeypatch)
    import db
    db.PATH.parent.mkdir(parents=True)
    with sqlite3.connect(db.PATH) as c:
        c.execute("CREATE TABLE cameras (id INTEGER PRIMARY KEY, name TEXT NOT NULL, rtsp_url TEXT NOT NULL, username TEXT, password TEXT, enabled INTEGER DEFAULT 1, recording_enabled INTEGER DEFAULT 1, recording_destination TEXT DEFAULT 'default', retention_days INTEGER, substream_url TEXT, description TEXT DEFAULT '')")
        c.execute("INSERT INTO cameras(id,name,rtsp_url) VALUES(42,'Old','rtsp://cam/live')")
    main.init()
    key = main.one(42)['key']
    main.init()
    assert main.one(42)['key'] == key
    with main.db() as c:
        c.execute("INSERT INTO cameras(name,rtsp_url) VALUES('Next','rtsp://cam/live')")
    assert main.rows()[1]['id'] > 42 and main.rows()[1]['key'] != key


def test_bootstrap_missing_invalid_valid_skipped_restarts(env, tmp_path, caplog):
    main, _, service = env
    path = tmp_path/'bootstrap.yaml'
    service.bootstrap(path)
    assert not main.rows()
    for text in ('bad: [','version: 2\ncameras: []',document(entry(),entry('bad',rtsp_url='invalid://user:secret@host'))):
        path.write_text(text)
        service.bootstrap(path)
        assert not main.rows()
    assert 'user:secret' not in caplog.text and 'camera_bootstrap_failed' in caplog.text
    path.write_text(document(entry(password='bootstrap-secret')))
    service.bootstrap(path)
    assert len(main.rows()) == 1
    with main.db() as c:
        c.execute("UPDATE cameras SET name='Edited'")
    for _ in range(2):
        main.init()
        service.bootstrap(path)
    assert len(main.rows()) == 1 and main.rows()[0]['name'] == 'Edited'
    assert 'bootstrap-secret' not in caplog.text


def test_lifespan_bootstrap_uses_optional_path(tmp_path, monkeypatch):
    main, _ = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(main,'MediaGateway',FakeGateway)
    import camera_yaml
    path=tmp_path/'cameras.yaml'
    path.write_text(document(entry()))
    monkeypatch.setattr(camera_yaml,'BOOTSTRAP_PATH',path)
    for _ in range(2):
        with TestClient(main.app):
            assert len(main.rows()) == 1


def test_export_order_defaults_and_migration_to_empty_database(env):
    main, client, service = env
    apply(client, document(entry('z-last'), entry('a-first', retention_days=None, recording_enabled=True, substream_url='rtsp://cam/small')))
    plain = client.get('/api/camera-config/export').text
    assert plain == client.get('/api/camera-config/export').text
    cameras = yaml.safe_load(plain)['cameras']
    assert [c['key'] for c in cameras] == ['a-first', 'z-last']
    assert cameras[1]['retention_days'] == 7 and cameras[1]['enabled'] is True
    expected = [dict(r) for r in main.rows()]
    with main.db() as c:
        c.execute('DELETE FROM cameras')
    assert apply(client, plain).json()['created'] == 2
    for previous in expected:
        current = next(dict(r) for r in main.rows() if r['key'] == previous['key'])
        assert {k:v for k,v in current.items() if k != 'id'} == {k:v for k,v in previous.items() if k != 'id'}
    assert preview(client, document({'key':'a-first','substream_url':None,'recording_schedule':None}))['update']
    assert apply(client, document({'key':'a-first','substream_url':None,'recording_schedule':None})).status_code == 200
    assert next(r for r in main.rows() if r['key']=='a-first')['substream_url'] is None


def test_credential_rotation_and_failed_mixed_update_create_are_atomic(env, monkeypatch):
    main, client, service = env
    apply(client, document(entry(username='admin', password='original')))
    cid=main.rows()[0]['id']
    assert apply(client, document({'key':'door','password':'replacement'})).json()['updated'] == 1
    assert main.one(cid)['password'] == 'replacement'
    text=document({'key':'door','name':'Changed'},entry('new'))
    original=service.write
    def failing(c, writes):
        original(c,writes)
        raise sqlite3.OperationalError('failed')
    monkeypatch.setattr(service,'write',failing)
    assert apply(client,text).status_code == 503
    assert len(main.rows()) == 1 and main.one(cid)['name'] == 'Door'


def test_bootstrap_database_failure_rolls_back_and_startup_continues(env, tmp_path, monkeypatch, caplog):
    main, _, service = env
    path=tmp_path/'failure.yaml'
    path.write_text(document(entry(),entry('second')))
    original=service.write
    def failing(c, writes):
        original(c,writes[:1])
        raise sqlite3.OperationalError('secret-internal-detail')
    monkeypatch.setattr(service,'write',failing)
    service.bootstrap(path)
    assert not main.rows()
    assert 'continuing startup' in caplog.text and 'secret-internal-detail' not in caplog.text


def test_new_substream_credentials_and_non_ascii_fingerprint(env):
    _, client, _ = env
    assert preview(client,document(entry(substream_url='rtsp://admin@cam/small')))['errors']
    assert apply(client,document(entry()),b'\xe9'*64).status_code == 409


def test_explicit_credentials_merge_with_embedded_credentials_on_creation(env):
    main, client, _ = env
    response = apply(client, document(entry(rtsp_url='rtsp://embedded:embedded-secret@cam/live', username='admin')))
    assert response.status_code == 200 and main.rows()[0]['password'] == 'embedded-secret'
    assert preview(client, document(entry('second', username='admin', substream_url='rtsp://sub:sub-secret@cam/small')))['errors']
