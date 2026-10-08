import asyncio
import json
import logging
import os
import secrets
import time
import shutil
import re
import threading
from contextlib import asynccontextmanager
from datetime import date as CivilDate, time as CivilTime, datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, unquote
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, field_validator
from db import db, init
from video import MediaGateway, ProcessSupervisor, ROOT, MIN_FREE, cleanup
from policies import RecordingSchedule, recording_expected, schedule_active
from storage import StorageMonitor, NAME_PATTERN
from notifications import Webhooks
from timeconfig import get_timezone, save_timezone, validate_zone, valid_timezones, day_range, resolve_local
from version import VERSION
from security import Sessions, MediaProxy

class JsonLog(logging.Formatter):
    def format(self, record):
        return json.dumps({'time': datetime.now(timezone.utc).isoformat(), 'level': record.levelname, 'logger': record.name, 'event': record.getMessage()})

handler = logging.StreamHandler()
handler.setFormatter(JsonLog())
logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO'), handlers=[handler])
log = logging.getLogger('nvr')
logging.getLogger('httpx').setLevel(logging.WARNING)
AUTH_USER = os.getenv('AUTH_USERNAME', '')
AUTH_PASS = os.getenv('AUTH_PASSWORD', '')
DEFAULT_LANGUAGE = os.getenv('DEFAULT_LANGUAGE', 'en').lower()
if DEFAULT_LANGUAGE not in ('en', 'ru'):
    raise RuntimeError('DEFAULT_LANGUAGE must be en or ru')
if bool(AUTH_USER) != bool(AUTH_PASS):
    raise RuntimeError('Set both AUTH_USERNAME and AUTH_PASSWORD, or neither')
sessions = Sessions()

class LoginInput(BaseModel):
    username: str
    password: str

async def auth(request: Request):
    if not AUTH_USER and not AUTH_PASS:
        return
    if not sessions.valid(request.cookies.get('nvr_session', ''), AUTH_USER, AUTH_PASS):
        raise HTTPException(401, 'Authentication required')

class CameraInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    rtsp_url: str
    username: str | None = None
    password: str | None = None
    enabled: bool = True
    recording_enabled: bool = True
    recording_destination: str = Field(default='default', pattern=r'^[A-Za-z0-9_-]{1,64}$')
    retention_days: int | None = Field(default=7, ge=1, le=3650)
    substream_url: str | None = None
    description: str = Field(default='', max_length=1000)
    clear_substream: bool = False
    recording_schedule: RecordingSchedule | None = None

    @field_validator('rtsp_url', 'substream_url')
    @classmethod
    def valid_url(cls, value):
        if value is None:
            return value
        if value == '':
            return value
        try:
            u = urlsplit(value)
            if u.scheme not in ('rtsp', 'rtsps') or not u.hostname or u.fragment:
                raise ValueError()
            _ = u.port
        except ValueError:
            raise ValueError('Expected RTSP URL') from None
        return value

class CameraUpdate(CameraInput):
    # Defaults permit omission, while explicit null still fails validation for
    # non-nullable fields. Update uses only fields actually supplied.
    name: str = Field(default=None, min_length=1, max_length=100)
    rtsp_url: str = None


def editable_url(value):
    if not value:
        return value
    parsed = urlsplit(value)
    # Query options may contain vendor tokens. Keep them write-only, like auth.
    return urlunsplit((parsed.scheme, parsed.netloc.rsplit('@', 1)[-1], parsed.path, '', ''))


def preserve_url_secrets(value, old):
    if not value or not old:
        return value
    current, previous = urlsplit(value), urlsplit(old)
    host = current.netloc
    if '@' not in host and '@' in previous.netloc:
        host = previous.netloc.rsplit('@', 1)[0] + '@' + host
    return urlunsplit((current.scheme, host, current.path, current.query or previous.query, ''))


class LayoutInput(BaseModel):
    columns: int = Field(ge=1, le=12)
    tiles: list[dict]

    @field_validator('tiles')
    @classmethod
    def valid_tiles(cls, v):
        if len(v) > 64:
            raise ValueError('Too many tiles')
        ids = set()
        for tile in v:
            if set(tile) != {'camera_id', 'x', 'y', 'w', 'h'} or any(type(tile[k]) is not int for k in tile) or any(tile[k] < 0 for k in ('x', 'y')) or not 1 <= tile['w'] <= 12 or not 1 <= tile['h'] <= 12:
                raise ValueError('Invalid tile')
            if tile['camera_id'] <= 0 or tile['camera_id'] in ids:
                raise ValueError('Camera tiles must be unique positive IDs')
            ids.add(tile['camera_id'])
        return v

def public(row):
    v = dict(row)
    if v.get('recording_schedule'):
        v['recording_schedule'] = json.loads(v['recording_schedule'])
    v.pop('password', None)
    for key in ('rtsp_url', 'substream_url'):
        if v.get(key):
            u = urlsplit(v[key])
            host = u.hostname or ''
            if ':' in host:
                host = f'[{host}]'
            if u.port:
                host += f':{u.port}'
            v[key] = f'{u.scheme}://{host}/…'
    v.pop('username', None)
    v['has_credentials'] = bool(row['username'] or row['password'] or urlsplit(row['rtsp_url']).username)
    return v

def rows():
    with db() as c:
        return c.execute('SELECT * FROM cameras ORDER BY id').fetchall()

def one(cid):
    with db() as c:
        row = c.execute('SELECT * FROM cameras WHERE id=?', (cid,)).fetchone()
    if not row:
        raise HTTPException(404, 'Camera not found')
    return row

