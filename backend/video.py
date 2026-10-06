import asyncio
import logging
import math
import json
import time
import threading
import os
import random
import re
import shutil
import signal
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit
import httpx
from db import db

log = logging.getLogger('nvr.video')
ROOT = Path(os.getenv('DEFAULT_RECORDING_PATH', '/recordings')).resolve()
MTX = os.getenv('MEDIAMTX_API', 'http://mediamtx:9997')
MTX_RTSP_HOST = os.getenv('MEDIAMTX_RTSP_HOST', 'mediamtx')
SEGMENT_SECONDS = int(os.getenv('SEGMENT_SECONDS', '600'))
MIN_FREE = float(os.getenv('MIN_FREE_SPACE_GB', '5')) * 1024**3
ARCHIVE_LOCK = threading.RLock()


def source_url(row, sub=False):
    raw = row['substream_url'] if sub else row['rtsp_url']
    if not raw:
        return None
    u = urlsplit(raw)
    host = u.hostname or ''
    if ':' in host:
        host = f'[{host}]'
    if u.port:
        host += f':{u.port}'
    # Explicit fields override URL credentials; otherwise preserve the encoded
    # userinfo verbatim. Rebuilding only hostname used to silently strip it.
    if row['username']:
        host = f"{quote(row['username'], safe='')}:{quote(row['password'] or '', safe='')}@{host}"
    elif '@' in u.netloc:
        host = f"{u.netloc.rsplit('@', 1)[0]}@{host}"
    return urlunsplit((u.scheme, host, u.path, u.query, ''))


def directory(row):
    name = row['recording_destination']
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name):
        raise ValueError('Invalid storage destination')
    path = (ROOT / name / str(row['id'])).resolve()
    if not path.is_relative_to(ROOT) or path.is_symlink():
        raise ValueError('Invalid storage path')
    return path


