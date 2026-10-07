"""Absolute UTC metadata, installation civil-time boundaries, batch investigations."""
import json
from datetime import date, datetime, time

import pytest
from fastapi.testclient import TestClient

from test_nvr import setup, FakeGateway
from policies import recording_expected, schedule_active
from timeconfig import day_range, resolve_local, save_timezone


@pytest.mark.parametrize('zone,day,hours', [
    ('UTC','2026-10-06',24),('Europe/Moscow','2026-10-06',24),
    ('America/New_York','2026-03-08',23),('America/New_York','2026-11-01',25),
    ('Australia/Lord_Howe','2026-10-04',23.5),
])
def test_local_day_absolute_duration(zone, day, hours):
    result=day_range(date.fromisoformat(day),zone)
    start=datetime.fromisoformat(result['start'])
    end=datetime.fromisoformat(result['end'])
    assert (end-start).total_seconds()==hours*3600
    assert start.utcoffset().total_seconds()==0
    assert result['ticks'][0]['time']==result['start']
    assert result['ticks'][-1]['time']==result['end']
    if zone=='Europe/Moscow':
        assert result['start']=='2026-10-05T21:00:00+00:00'


@pytest.mark.parametrize('zone,local,utc', [
    ('UTC','2026-10-06T14:32:17','2026-10-06T14:32:17+00:00'),
    ('Europe/Moscow','2026-10-06T14:32:17','2026-10-06T11:32:17+00:00'),
    ('America/New_York','2026-07-06T14:32:17','2026-07-06T18:32:17+00:00'),
    ('America/New_York','2026-01-06T14:32:17','2026-01-06T19:32:17+00:00'),
])
def test_local_to_absolute(zone,local,utc):
    stamp=datetime.fromisoformat(local)
    result=resolve_local(stamp.date(),stamp.time(),zone)
    assert result[0]['time']==utc
    assert datetime.fromisoformat(result[0]['local']).replace(tzinfo=None)==stamp


def test_dst_ambiguity_gap_and_skipped_date():
    values=resolve_local(date(2026,11,1),time(1,30),'America/New_York')
    assert [item['time'] for item in values]==['2026-11-01T05:30:00+00:00','2026-11-01T06:30:00+00:00']
    with pytest.raises(ValueError,match='does not exist'):
        resolve_local(date(2026,3,8),time(2,30),'America/New_York')
    with pytest.raises(ValueError,match='does not exist'):
        day_range(date(2011,12,30),'Pacific/Apia')
    assert day_range(date(2011,12,29),'Pacific/Apia')['end']=='2011-12-30T10:00:00+00:00'
    with pytest.raises(ValueError,match='offset'):
        resolve_local(date(2026,10,6),time.fromisoformat('12:00+03:00'),'Europe/Moscow')


def records(main):
    with main.db() as connection:
        return [tuple(row) for row in connection.execute('SELECT * FROM segments ORDER BY id')]


def insert(main,camera,start,end):
    with main.db() as connection:
        cursor=connection.execute('INSERT INTO segments(camera_id,path,started_at,ended_at,size_bytes) VALUES(?,?,?,?,?)',
                                 (camera,f'/recording-{camera}-{start}',start,end,4096))
        return cursor.lastrowid


def client_setup(tmp_path,monkeypatch):
    main,_=setup(tmp_path,monkeypatch)
    monkeypatch.setenv('APP_TIMEZONE','UTC')
    monkeypatch.setattr(main,'MediaGateway',FakeGateway)
    return main