@asynccontextmanager
async def lifespan(app):
    if not AUTH_USER:
        log.warning('Authentication disabled: all reachable application and media routes are public')
    elif os.getenv('COOKIE_SECURE', 'false').lower() != 'true':
        log.warning('Secure cookies disabled: use HTTPS and COOKIE_SECURE=true outside a trusted LAN')
    log.warning('MediaMTX HTTP/API/RTSP must remain private; publishing them bypasses application authentication')
    get_timezone()  # Fail startup clearly for invalid/corrupt time configuration.
    init()
    from exports import jobs
    jobs.startup()
    ROOT.mkdir(parents=True, exist_ok=True)
    gateway = MediaGateway()
    storage_monitor = StorageMonitor(ROOT, MIN_FREE)
    supervisor = ProcessSupervisor(gateway, storage_monitor)
    hooks = Webhooks()
    app.state.gateway = gateway
    app.state.media = MediaProxy(os.getenv('MEDIAMTX_WEBRTC', 'http://mediamtx:8889'), sessions,
                                lambda: (AUTH_USER, AUTH_PASS))
    app.state.media.ready = False
    app.state.supervisor = supervisor
    app.state.storage = storage_monitor
    app.state.webhooks = hooks
    app.state.reconcile_lock = asyncio.Lock()
    app.state.protection_lock = asyncio.Lock()
    async def maintenance():
        orphan_cleanup_pending = True
        while True:
            await app.state.media.reap()
            if orphan_cleanup_pending:
                try:
                    while True:
                        existing = await gateway.call('GET', '/v3/webrtcsessions/list?itemsPerPage=1000')
                        if not existing.get('items'):
                            break
                        for session in existing['items']:
                            await gateway.call('POST', f"/v3/webrtcsessions/kick/{session['id']}")
                    orphan_cleanup_pending = False
                    app.state.media.ready = True
                except Exception:
                    log.warning('media_session_cleanup_pending')
            try:
                await reconcile_state(app)
            except Exception:
                log.warning('gateway_sync_pending')
            try:
                observations = health_observations(rows(), supervisor, storage_monitor)
                hooks.observe(observations)
            except Exception:
                log.warning('health_observation_failed')
            await asyncio.sleep(5)
    async def camera_health():
        while True:
            try:
                await gateway.poll_health(rows())
            except Exception:
                log.warning('camera_health_check_pending')
            await asyncio.sleep(5)
    async def archive_maintenance():
        while True:
            try:
                unavailable = {name for name in storage_monitor.statuses if not storage_monitor.status(name)['available']}
                await asyncio.to_thread(cleanup, rows(), set(supervisor.procs), unavailable)
            except Exception:
                log.warning('archive_maintenance_failed')
            await asyncio.sleep(300)
    await refresh_storage(app, rows())
    workers = [asyncio.create_task(maintenance()), asyncio.create_task(archive_maintenance()), asyncio.create_task(hooks.run()), asyncio.create_task(camera_health())]
    try:
        yield
    finally:
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        await asyncio.to_thread(jobs.close)
        await supervisor.stop()
        await storage_monitor.close()
        await hooks.close()
        await gateway.close()
        await app.state.media.close()

app = FastAPI(title='TupoNVR', version=VERSION, lifespan=lifespan)

@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    # Pydantic includes rejected input by default, which can contain passwords
    # or secret-bearing URLs. Retain only useful field/type/message information.
    return JSONResponse(status_code=422, content={'detail':[
        {key:error[key] for key in ('loc','msg','type')} for error in exc.errors()]})

@app.middleware('http')
async def same_origin_writes(request: Request, call_next):
    origin = request.headers.get('origin')
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
        if request.headers.get('sec-fetch-site') == 'cross-site':
            return Response(status_code=403)
        if origin:
            try:
                origin_url = urlsplit(origin)
                allowed = origin_url.scheme == request.url.scheme and origin_url.netloc == request.url.netloc
            except ValueError:
                allowed = False
            if not allowed:
                return Response(status_code=403)
    response = await call_next(request)
    if request.url.path.startswith('/api/') or request.url.path == '/metrics':
        response.headers['Cache-Control'] = 'no-store'
    return response

def destination_markers():
    with db() as c:
        return {row['name']: row['expected_marker'] for row in c.execute('SELECT * FROM destinations')}

async def refresh_storage(app, current):
    markers = destination_markers()
    names = {'default', *markers, *(r['recording_destination'] for r in current)}
    await app.state.storage.refresh(names, markers)


def expected_reason(row, monitor):
    if not row['enabled']:
        return False, 'DISABLED'
    if not row['recording_enabled']:
        return False, 'PAUSED'
    if not schedule_active(dict(row).get('recording_schedule')):
        return False, 'SCHEDULED_PAUSE'
    if monitor and not monitor.status(row['recording_destination'])['ready']:
        return False, 'STORAGE_UNAVAILABLE'
    return True, None


def health_observations(current, supervisor, monitor):
    observations = {}
    expected_destinations = {r['recording_destination'] for r in current if recording_expected(r)}
    for name in monitor.statuses:
        state = monitor.status(name)
        expected = name in expected_destinations
        observations[f'storage:{name}'] = {'kind': 'storage', 'destination': name, 'reason': state['reason'] if expected else 'recording_not_expected',
                                         'expected': expected, 'failing': expected and not state['ready']}
    for row in current:
        expected, reason = expected_reason(row, monitor)
        state = supervisor.health(row['id'], expected, reason)['recording_health']
        observations[f"camera:{row['id']}"] = {'kind': 'recording', 'camera_id': row['id'], 'destination': row['recording_destination'],
                                              'reason': state.lower(), 'expected': expected, 'failing': expected and state != 'WRITING'}
    return observations


async def reconcile_state(app):
    async with app.state.reconcile_lock:
        current = rows()
        monitor = getattr(app.state, 'storage', None)
        if monitor:
            await refresh_storage(app, current)
        effective = [{**dict(row), 'recording_enabled': recording_expected(row),
                      'storage_ready': monitor.status(row['recording_destination'])['ready'] if monitor else True} for row in current]
        await app.state.supervisor.apply(effective, start=False)
        # Storage gating does not turn off live viewing or keep sources permanently connected.
        gateway_rows = [{**row, 'recording_enabled': row['recording_enabled'] and row['storage_ready']} for row in effective]
        await app.state.gateway.reconcile(gateway_rows)
        await app.state.supervisor.apply(effective)

async def reconcile(request: Request):
    await reconcile_state(request.app)

async def invalidate_diagnostics(gateway, cid):
    if hasattr(gateway, 'invalidate_check'):
        await gateway.invalidate_check(cid)
        return
    task = getattr(gateway, 'check_tasks', {}).pop(cid, None)
    if task is not None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    getattr(gateway, 'checks', {}).pop(cid, None)