class MediaGateway:
    def __init__(self):
        self.client = httpx.AsyncClient(base_url=MTX, timeout=5, trust_env=False)
        self.names = set()
        self.sources = {}
        self.checks = {}
        self.check_tasks = {}
        self.connectivity = {}
        self.check_sources = {}
        self.next_probe = {}
        self.check_limit = asyncio.Semaphore(2)
        self.lock = asyncio.Lock()

    async def call(self, method, endpoint, body=None):
        r = await self.client.request(method, endpoint, json=body)
        if r.status_code >= 400:
            raise RuntimeError(f'Media gateway HTTP {r.status_code}')
        return r.json() if r.content else {}

    async def reconcile(self, rows):
        async with self.lock:
            await self._reconcile(rows)

    async def _reconcile(self, rows):
        wanted = {}
        for row in rows:
            if row['enabled']:
                wanted[f"cam_{row['id']}"] = source_url(row)
                if row['substream_url']:
                    wanted[f"cam_{row['id']}_sub"] = source_url(row, True)
        configured = await self.call('GET', '/v3/config/paths/list?itemsPerPage=1000')
        existing = {i['name'] for i in configured.get('items', [])}
        for name in existing - wanted.keys():
            if re.fullmatch(r'cam_[0-9]+(?:_sub)?', name) or re.fullmatch(r'check_[0-9]+_[0-9a-f]{32}', name):
                await self.call('DELETE', f'/v3/config/paths/delete/{name}')
        current = {}
        for name, src in wanted.items():
            demand = name.endswith('_sub') or not any(r['enabled'] and r['recording_enabled'] and name == f"cam_{r['id']}" for r in rows)
            body = {'source': src, 'rtspTransport': 'tcp', 'sourceOnDemand': demand}
            signature = (src, demand)
            if name not in existing or self.sources.get(name) != signature:
                endpoint = f'/v3/config/paths/{"patch" if name in existing else "add"}/{name}'
                await self.call('PATCH' if name in existing else 'POST', endpoint, body)
            current[name] = signature
        self.sources = current
        self.names = set(wanted)

    def connectivity_status(self, camera_id, now=None):
        health = self.connectivity.get(camera_id)
        now = time.monotonic() if now is None else now
        if health is None or now - health['_tick'] > 180:
            return {'online': None, 'connectivity_state': 'UNKNOWN', 'connectivity_checked_at': health['checked_at'] if health else None,
                    'connectivity_last_success': health.get('last_success') if health else None}
        return {'online': health['online'], 'connectivity_state': 'ONLINE' if health['online'] is True else 'OFFLINE' if health['online'] is False else 'UNKNOWN',
                'connectivity_checked_at': health['checked_at'], 'connectivity_last_success': health.get('last_success')}

    async def status(self, camera_id):
        # MediaMTX counters describe consumption, never camera connectivity.
        result = self.connectivity_status(camera_id)
        result['bytes_received'] = 0
        try:
            path = await self.call('GET', f'/v3/paths/get/cam_{camera_id}')
            result['bytes_received'] = path.get('bytesReceived', 0)
        except Exception:
            pass
        return result

    def observe_probe(self, cid, result, now=None):
        now = time.monotonic() if now is None else now
        previous = self.connectivity.get(cid, {})
        failures = 0 if result['ok'] else previous.get('failures', 0) + 1
        online = True if result['ok'] else False if failures >= 2 else previous.get('online')
        self.connectivity[cid] = {'online': online, 'failures': failures, 'checked_at': result['checked_at'],
                                  'last_success': result['checked_at'] if result['ok'] else previous.get('last_success'), '_tick': now}
        self.next_probe[cid] = now + (15 if failures == 1 else 60)

    async def poll_health(self, rows):
        # One backend scheduler; requests and viewers never run periodic probes.
        enabled = {row['id']: dict(row) for row in rows if row['enabled']}
        for cid in set(self.check_sources) - enabled.keys():
            # Disabled cameras can still be checked explicitly. Let a bounded
            # operator probe finish before discarding its runtime cache.
            if cid not in self.check_tasks:
                await self.invalidate_check(cid)
        due = []
        for cid, row in enabled.items():
            signature = tuple(row[key] for key in ('rtsp_url', 'username', 'password'))
            if self.check_sources.get(cid) != signature:
                await self.invalidate_check(cid)
            if time.monotonic() >= self.next_probe.get(cid, 0):
                due.append(self.check(row, force=False))
        await asyncio.gather(*due, return_exceptions=True)

    async def invalidate_check(self, cid):
        task = self.check_tasks.pop(cid, None)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        for collection in (self.checks, self.connectivity, self.next_probe, self.check_sources):
            collection.pop(cid, None)

    async def check(self, row, force=True):
        cid = row['id']
        signature = tuple(row[key] for key in ('rtsp_url', 'username', 'password'))
        if cid in self.check_sources and self.check_sources[cid] != signature:
            await self.invalidate_check(cid)
        self.check_sources[cid] = signature
        if cid not in self.check_tasks:
            # Briefly reuse manual diagnostics too, so multiple operators cannot
            # accidentally generate rapid consecutive failures or extra sessions.
            if cid in self.checks and (not force and time.monotonic() < self.next_probe.get(cid, 0) or
                                      time.monotonic() - self.connectivity[cid]['_tick'] < 10):
                return self.checks[cid]
            self.check_tasks[cid] = asyncio.create_task(self._check(dict(row)))
        task = self.check_tasks[cid]
        try:
            return await asyncio.shield(task)
        finally:
            if task.done() and self.check_tasks.get(cid) is task:
                self.check_tasks.pop(cid, None)

    async def _check(self, row):
        async with self.check_limit:
            cid = row['id']
            proc = None
            result = {'ok': False, 'video': None, 'message': 'Cannot read video; check source credentials, connectivity and codec'}
            try:
                # Probe the configured source itself, not an on-demand relay or
                # a recorder. No shell, transcoding, stderr capture, or URL logs.
                proc = await asyncio.create_subprocess_exec('ffprobe', '-v', 'quiet', '-rtsp_transport', 'tcp', '-timeout', '8000000',
                    '-analyzeduration', '1000000', '-probesize', '262144',
                    '-show_entries', 'stream=codec_type,codec_name,width,height,avg_frame_rate', '-of', 'json',
                    source_url(row), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                output, _ = await asyncio.wait_for(proc.communicate(), 10)
                if proc.returncode:
                    raise ValueError('No stream')
                stream = next(s for s in json.loads(output)['streams'] if s['codec_type'] == 'video' and s.get('codec_name') and s.get('width', 0) > 0 and s.get('height', 0) > 0)
                video = {key: stream.get(key) for key in ('codec_name', 'width', 'height', 'avg_frame_rate')}
                result = {'ok': True, 'video': video, 'message': 'Video stream readable',
                          'browser_compatibility': 'likely' if video['codec_name'] == 'h264' else 'browser_dependent'}
            except Exception:
                pass  # Never expose ffprobe output or camera-supplied metadata.
            finally:
                if proc is not None and proc.returncode is None:
                    proc.kill()
                    await proc.wait()
            result['checked_at'] = datetime.now(timezone.utc).isoformat()
            self.checks[cid] = result
            self.observe_probe(cid, result)
            return result

    async def close(self):
        for task in self.check_tasks.values():
            task.cancel()
        await asyncio.gather(*self.check_tasks.values(), return_exceptions=True)
        await self.client.aclose()


class ProcessSupervisor:
    def __init__(self, gateway, storage=None):
        self.gateway = gateway
        self.storage = storage
        self.progress = {}
        self.tasks = {}
        self.procs = {}
        self.errors = {}
        self.reconnects = {}
        self.recording_errors = {}
        self.last_ok = {}
        self.lock = asyncio.Lock()
        self.running = True

    async def apply(self, rows, start=True):
        async with self.lock:
            rows = [dict(r) for r in rows]
            wanted = {r['id']: r for r in rows if r['enabled'] and r['recording_enabled'] and r.get('storage_ready', True)}
            stopping = []
            for cid in list(self.tasks):
                if cid not in wanted or any(self.tasks[cid][0][key] != wanted[cid][key] for key in ('rtsp_url', 'username', 'password', 'recording_destination')) or self.tasks[cid][1].done():
                    task = self.tasks.pop(cid)[1]
                    task.cancel()
                    stopping.append(task)
            # Shared schedule/storage transitions stop all affected recorders
            # together, rather than adding a 15-second grace period per camera.
            await asyncio.gather(*stopping, return_exceptions=True)
            for cid, row in wanted.items():
                if start and cid not in self.tasks:
                    self.tasks[cid] = (dict(row), asyncio.create_task(self.run(dict(row)), name=f'recorder-{cid}'))

    async def stop(self):
        self.running = False
        async with self.lock:
            tasks = [v[1] for v in self.tasks.values()]
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.tasks.clear()

    async def run(self, row):
        cid = row['id']
        delay = 2
        try:
            while self.running:
                now = datetime.now(timezone.utc)
                hour_name = now.strftime('%Y/%m/%d/%H')
                if self.storage:
                    if not await self.storage.prepare(row, hour_name):
                        self.errors[cid] = 'Storage unavailable, not writable, or below reserve'
                        await asyncio.sleep(5)
                        continue
                    dest = ROOT / row['recording_destination'] / str(cid)
                    hour = dest / hour_name
                else:
                    dest = directory(row)
                    dest.mkdir(parents=True, exist_ok=True)
                    if shutil.disk_usage(dest).free < MIN_FREE:
                        self.errors[cid] = 'Storage free space below reserve'
                        await asyncio.sleep(30)
                        continue
                    hour = dest / hour_name
                    hour.mkdir(parents=True, exist_ok=True)
                output = str(hour / '%Y%m%dT%H%M%S.mp4')
                args = ['ffmpeg', '-hide_banner', '-nostdin', '-loglevel', 'error', '-progress', 'pipe:1', '-stats_period', '2',
                        '-rtsp_transport', 'tcp', '-timeout', '15000000', '-i', f'rtsp://{MTX_RTSP_HOST}:8554/cam_{cid}',
                        '-map', '0:v:0', '-an', '-c:v', 'copy', '-f', 'segment',
                        '-segment_time', str(SEGMENT_SECONDS), '-reset_timestamps', '1',
                        '-strftime', '1', '-segment_format', 'mp4', output]
                proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env={**os.environ, 'TZ': 'UTC'})
                self.procs[cid] = proc
                drain = asyncio.create_task(self.drain_errors(cid, proc))
                started = asyncio.get_running_loop().time()
                self.progress[cid] = {'started_tick': started, 'last_tick': None, 'last_progress_at': None, 'frames': 0, 'out_time_us': 0}
                progress = asyncio.create_task(self.drain_progress(cid, proc))
                try:
                    # Rotate at the top of each UTC hour so paths remain truthful.
                    until_hour = 3600 - (now.minute * 60 + now.second) + 1
                    rotated = False
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=until_hour)
                    except asyncio.TimeoutError:
                        rotated = True
                        proc.send_signal(signal.SIGINT)
                        try:
                            await asyncio.wait_for(proc.wait(), timeout=15)
                        except asyncio.TimeoutError:
                            proc.kill()
                            await proc.wait()
                    elapsed = asyncio.get_running_loop().time() - started
                    if elapsed > 30:
                        delay = 2
                    else:
                        delay = min(delay * 2, 60)
                    if proc.returncode not in (0, 255):
                        self.recording_errors[cid] = self.recording_errors.get(cid, 0) + 1
                    if not rotated:
                        self.reconnects[cid] = self.reconnects.get(cid, 0) + 1
                finally:
                    if proc.returncode is None:
                        proc.send_signal(signal.SIGINT)
                        try:
                            await asyncio.wait_for(proc.wait(), timeout=15)
                        except asyncio.TimeoutError:
                            proc.kill()
                            await proc.wait()
                    self.procs.pop(cid, None)
                    await asyncio.gather(drain, progress, return_exceptions=True)
                    if not self.storage or await self.storage.available(row['recording_destination']):
                        await asyncio.to_thread(index_segments, cid, dest)
                await asyncio.sleep(delay + random.random())
        except asyncio.CancelledError:
            raise
        except Exception:
            self.errors[cid] = 'Recorder supervisor failed; retrying on next sync'
            log.exception('supervisor_failed camera_id=%d', cid)

    async def drain_progress(self, cid, proc):
        while True:
            line = await proc.stdout.readline()
            if not line:
                return
            key, _, raw = line.decode('ascii', errors='ignore').strip().partition('=')
            if key not in ('frame', 'out_time_us'):
                continue
            try:
                value = int(raw)
            except ValueError:
                continue
            state = self.progress[cid]
            field = 'frames' if key == 'frame' else key
            if value > state[field]:
                state[field] = value
                state['last_tick'] = time.monotonic()
                state['last_progress_at'] = datetime.now(timezone.utc).isoformat()
                self.last_ok[cid] = state['last_progress_at']
                self.errors.pop(cid, None)

    def health(self, cid, expected, reason=None, now=None):
        running = cid in self.procs and self.procs[cid].returncode is None
        progress = self.progress.get(cid, {})
        now = time.monotonic() if now is None else now
        if not expected:
            state = reason or 'PAUSED'
        elif not running:
            state = 'RECONNECTING'
        elif progress.get('last_tick') is not None and now - progress['last_tick'] <= 30:
            state = 'WRITING'
        elif now - progress.get('started_tick', now) <= 30:
            state = 'STARTING'
        else:
            state = 'STALLED'
        return {'recording_health': state, 'recorder_running': running,
                'last_progress_at': progress.get('last_progress_at'),
                'progress_age_seconds': max(0, now - progress['last_tick']) if progress.get('last_tick') is not None else None}

    async def drain_errors(self, cid, proc):
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            # FFmpeg reads only a local URL; avoid logging untrusted camera-supplied metadata.
            self.errors[cid] = 'Recorder error; inspect connectivity and codec'
            log.warning('recorder_error camera_id=%d', cid)