def test_timezone_persistence_auth_validation_and_no_database_changes(tmp_path,monkeypatch):
    main=client_setup(tmp_path,monkeypatch)
    with TestClient(main.app) as client:
        assert client.get('/api/time').status_code==401
        assert client.put('/api/time',json={'timezone':'Europe/Moscow'}).status_code==401
        assert client.post('/api/login', json={'username':'admin','password':'secret'}).status_code == 200
        assert client.get('/api/time').json()['timezone']=='UTC'
        assert 'America/New_York' in client.get('/api/time').json()['timezones']
        insert(main,1,'2026-10-06T08:00:00+00:00','2026-10-06T08:01:00+00:00')
        before=records(main)
        with main.db() as connection:
            schema=[tuple(row) for row in connection.execute('SELECT * FROM sqlite_master ORDER BY name')]
        assert client.put('/api/time',json={'timezone':'UTC+3'}).status_code==422
        assert client.put('/api/time',json={'timezone':'Europe/Moscow'}).json()=={'timezone':'Europe/Moscow'}
        assert client.get('/api/config').json()['timezone']=='Europe/Moscow'
        assert records(main)==before
        with main.db() as connection:
            assert [tuple(row) for row in connection.execute('SELECT * FROM sqlite_master ORDER BY name')]==schema
        path=tmp_path/'data'/'settings.json'
        assert json.loads(path.read_text())=={'timezone':'Europe/Moscow'}
        assert path.stat().st_mode & 0o777 ==0o600
        import timeconfig
        monkeypatch.setattr(timeconfig,'_cached',None)
        monkeypatch.setenv('APP_TIMEZONE','America/New_York')
        assert client.get('/api/time').json()['timezone']=='Europe/Moscow'
        response=client.post('/api/time/resolve',json={'date':'2026-10-06','time':'11:00:00'}).json()
        assert response['instants'][0]['time']=='2026-10-06T08:00:00+00:00'
        assert client.put('/api/time',json={'timezone':'America/New_York'}).status_code==200
        assert records(main)==before
        assert client.post('/api/time/resolve',json={'date':'2026-03-08','time':'02:30'}).status_code==422
        assert len(client.post('/api/time/resolve',json={'date':'2026-11-01','time':'01:30'}).json()['instants'])==2


def test_failed_configuration_write_preserves_setting(tmp_path,monkeypatch):
    client_setup(tmp_path,monkeypatch)
    save_timezone('UTC')
    import timeconfig
    def fail(*args):
        raise OSError('read-only volume')
    monkeypatch.setattr(timeconfig.os,'replace',fail)
    with pytest.raises(OSError):
        save_timezone('Europe/Moscow')
    assert timeconfig.get_timezone()=='UTC'
    assert json.loads((tmp_path/'data'/'settings.json').read_text())['timezone']=='UTC'
    assert not list((tmp_path/'data').glob('.settings-*'))


def test_global_timezone_drives_legacy_schedules_and_overnight_dst(tmp_path,monkeypatch):
    client_setup(tmp_path,monkeypatch)
    # Legacy per-camera zone is deliberately not an independent runtime override.
    schedule={'timezone':'UTC','windows':[{'days':[0],'start':'22:00','end':'07:00'}]}
    row={'enabled':True,'recording_enabled':True,'recording_schedule':schedule}
    instant=datetime.fromisoformat('2026-10-05T19:30:00Z')
    assert not recording_expected(row,instant)
    save_timezone('Europe/Moscow')
    assert recording_expected(row,instant)
    assert recording_expected(row,datetime.fromisoformat('2026-10-06T03:59:00Z'))
    assert not recording_expected(row,datetime.fromisoformat('2026-10-06T04:00:00Z'))
    save_timezone('America/New_York')
    schedule['windows']=[{'days':[6],'start':'01:00','end':'02:00'}]
    assert recording_expected(row,datetime.fromisoformat('2026-11-01T05:30:00Z'))
    assert recording_expected(row,datetime.fromisoformat('2026-11-01T06:30:00Z'))
    schedule['windows']=[{'days':[6],'start':'02:00','end':'03:00'}]
    assert not recording_expected(row,datetime.fromisoformat('2026-03-08T07:15:00Z'))
    with pytest.raises(ValueError,match='aware'):
        schedule_active(schedule,datetime(2026,10,6))