@app.post('/api/login')
async def login(body: LoginInput, request: Request, response: Response):
    if not AUTH_USER and not AUTH_PASS:
        return {'ok': True}
    if not secrets.compare_digest(body.username.encode(), AUTH_USER.encode()) or not secrets.compare_digest(body.password.encode(), AUTH_PASS.encode()):
        raise HTTPException(401, 'Invalid credentials')
    sessions.revoke(request.cookies.get('nvr_session', ''))
    response.set_cookie('nvr_session', sessions.create(AUTH_USER, AUTH_PASS), httponly=True, samesite='strict', secure=os.getenv('COOKIE_SECURE','false').lower() == 'true', max_age=sessions.lifetime)
    response.headers['Cache-Control'] = 'no-store'
    return {'ok': True}

@app.post('/api/logout', status_code=204)
async def logout(request: Request, response: Response):
    sessions.revoke(request.cookies.get('nvr_session', ''))
    if hasattr(request.app.state, 'media'):
        await request.app.state.media.reap()
    response.delete_cookie('nvr_session', httponly=True, samesite='strict',
                           secure=os.getenv('COOKIE_SECURE', 'false').lower() == 'true')


@app.options('/api/media/{stream}/whep', dependencies=[Depends(auth)])
@app.post('/api/media/{stream}/whep', dependencies=[Depends(auth)])
@app.patch('/api/media/{stream}/whep/{resource}', dependencies=[Depends(auth)])
@app.delete('/api/media/{stream}/whep/{resource}', dependencies=[Depends(auth)])
async def media(request: Request, stream: str, resource: str | None = None):
    if not re.fullmatch(r'cam_[1-9][0-9]*(?:_sub)?', stream):
        raise HTTPException(404, 'Stream not found')
    if resource is not None and not re.fullmatch(r'[0-9a-fA-F-]{36}', resource):
        raise HTTPException(404, 'Media session not found')
    path = f'/{stream}/whep' + (f'/{resource}' if resource else '')
    owner = request.cookies.get('nvr_session', '') if AUTH_USER else ''
    return await request.app.state.media.forward(request, path, owner)


@app.get('/health')
def health():
    return {'ok': True}

@app.get('/ready')
async def ready(request: Request):
    try:
        await request.app.state.gateway.call('GET', '/v3/paths/list')
    except Exception:
        raise HTTPException(503, 'Media gateway unavailable')
    return {'ready': True}

@app.get('/api/cameras', dependencies=[Depends(auth)])
def cameras():
    return [public(r) for r in rows()]

@app.get('/api/config', dependencies=[Depends(auth)])
def browser_config():
    return {'webrtc_port': int(os.getenv('WEBRTC_PORT', '8889')), 'timezone': get_timezone(),
            'now': datetime.now(timezone.utc).isoformat(), 'username': AUTH_USER or None}


class TimezoneInput(BaseModel):
    timezone: str = Field(max_length=100)

    @field_validator('timezone')
    @classmethod
    def valid_zone(cls, value):
        return validate_zone(value)


class LocalTimeInput(BaseModel):
    date: CivilDate
    time: CivilTime


@app.get('/api/time', dependencies=[Depends(auth)])
def time_settings():
    return {'timezone': get_timezone(), 'now': datetime.now(timezone.utc).isoformat(),
            'timezones': sorted(valid_timezones())}


@app.put('/api/time', dependencies=[Depends(auth)])
async def change_timezone(value: TimezoneInput, request: Request):
    try:
        zone = await asyncio.to_thread(save_timezone, value.timezone)
    except (OSError, ValueError):
        raise HTTPException(503, 'Cannot save timezone configuration')
    try:
        await reconcile(request)
    except Exception:
        log.warning('timezone_sync_pending')
    return {'timezone': zone}


@app.get('/api/time/day', dependencies=[Depends(auth)])
def local_day(date: CivilDate):
    try:
        return day_range(date, get_timezone())
    except (ValueError, OverflowError):
        raise HTTPException(422, 'This local date does not exist in the configured timezone')


@app.post('/api/time/resolve', dependencies=[Depends(auth)])
def local_time(value: LocalTimeInput):
    try:
        return {'timezone': get_timezone(), 'instants': resolve_local(value.date, value.time, get_timezone())}
    except (ValueError, OverflowError) as error:
        raise HTTPException(422, str(error))

@app.get('/api/language')
def browser_language():
    # Available before login; disclose only the UI language.
    return {'default_language': DEFAULT_LANGUAGE}

@app.post('/api/cameras', dependencies=[Depends(auth)], status_code=201)
async def add(camera: CameraInput, request: Request):
    data = camera.model_dump()
    data.pop('clear_substream')
    data['recording_schedule'] = json.dumps(data['recording_schedule']) if data['recording_schedule'] else None
    if not data['rtsp_url']:
        raise HTTPException(422, 'RTSP URL required')
    if not data['substream_url']:
        data['substream_url'] = None
    with db() as c:
        cur = c.execute('INSERT INTO cameras(name,rtsp_url,username,password,enabled,recording_enabled,recording_destination,retention_days,substream_url,description,recording_schedule) VALUES(:name,:rtsp_url,:username,:password,:enabled,:recording_enabled,:recording_destination,:retention_days,:substream_url,:description,:recording_schedule)', data)
        cid = cur.lastrowid
    try:
        await reconcile(request)
    except Exception:
        log.warning('camera_sync_pending camera_id=%d', cid)
    return public(one(cid))

@app.get('/api/cameras/{cid}', dependencies=[Depends(auth)])
def camera(cid: int):
    return public(one(cid))

@app.get('/api/cameras/{cid}/edit', dependencies=[Depends(auth)])
def editable_camera(cid: int):
    row = one(cid)
    result = public(row)
    for key in ('rtsp_url', 'substream_url'):
        result[key] = editable_url(row[key])
    result['username'] = row['username'] if row['username'] else unquote(urlsplit(row['rtsp_url']).username or '')
    result['has_url_options'] = bool(urlsplit(row['rtsp_url']).query or (row['substream_url'] and urlsplit(row['substream_url']).query))
    return result