def safe_archive_path(path):
    return path.resolve().is_relative_to(ROOT) and not path.is_symlink()


def index_segments(cid, root, active=False):
    with ARCHIVE_LOCK:
        _index_segments(cid, root, active)


def _index_segments(cid, root, active=False):
    if not root.exists() or not safe_archive_path(root):
        return
    hour = datetime.now(timezone.utc).strftime('%Y/%m/%d/%H')
    current = root / hour
    open_candidate = max(current.glob('*.mp4'), default=None) if active and current.exists() else None
    files = root.rglob('*.mp4')
    with db() as c:
        known = {r[0] for r in c.execute('SELECT path FROM segments WHERE camera_id=?', (cid,))}
        for file in files:
            if file == open_candidate:
                continue
            if str(file) in known:
                continue
            if not safe_archive_path(file) or not file.is_file():
                continue
            try:
                stat = file.stat()
            except FileNotFoundError:
                continue
            if stat.st_size < 1024:
                continue
            try:
                start = datetime.strptime(file.stem, '%Y%m%dT%H%M%S').replace(tzinfo=timezone.utc)
                probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', str(file)], capture_output=True, timeout=10, check=True)
                duration = float(probe.stdout.strip())
                if not math.isfinite(duration) or duration <= 0:
                    continue
                end = start + timedelta(seconds=duration)
            except (ValueError, OverflowError):
                continue
            except subprocess.CalledProcessError as error:
                if b'moov atom not found' in (error.stderr or b'') and stat.st_mtime < datetime.now(timezone.utc).timestamp() - 86400:
                    file.unlink(missing_ok=True)
                continue
            except (subprocess.SubprocessError, OSError):
                # Tool failures and storage timeouts do not prove footage is corrupt.
                continue
            c.execute('INSERT OR IGNORE INTO segments(camera_id,path,started_at,ended_at,size_bytes) VALUES(?,?,?,?,?)',
                      (cid, str(file), start.isoformat(), end.isoformat(), stat.st_size))
            c.commit()