def test_batch_timeline_resolver_offsets_gaps_and_local_date_overlap(tmp_path,monkeypatch):
    main=client_setup(tmp_path,monkeypatch)
    with TestClient(main.app) as client:
        assert client.post('/api/login', json={'username':'admin','password':'secret'}).status_code == 200
        a=insert(main,1,'2026-10-05T20:59:00+00:00','2026-10-05T21:03:00+00:00')
        b=insert(main,2,'2026-10-05T21:00:30+00:00','2026-10-05T21:02:00+00:00')
        c=insert(main,3,'2026-10-05T21:04:00+00:00','2026-10-05T21:05:00+00:00')
        d=insert(main,1,'2026-10-05T21:03:00+00:00','2026-10-05T21:06:00+00:00')
        save_timezone('Europe/Moscow')
        bounds=client.get('/api/time/day?date=2026-10-06').json()
        batch=client.post('/api/recordings/timelines',json={'camera_ids':[1,2,3,4], 'start':bounds['start'],'end':bounds['end']})
        assert batch.status_code==200,batch.text
        assert batch.json()['cameras']['1']['intervals']==[{'start':'2026-10-05T21:00:00+00:00','end':'2026-10-05T21:06:00+00:00'}]
        assert batch.json()['cameras']['4']['intervals']==[]
        instant=client.post('/api/time/resolve',json={'date':'2026-10-06','time':'00:01:00'}).json()['instants'][0]['time']
        result=client.post('/api/recordings/resolve',json={'camera_ids':[1,2,3,4], 'time':instant}).json()['cameras']
        assert result['1']['segment']['id']==a and result['1']['seek_seconds']==120
        assert result['2']['segment']['id']==b and result['2']['seek_seconds']==30
        assert result['3']['segment'] is None and result['3']['next_segment']['id']==c
        assert result['4']['segment'] is None and result['4']['next_segment'] is None
        boundary=client.post('/api/recordings/resolve',json={'camera_ids':[1,3], 'time':'2026-10-05T21:04:10Z'}).json()['cameras']
        assert boundary['1']['segment']['id']==d and boundary['1']['seek_seconds']==70
        assert boundary['3']['segment']['id']==c and boundary['3']['seek_seconds']==10
        listing=client.get('/api/recordings',params={'camera_ids':'1,2','start':bounds['start'],'end':bounds['end'],'limit':2}).json()
        assert {r['id'] for r in listing}=={d,b}
        assert client.get('/api/recordings',params={'camera_ids':'1,2','start':bounds['start'],'end':bounds['end'],'offset':2}).json()[0]['id']==a
        assert all('path' not in item for item in listing)
        assert client.post('/api/recordings/resolve',json={'camera_ids':[1],'time':'2026-10-05T21:01:00'}).status_code==422
        assert client.post('/api/recordings/timelines',json={'camera_ids':[1],'start':'2026-10-05T00:00:00Z','end':'2026-12-05T00:00:00Z'}).status_code==422
        assert client.get('/api/recordings',params={'start':bounds['start']}).status_code==422
        assert client.post('/api/recordings/resolve',json={'camera_ids':[-1],'time':instant}).status_code==422
        assert client.get('/api/recordings',params={'camera_ids':'oops'}).status_code==422


def test_invalid_bootstrap_zone_fails_without_a_saved_preference(tmp_path,monkeypatch):
    client_setup(tmp_path,monkeypatch)
    monkeypatch.setenv('APP_TIMEZONE','UTC+3')
    import timeconfig
    with pytest.raises(ValueError,match='IANA'):
        timeconfig.get_timezone()
    for value in ('localtime','posixrules'):
        with pytest.raises(ValueError,match='IANA'):
            timeconfig.validate_zone(value)


def test_iana_aliases_are_canonicalized_for_browser_compatibility(tmp_path,monkeypatch):
    client_setup(tmp_path,monkeypatch)
    import timeconfig
    assert timeconfig.validate_zone('UTC')=='UTC'
    assert timeconfig.validate_zone('US/Eastern')=='America/New_York'
    assert timeconfig.save_timezone('US/Eastern')=='America/New_York'
    assert timeconfig.get_timezone()=='America/New_York'
    if 'Asia/Beijing' in timeconfig.available_timezones():
        assert timeconfig.validate_zone('Asia/Beijing')=='Asia/Shanghai'
        assert 'Asia/Beijing' not in timeconfig.valid_timezones()


def test_historical_subminute_midnight_transition():
    result=day_range(date(1972,1,7),'Africa/Monrovia')
    assert result['start']=='1972-01-07T00:44:30+00:00'