@app.patch('/api/cameras/{cid}', dependencies=[Depends(auth)])
@app.put('/api/cameras/{cid}', dependencies=[Depends(auth)])
async def update(cid: int, camera: CameraUpdate, request: Request):
    old = one(cid)
    changes = camera.model_dump(exclude_unset=True)
    clear_substream = changes.pop('clear_substream', False)
    if 'recording_schedule' in changes:
        changes['recording_schedule'] = json.dumps(changes['recording_schedule']) if changes['recording_schedule'] else None
    # Preserve the previous blank-field contract as well as partial updates.
    for key in ('rtsp_url', 'substream_url'):
        if key in changes:
            if key == 'substream_url' and changes[key] is None and request.method == 'PATCH':
                continue
            if not changes[key]:
                changes.pop(key)
            else:
                changes[key] = preserve_url_secrets(changes[key], old[key])
    if clear_substream:
        changes['substream_url'] = None
    if not changes.get('password'):
        changes.pop('password', None)
    if changes.get('username', '') is None:
        changes.pop('username', None)
    data = {**dict(old), **changes}
    if changes.get('password') and not data['username']:
        data['username'] = unquote(urlsplit(data['rtsp_url']).username or '') or None
    if changes.get('username') and not data['password']:
        data['password'] = unquote(urlsplit(data['rtsp_url']).password or '') or None
    if changes.get('username') == '':
        for key in ('rtsp_url', 'substream_url'):
            if data[key]:
                parsed = urlsplit(data[key])
                data[key] = urlunsplit((parsed.scheme, parsed.netloc.rsplit('@',1)[-1], parsed.path, parsed.query, ''))
    data['id'] = cid
    with db() as c:
        c.execute('UPDATE cameras SET name=:name,rtsp_url=:rtsp_url,username=:username,password=:password,enabled=:enabled,recording_enabled=:recording_enabled,recording_destination=:recording_destination,retention_days=:retention_days,substream_url=:substream_url,description=:description,recording_schedule=:recording_schedule WHERE id=:id', data)
    if any(data[key] != old[key] for key in ('rtsp_url', 'username', 'password')):
        await invalidate_diagnostics(request.app.state.gateway, cid)
    try:
        await reconcile(request)
    except Exception:
        log.warning('camera_sync_pending camera_id=%d', cid)
    return public(one(cid))

@app.delete('/api/cameras/{cid}', dependencies=[Depends(auth)], status_code=204)
async def remove(cid: int, request: Request):
    one(cid)
    with db() as c:
        c.execute('DELETE FROM cameras WHERE id=?', (cid,))
    await invalidate_diagnostics(request.app.state.gateway, cid)
    try:
        await reconcile(request)
    except Exception:
        log.warning('camera_sync_pending camera_id=%d', cid)

@app.post('/api/cameras/{cid}/start', dependencies=[Depends(auth)])
async def start(cid: int, request: Request):
    one(cid)
    with db() as c:
        c.execute('UPDATE cameras SET enabled=1 WHERE id=?', (cid,))
    try:
        await reconcile(request)
    except Exception:
        log.warning('camera_sync_pending camera_id=%d', cid)
    return public(one(cid))

@app.post('/api/cameras/{cid}/stop', dependencies=[Depends(auth)])
async def stop(cid: int, request: Request):
    one(cid)
    with db() as c:
        c.execute('UPDATE cameras SET enabled=0 WHERE id=?', (cid,))
    try:
        await reconcile(request)
    except Exception:
        log.warning('camera_sync_pending camera_id=%d', cid)
    return public(one(cid))

@app.get('/api/cameras/{cid}/status', dependencies=[Depends(auth)])
async def status(cid: int, request: Request):
    row = one(cid)
    sup = request.app.state.supervisor
    st = await request.app.state.gateway.status(cid)
    expected, reason = expected_reason(row, getattr(request.app.state, 'storage', None))
    health = sup.health(cid, expected, reason)
    with db() as c:
        latest = c.execute('SELECT id,started_at,ended_at,size_bytes FROM segments WHERE camera_id=? ORDER BY ended_at DESC,id DESC LIMIT 1', (cid,)).fetchone()
    recording = health['recorder_running']
    state = 'DISABLED' if not row['enabled'] else st.get('connectivity_state', 'ONLINE' if st['online'] else 'OFFLINE')
    return {'state': state, 'connectivity_state': state, 'online': st['online'] if row['enabled'] else None,
            'connectivity_checked_at': st.get('connectivity_checked_at'), 'connectivity_last_success': st.get('connectivity_last_success'),
            'recording': recording, 'recording_enabled': bool(row['recording_enabled']), 'enabled': bool(row['enabled']), 'recording_expected': recording_expected(row),
            'last_success': sup.last_ok.get(cid), 'last_error': sup.errors.get(cid), 'bytes_received': st['bytes_received'],
            'reconnects': sup.reconnects.get(cid, 0), **health, 'latest_segment': dict(latest) if latest else None,
            'diagnostics': getattr(request.app.state.gateway, 'checks', {}).get(cid)}

@app.post('/api/cameras/{cid}/check', dependencies=[Depends(auth)])
async def check(cid: int, request: Request):
    row = one(cid)
    try:
        return await request.app.state.gateway.check(row)
    except asyncio.CancelledError:
        if asyncio.current_task().cancelling():
            raise
        raise HTTPException(409, 'Camera changed; retry check') from None

@app.get('/api/storage/status', dependencies=[Depends(auth)])
def storage():
    monitor = getattr(app.state, 'storage', None)
    usage = None if monitor else shutil.disk_usage(ROOT)
    with db() as c:
        size = c.execute('SELECT COALESCE(SUM(size_bytes),0) FROM segments').fetchone()[0]
    return {'total_bytes': monitor.root_usage['total_bytes'] if monitor else usage.total, 'free_bytes': monitor.root_usage['free_bytes'] if monitor else usage.free, 'recording_bytes': size, 'min_free_bytes': int(MIN_FREE),
            'destinations': [monitor.status(name) for name in sorted(monitor.statuses)] if monitor else []}

class DestinationInput(BaseModel):
    expected_marker: str | None = Field(default=None, pattern=r'^[A-Za-z0-9_-]{1,128}$')