def cleanup(rows, active, unavailable=None):
    with ARCHIVE_LOCK:
        _cleanup(rows, active, unavailable or set())


def _cleanup(rows, active, unavailable):
    now = datetime.now(timezone.utc)
    # Include old camera directories after deletion; otherwise their footage
    # could remain forever without a camera row to trigger indexing.
    roots = {(r['id'], directory(r)) for r in rows if r['recording_destination'] not in unavailable}
    for destination in ROOT.iterdir():
        if destination.name in unavailable or not destination.is_dir():
            continue
        for candidate in destination.iterdir():
            if candidate.is_dir() and not candidate.is_symlink() and candidate.name.isdecimal() and candidate.resolve().is_relative_to(ROOT):
                roots.add((int(candidate.name), candidate))
    with db() as c:
        for cid, root in roots:
            index_segments(cid, root, cid in active)
        records = c.execute('SELECT * FROM segments ORDER BY started_at, id').fetchall()
        by_id = {r['id']: r for r in rows}
        for s in records:
            path = Path(s['path'])
            if path.is_relative_to(ROOT) and path.relative_to(ROOT).parts[0] in unavailable:
                continue
            if not safe_archive_path(path):
                c.execute('DELETE FROM segments WHERE id=?', (s['id'],))
                continue
            row = by_id.get(s['camera_id'])
            # Never delete the current hour, even if FFmpeg is between segment closes.
            current_hour = now.strftime('%Y/%m/%d/%H')
            if s['camera_id'] in active and current_hour in path.as_posix():
                continue
            retention = row['retention_days'] if row else 7
            expired = bool(retention and datetime.fromisoformat(s['started_at']) < now - timedelta(days=retention))
            low_space = path.exists() and shutil.disk_usage(path.parent).free < MIN_FREE
            if expired or low_space or not path.exists():
                if path.exists() and path.is_file() and not path.is_symlink():
                    path.unlink()
                c.execute('DELETE FROM segments WHERE id=?', (s['id'],))