@app.put('/api/storage/destinations/{name}', dependencies=[Depends(auth)])
async def save_destination(name: str, value: DestinationInput, request: Request):
    import re
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name):
        raise HTTPException(422, 'Invalid destination name')
    with db() as c:
        c.execute('INSERT INTO destinations(name,expected_marker) VALUES(?,?) ON CONFLICT(name) DO UPDATE SET expected_marker=excluded.expected_marker', (name, value.expected_marker))
    try:
        await reconcile(request)
    except Exception:
        log.warning('destination_sync_pending')
    return {'name': name, 'expected_marker': value.expected_marker, **request.app.state.storage.status(name)}

class ProtectionInput(BaseModel):
    action: str = Field(pattern=r'^(create|use_existing)$')
    previous_expected_marker: str | None
    replace_existing: bool = False
    allow_local: bool = False

@app.post('/api/storage/destinations/{name}/protection', dependencies=[Depends(auth)])
async def initialize_protection(name: str, value: ProtectionInput, request: Request):
    if not re.fullmatch(NAME_PATTERN, name):
        raise HTTPException(422, 'Invalid destination name')
    configured = {'default', *destination_markers(), *(r['recording_destination'] for r in rows())}
    if name not in configured:
        raise HTTPException(404, 'Storage destination not configured')
    async with request.app.state.protection_lock:
        expected = destination_markers().get(name)
        if expected != value.previous_expected_marker:
            raise HTTPException(409, 'Storage configuration changed; refresh and retry')
        if value.action == 'create' and expected:
            raise HTTPException(409, 'Protection already configured; use existing ID or manual setup')
        monitor = request.app.state.storage
        async with monitor.lock:
            result = await monitor.inspect(name, action=value.action, allow_local=value.allow_local)
        identifier = result.get('identifier')
        if not identifier:
            messages = {'marker_exists':'Protection ID already exists; use existing ID',
                        'mount_confirmation_required':'Destination is not mounted; confirm local storage explicitly',
                        'invalid_marker':'Protection ID is empty or malformed',
                        'not_writable':'Storage not writable', 'identity_changed':'Storage identity changed'}
            raise HTTPException(409 if result.get('reason') in ('marker_exists','mount_confirmation_required') else 400,
                                messages.get(result.get('reason'), 'Storage unavailable or protection operation failed'))
        if expected and expected != identifier and not value.replace_existing:
            raise HTTPException(409, 'Storage ID mismatch; confirm adoption explicitly')
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            current = c.execute('SELECT expected_marker FROM destinations WHERE name=?', (name,)).fetchone()
            if (current['expected_marker'] if current else None) != expected:
                raise HTTPException(409, 'Storage configuration changed; refresh and retry')
            c.execute('INSERT INTO destinations(name,expected_marker) VALUES(?,?) ON CONFLICT(name) DO UPDATE SET expected_marker=excluded.expected_marker', (name, identifier))
    try:
        await reconcile(request)
    except Exception:
        log.warning('destination_sync_pending')
    return {'name':name,'expected_marker':identifier,**monitor.status(name)}

@app.get('/api/storage/destinations', dependencies=[Depends(auth)])
def destinations():
    markers = destination_markers()
    monitor = app.state.storage
    return [{**monitor.status(name), 'expected_marker': markers.get(name)} for name in sorted(monitor.statuses)]

@app.get('/api/notifications/status', dependencies=[Depends(auth)])
def notification_status():
    return app.state.webhooks.status()

class CleanupCriteria(BaseModel):
    model_config = {'extra': 'forbid'}
    camera_ids: list[int] | None = Field(default=None, min_length=1, max_length=500)
    recording_ids: list[int] | None = Field(default=None, min_length=1, max_length=10000)
    start: datetime | None = None
    end: datetime | None = None

    @field_validator('camera_ids', 'recording_ids')
    @classmethod
    def positive_ids(cls, value):
        if value is not None and any(item < 1 for item in value):
            raise ValueError('Expected positive IDs')
        return sorted(set(value)) if value is not None else None


def cleanup_matches(value):
    where, args = [], []
    for field, ids in [('camera_id', value.camera_ids), ('id', value.recording_ids)]:
        if ids is not None:
            where.append(field + ' IN (' + ','.join('?' for _ in ids) + ')')
            args.extend(ids)
    if value.start is not None or value.end is not None:
        if value.start is None or value.end is None:
            raise HTTPException(422, 'Both start and end are required')
        start, end = utc_time(value.start), utc_time(value.end)
        if start >= end:
            raise HTTPException(422, 'Start must precede end')
        where.extend(['started_at<?', 'ended_at>?'])
        args.extend([end.isoformat(), start.isoformat()])
    with db() as c:
        return [dict(row) for row in c.execute('SELECT * FROM segments' +
                (' WHERE ' + ' AND '.join(where) if where else '') + ' ORDER BY started_at,id', args)]


cleanup_jobs = {}
cleanup_jobs_lock = threading.RLock()


@app.post('/api/recordings/cleanup/query', dependencies=[Depends(auth)])
def cleanup_query(value: CleanupCriteria, offset: int = 0, limit: int = 100):
    matches = cleanup_matches(value)
    offset, limit = max(0, offset), max(1, min(500, limit))
    return {'total': len(matches), 'recordings': [segment_metadata(row) for row in matches[offset:offset + limit]]}


@app.post('/api/recordings/cleanup/preview', dependencies=[Depends(auth)])
def cleanup_preview(value: CleanupCriteria):
    import video
    records = cleanup_matches(value)
    token = secrets.token_urlsafe(24)
    found = {row['id'] for row in records}
    not_found = len(set(value.recording_ids or []) - found)
    with video.ARCHIVE_LOCK:
        eligible = [row for row in records if not video.is_active_path(Path(row['path']), row['camera_id'])]
    preview = {'token': token, 'count': len(eligible), 'size_bytes': sum(row['size_bytes'] for row in eligible),
               'cameras': sorted({row['camera_id'] for row in records}), 'criteria': value.model_dump(mode='json'),
               'active_excluded': len(records) - len(eligible), 'not_found': not_found, 'clear_all': all(item is None for item in value.model_dump().values())}
    with cleanup_jobs_lock:
        for key, job in list(cleanup_jobs.items()):
            if job['state'] != 'running' and time.monotonic() - job['created'] > 900:
                del cleanup_jobs[key]
        if len(cleanup_jobs) >= 32:
            raise HTTPException(429, 'Too many cleanup previews; retry later')
        cleanup_jobs[token] = {'created': time.monotonic(), 'state': 'preview', 'preview': preview,
                               'records': eligible, 'processed': len(records) - len(eligible) + not_found, 'total': len(records) + not_found, 'deleted': 0,
                               'reclaimed_bytes': 0, 'active': len(records) - len(eligible), 'missing': not_found, 'failed': 0}
    return preview


class CleanupConfirmation(BaseModel):
    model_config = {'extra': 'forbid'}
    token: str
    confirmation: str


def cleanup_worker(job):
    import video
    from recording_cleanup import MetadataUpdateError, delete_indexed
    try:
        for original in job['records']:
            result, size = 'failed', 0
            try:
                with video.ARCHIVE_LOCK:
                    with db() as c:
                        row = c.execute('SELECT * FROM segments WHERE id=? AND path=?', (original['id'], original['path'])).fetchone()
                    if row is not None and any(row[key] != original[key] for key in ('camera_id', 'started_at', 'ended_at', 'size_bytes')):
                        result, size = 'failed', 0
                    else:
                        result, size = delete_indexed(row) if row else ('missing', 0)
            except MetadataUpdateError as error:
                size = error.reclaimed_bytes
                log.exception('manual_cleanup_index_update_failed segment_id=%d', original['id'])
            except Exception:
                log.exception('manual_cleanup_failed segment_id=%d', original['id'])
            with cleanup_jobs_lock:
                job[result] += 1
                job['reclaimed_bytes'] += size
                job['processed'] += 1
    finally:
        with cleanup_jobs_lock:
            job['state'] = 'done'
            job['records'] = []


@app.post('/api/recordings/cleanup/delete', dependencies=[Depends(auth)], status_code=202)
def cleanup_execute(value: CleanupConfirmation):
    with cleanup_jobs_lock:
        job = cleanup_jobs.get(value.token)
        if job is None or time.monotonic() - job['created'] > 900 and job['state'] == 'preview':
            raise HTTPException(410, 'Cleanup preview expired; preview again')
        expected = 'DELETE' if job['preview']['clear_all'] else 'confirm'
        if value.confirmation != expected:
            raise HTTPException(422, 'Explicit deletion confirmation required')
        if job['state'] == 'preview':
            job['state'] = 'running'
            threading.Thread(target=cleanup_worker, args=(job,), daemon=True).start()
    return {'token': value.token}


@app.get('/api/recordings/cleanup/progress/{token}', dependencies=[Depends(auth)])
def cleanup_progress(token: str):
    with cleanup_jobs_lock:
        job = cleanup_jobs.get(token)
        if job is None:
            raise HTTPException(404, 'Cleanup not found')
        return {key: job[key] for key in ('state', 'processed', 'total', 'deleted', 'reclaimed_bytes', 'active', 'missing', 'failed')}


@app.get('/api/recordings', dependencies=[Depends(auth)])
def recordings(camera_id: int | None = None, date: str | None = None, limit: int = 200, offset: int = 0,
               start: datetime | None = None, end: datetime | None = None, camera_ids: str | None = None):
    if date:
        try:
            parsed = datetime.strptime(date, '%Y-%m-%d')
            if parsed.strftime('%Y-%m-%d') != date:
                raise ValueError('Expected YYYY-MM-DD')
        except ValueError:
            raise HTTPException(422, 'Invalid date')
    limit = max(1, min(500, limit))
    offset = max(0, offset)
    where = []
    args = []
    if camera_id is not None:
        where.append('camera_id=?'); args.append(camera_id)
    if date:
        where.append('substr(started_at,1,10)=?'); args.append(date)
    if camera_ids is not None:
        try:
            ids = sorted(set(int(value) for value in camera_ids.split(',')))
            if not ids or min(ids) < 1:
                raise ValueError()
        except ValueError:
            raise HTTPException(422, 'Expected positive camera IDs')
        where.append('camera_id IN (' + ','.join('?' for _ in ids) + ')'); args.extend(ids)
    if start is not None or end is not None:
        if start is None or end is None:
            raise HTTPException(422, 'Both start and end are required')
        start, end = archive_range(start, end)
        where.extend(['started_at<?', 'ended_at>?']); args.extend([end.isoformat(), start.isoformat()])
    query = 'SELECT id,camera_id,started_at,ended_at,size_bytes FROM segments' + (' WHERE ' + ' AND '.join(where) if where else '') + ' ORDER BY started_at DESC LIMIT ? OFFSET ?'
    with db() as c:
        return [dict(r) for r in c.execute(query, (*args, limit, offset))]

def utc_time(value):
    if value.tzinfo is None:
        raise HTTPException(422, 'A timezone offset or Z is required')
    return value.astimezone(timezone.utc)


def segment_metadata(row):
    return {key: row[key] for key in ('id', 'camera_id', 'started_at', 'ended_at', 'size_bytes')}


def archive_range(start, end):
    start, end = utc_time(start), utc_time(end)
    if not start < end or end - start > timedelta(days=31):
        raise HTTPException(422, 'Choose a range between zero and 31 days')
    return start, end


def availability(records, start, end):
    intervals = []
    for row in records:
        a = max(start, datetime.fromisoformat(row['started_at']))
        b = min(end, datetime.fromisoformat(row['ended_at']))
        if intervals and a <= intervals[-1][1]:
            intervals[-1][1] = max(intervals[-1][1], b)
        else:
            intervals.append([a, b])
    gaps = []
    cursor = start
    for a, b in intervals:
        if cursor < a:
            gaps.append({'start': cursor.isoformat(), 'end': a.isoformat()})
        cursor = b
    if cursor < end:
        gaps.append({'start': cursor.isoformat(), 'end': end.isoformat()})
    return {'intervals': [{'start': a.isoformat(), 'end': b.isoformat()} for a, b in intervals], 'gaps': gaps}


class CameraSelection(BaseModel):
    camera_ids: list[int] = Field(min_length=1)

    @field_validator('camera_ids')
    @classmethod
    def valid_ids(cls, values):
        if any(value < 1 for value in values):
            raise ValueError('Expected positive camera IDs')
        return sorted(set(values))


class TimelineInput(CameraSelection):
    start: datetime
    end: datetime


class ResolveInput(CameraSelection):
    time: datetime


@app.post('/api/recordings/timelines', dependencies=[Depends(auth)])
def timelines(value: TimelineInput):
    start, end = archive_range(value.start, value.end)
    ids = value.camera_ids
    with db() as c:
        records = c.execute('SELECT camera_id,started_at,ended_at FROM segments WHERE camera_id IN ('
                            + ','.join('?' for _ in ids) + ') AND started_at<? AND ended_at>? ORDER BY started_at,id LIMIT 20001',
                            (*ids, end.isoformat(), start.isoformat())).fetchall()
    if len(records) > 20000:
        raise HTTPException(422, 'Too many segments; choose a shorter time range')
    grouped = {cid: [] for cid in ids}
    for row in records:
        grouped[row['camera_id']].append(row)
    return {'start': start.isoformat(), 'end': end.isoformat(),
            'cameras': {str(cid): availability(grouped[cid], start, end) for cid in ids}}


@app.post('/api/recordings/resolve', dependencies=[Depends(auth)])
def resolve_recordings(value: ResolveInput):
    stamp = utc_time(value.time).isoformat()
    ids = value.camera_ids
    # One round trip/SQL statement; correlated lookups use the camera/time index.
    placeholders = ','.join('(?)' for _ in ids)
    query = f'''WITH requested(camera_id) AS (VALUES {placeholders}), choices AS (
        SELECT camera_id,
          (SELECT id FROM segments WHERE camera_id=requested.camera_id AND started_at<=? AND ended_at>?
           ORDER BY started_at DESC,id DESC LIMIT 1) AS current_id,
          (SELECT id FROM segments WHERE camera_id=requested.camera_id AND started_at>?
           ORDER BY started_at,id LIMIT 1) AS next_id FROM requested)
        SELECT choices.camera_id AS requested_id, choices.current_id, segments.* FROM choices
        LEFT JOIN segments ON segments.id IN (choices.current_id,choices.next_id)'''
    result = {str(cid): {'segment': None, 'next_segment': None, 'seek_seconds': 0} for cid in ids}
    with db() as c:
        for row in c.execute(query, (*ids, stamp, stamp, stamp)):
            if row['id'] is None:
                continue
            item = result[str(row['requested_id'])]
            if row['id'] == row['current_id']:
                item['segment'] = segment_metadata(row)
                item['seek_seconds'] = (datetime.fromisoformat(stamp) - datetime.fromisoformat(row['started_at'])).total_seconds()
            else:
                item['next_segment'] = segment_metadata(row)
    return {'time': stamp, 'cameras': result}


@app.get('/api/recordings/timeline', dependencies=[Depends(auth)])
def timeline(camera_id: int, start: datetime, end: datetime):
    start, end = archive_range(start, end)
    with db() as c:
        records = c.execute('SELECT started_at,ended_at FROM segments WHERE camera_id=? AND started_at<? AND ended_at>? ORDER BY started_at,id LIMIT 5001', (camera_id, end.isoformat(), start.isoformat())).fetchall()
    if len(records) > 5000:
        raise HTTPException(422, 'Too many segments; choose a shorter time range')
    return availability(records, start, end)


@app.get('/api/recordings/at', dependencies=[Depends(auth)])
def recording_at(camera_id: int, time: datetime):
    stamp = utc_time(time)
    with db() as c:
        row = c.execute('SELECT * FROM segments WHERE camera_id=? AND started_at<=? AND ended_at>? ORDER BY started_at DESC,id DESC LIMIT 1', (camera_id, stamp.isoformat(), stamp.isoformat())).fetchone()
    if row is None:
        raise HTTPException(404, 'No recording at this time')
    return {'segment': segment_metadata(row), 'seek_seconds': (stamp - datetime.fromisoformat(row['started_at'])).total_seconds()}


@app.get('/api/recordings/{sid}/adjacent', dependencies=[Depends(auth)])
def adjacent(sid: int, direction: str = 'next'):
    if direction not in ('next', 'previous'):
        raise HTTPException(422, 'Expected next or previous')
    with db() as c:
        current = c.execute('SELECT * FROM segments WHERE id=?', (sid,)).fetchone()
        if current is None:
            raise HTTPException(404, 'Segment not found')
        comparison, order = ('>', 'ASC') if direction == 'next' else ('<', 'DESC')
        extra = ' AND ended_at>?' if direction == 'next' else ''
        args = (current['camera_id'], current['started_at'], current['id']) + ((current['ended_at'],) if direction == 'next' else ())
        row = c.execute(f'SELECT * FROM segments WHERE camera_id=? AND (started_at,id){comparison}(?,?){extra} ORDER BY started_at {order},id {order} LIMIT 1', args).fetchone()
    gap = max(0, (datetime.fromisoformat(row['started_at']) - datetime.fromisoformat(current['ended_at'])).total_seconds()) if row and direction == 'next' else 0
    seek = max(0, (datetime.fromisoformat(current['ended_at']) - datetime.fromisoformat(row['started_at'])).total_seconds()) if row and direction == 'next' else 0
    return {'segment': segment_metadata(row) if row else None, 'gap_seconds': gap, 'seek_seconds': seek}


def segment(sid):
    with db() as c:
        s = c.execute('SELECT * FROM segments WHERE id=?', (sid,)).fetchone()
    if not s:
        raise HTTPException(404)
    root = ROOT
    path = Path(s['path'])
    monitor = getattr(app.state, 'storage', None)
    if monitor and path.is_relative_to(ROOT):
        destination = path.relative_to(ROOT).parts[0]
        if destination in monitor.statuses and not monitor.status(destination)['available']:
            raise HTTPException(503, 'Recording storage unavailable')
    if not path.resolve().is_relative_to(root) or path.is_symlink() or not path.is_file():
        raise HTTPException(404)
    return path

class ExportInput(BaseModel):
    camera_id: int = Field(gt=0)
    start: datetime
    end: datetime
    mode: str = Field(default='exact', pattern=r'^(exact|copy)$')


@app.post('/api/recordings/exports', dependencies=[Depends(auth)], status_code=202)
def create_export(value: ExportInput):
    from exports import jobs
    start, end = archive_range(value.start, value.end)
    return jobs.create(value.camera_id, start, end, value.mode, segment)


@app.get('/api/recordings/exports/{token}', dependencies=[Depends(auth)])
def export_status(token: str):
    from exports import jobs
    return jobs.status(token)


@app.get('/api/recordings/exports/{token}/download', dependencies=[Depends(auth)])
def export_download(token: str):
    from exports import jobs
    job = jobs.download(token)

    class ExportResponse(FileResponse):
        async def __call__(self, scope, receive, send):
            try:
                await super().__call__(scope, receive, send)
            finally:
                jobs.finish_download(token)

    return ExportResponse(job['directory'] / 'export.mp4', media_type='video/mp4', filename=job['filename'])


@app.get('/api/recordings/{sid}', dependencies=[Depends(auth)])
def play(sid: int):
    return FileResponse(segment(sid), media_type='video/mp4')

@app.get('/api/recordings/{sid}/download', dependencies=[Depends(auth)])
def download(sid: int):
    path = segment(sid)
    return FileResponse(path, media_type='video/mp4', filename=path.name)

@app.delete('/api/recordings/{sid}', dependencies=[Depends(auth)], status_code=204)
def delete(sid: int):
    import video
    from recording_cleanup import MetadataUpdateError, delete_indexed
    with video.ARCHIVE_LOCK:
        with db() as c:
            row = c.execute('SELECT * FROM segments WHERE id=?', (sid,)).fetchone()
        if row is None:
            raise HTTPException(404, 'Recording not found')
        try:
            result, _ = delete_indexed(row)
        except MetadataUpdateError:
            raise HTTPException(503, 'File deletion completed but index update failed; retry to reconcile')
        if result == 'active':
            raise HTTPException(409, 'Active recording excluded')
        if result == 'failed':
            raise HTTPException(503, 'Recording deletion refused or failed')

@app.get('/api/layout', dependencies=[Depends(auth)])
def layout():
    with db() as c:
        row = c.execute('SELECT payload FROM layouts WHERE name=?', ('default',)).fetchone()
    return json.loads(row[0]) if row else {'columns': 2, 'tiles': []}

@app.put('/api/layout', dependencies=[Depends(auth)])
def save_layout(value: LayoutInput):
    data = value.model_dump()
    with db() as c:
        c.execute('INSERT INTO layouts(name,payload) VALUES(?,?) ON CONFLICT(name) DO UPDATE SET payload=excluded.payload', ('default', json.dumps(data)))
    return data

@app.get('/api/dashboard', dependencies=[Depends(auth)])
async def dashboard(request: Request):
    rr = rows()
    states = await asyncio.gather(*(request.app.state.gateway.status(r['id']) for r in rr))
    sup = request.app.state.supervisor
    monitor = getattr(request.app.state, 'storage', None)
    writing = sum(sup.health(r['id'], *expected_reason(r, monitor))['recording_health'] == 'WRITING' for r in rr)
    return {'cameras': len(rr), 'online': sum(s['online'] is True and bool(r['enabled']) for r, s in zip(rr, states)), 'recording': sum(p.returncode is None for p in sup.procs.values()), 'writing': writing, 'errors': [{'camera_id': r['id'], 'message': sup.errors[r['id']]} for r in rr if r['id'] in sup.errors and recording_expected(r)], 'storage': storage()}

@app.get('/metrics', dependencies=[Depends(auth)], response_class=PlainTextResponse)
async def metrics(request: Request):
    sup = request.app.state.supervisor
    lines = []
    for row in rows():
        cid = row['id']
        online = await request.app.state.gateway.status(cid)
        lines += [f'camera_online{{camera_id="{cid}"}} {int(online['online']) if online['online'] is not None and row['enabled'] else 'NaN'}', f'camera_recording{{camera_id="{cid}"}} {int(cid in sup.procs and sup.procs[cid].returncode is None)}', f'camera_reconnects_total{{camera_id="{cid}"}} {sup.reconnects.get(cid,0)}', f'recording_errors_total{{camera_id="{cid}"}} {sup.recording_errors.get(cid,0)}']
        expected, reason = expected_reason(row, getattr(request.app.state, 'storage', None))
        health = sup.health(cid, expected, reason)
        lines += [f'camera_recording_expected{{camera_id="{cid}"}} {int(recording_expected(row))}', f'camera_recording_writing{{camera_id="{cid}"}} {int(health["recording_health"] == "WRITING")}']
        if health['progress_age_seconds'] is not None:
            lines.append(f'camera_recording_progress_age_seconds{{camera_id="{cid}"}} {health["progress_age_seconds"]}')
    s = storage()
    for destination in s['destinations']:
        name = destination['name']
        lines.append(f'storage_destination_ready{{destination="{name}"}} {int(destination["ready"])}')
        if destination['free_bytes'] is not None:
            lines.append(f'storage_destination_free_bytes{{destination="{name}"}} {destination["free_bytes"]}')
    lines += [f'storage_free_bytes {s["free_bytes"] if s["free_bytes"] is not None else "NaN"}', f'recording_bytes_total {s["recording_bytes"]}']
    return '\n'.join(lines) + '\n'

def mount_ui(application: FastAPI, directory: Path):
    from fastapi.staticfiles import StaticFiles
    @application.get('/cameras/{camera_id}/live', include_in_schema=False)
    def single_camera_ui(camera_id: str):
        return FileResponse(directory / 'index.html')

    @application.get('/overview/', include_in_schema=False)
    @application.get('/multiview/', include_in_schema=False)
    @application.get('/archive/', include_in_schema=False)
    @application.get('/settings/', include_in_schema=False)
    @application.get('/overview', include_in_schema=False)
    @application.get('/multiview', include_in_schema=False)
    @application.get('/archive', include_in_schema=False)
    @application.get('/settings', include_in_schema=False)
    @application.get('/account', include_in_schema=False)
    @application.get('/account/', include_in_schema=False)
    def application_ui():
        return FileResponse(directory / 'index.html')

    application.mount('/', StaticFiles(directory=directory, html=True), name='ui')


static = Path('/app/static')
if static.exists():
    mount_ui(app, static)
