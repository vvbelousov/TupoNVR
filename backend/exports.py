"""Bounded, disk-backed archive exports using the recorder's FFmpeg installation."""
import json
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException


class ExportJobs:
    def __init__(self):
        self.lock = threading.RLock()
        self.jobs = {}
        self.pins = {}
        self.processes = set()
        self.workers = set()
        self.stopping = False
        self.expiry_timer = None

    def status(self, token):
        with self.lock:
            self.reap()
            job = self.jobs.get(token)
            if not job:
                raise HTTPException(404, 'Export expired or not found')
            return {key: job[key] for key in ('token', 'state', 'completed', 'total', 'error', 'gaps', 'mode', 'phase', 'cameras', 'format', 'partial')}

    def create(self, camera_id, start, end, mode, validate_path, camera_ids=None):
        from db import db, PATH
        import video
        ids = camera_ids if camera_ids is not None else [camera_id]
        if not ids or len(ids) > 64 or len(set(ids)) != len(ids) or any(i <= 0 for i in ids):
            raise HTTPException(422, 'Choose 1 to 64 distinct cameras')
        with self.lock:
            self.reap()
            if self.stopping:
                raise HTTPException(503, 'Export service is shutting down')
            if sum(job['state'] == 'processing' for job in self.jobs.values()) >= 2 or len(self.jobs) >= 16:
                raise HTTPException(429, 'Too many exports; retry later')
            # Validate all cameras and pin all inputs atomically with retention.
            with video.ARCHIVE_LOCK:
                plans, cameras, pieces = [], [], []
                with db() as c:
                    valid = {row['id'] for row in c.execute('SELECT id FROM cameras UNION SELECT DISTINCT camera_id AS id FROM segments')}
                    if any(i not in valid for i in ids):
                        raise HTTPException(404 if camera_ids is None else 422,
                                            'No recordings in this interval' if camera_ids is None else 'Unknown export camera')
                    for cid in ids:
                        result = {'camera_id': cid, 'state': 'preparing', 'error': '', 'gaps': [],
                                  'completed': 0, 'total': 0, 'mode': mode, 'filename': None}
                        cameras.append(result)
                        try:
                            records = c.execute('SELECT * FROM segments WHERE camera_id=? AND started_at<? AND ended_at>? ORDER BY started_at,id LIMIT 2001',
                                                (cid, end.isoformat(), start.isoformat())).fetchall()
                            if not records:
                                raise HTTPException(404, 'No recordings in this interval')
                            if len(records) > 2000:
                                raise HTTPException(422, 'Too many segments; choose a shorter interval')
                            selected, gaps, cursor, whole = [], [], start, True
                            for row in records:
                                a = max(start, cursor, datetime.fromisoformat(row['started_at']))
                                b = min(end, datetime.fromisoformat(row['ended_at']))
                                if b <= a:
                                    continue
                                path = validate_path(row['id'])
                                if video.is_active_path(path, cid):
                                    raise HTTPException(409, 'Recording is still being written; choose an earlier end time')
                                if cursor < a:
                                    gaps.append({'start': cursor.isoformat(), 'end': a.isoformat()})
                                selected.append((path, (a - datetime.fromisoformat(row['started_at'])).total_seconds(), (b - a).total_seconds()))
                                whole = whole and a == datetime.fromisoformat(row['started_at']) and b == datetime.fromisoformat(row['ended_at'])
                                cursor = b
                            if cursor < end:
                                gaps.append({'start': cursor.isoformat(), 'end': end.isoformat()})
                            result.update(gaps=gaps, total=len(selected), mode='copy' if whole else mode)
                            plans.append((result, selected, whole))
                            pieces.extend(selected)
                        except HTTPException as error:
                            if len(ids) == 1:
                                raise
                            reason = str(error.detail or 'Recording unavailable')
                            if reason == 'Not Found':
                                reason = 'A recording is missing or unavailable; refresh the archive or choose another interval.'
                            result.update(state='failed', error=reason)
                if not plans:
                    raise HTTPException(404, 'No selected cameras have usable recordings')
                if len(pieces) > 2000:
                    raise HTTPException(422, 'Too many segments; choose a shorter interval')
                export_root = PATH.parent / 'exports'
                try:
                    export_root.mkdir(mode=0o700, parents=True, exist_ok=True)
                    # Reserve headroom for parts, joined video and ZIP across admitted jobs.
                    estimate = max(sum(path.stat().st_size for path, _, _ in pieces) * 3,
                                   int(sum(duration for _, _, duration in pieces) * 2_000_000 * 3))
                    reserved = sum(j.get('reservation', 0) for j in self.jobs.values())
                    if estimate > 8 * 1024**3 or shutil.disk_usage(export_root).free < estimate + reserved + 256 * 1024**2:
                        raise HTTPException(507, 'Insufficient temporary export space; choose a shorter interval')
                    directory = Path(tempfile.mkdtemp(prefix='job-', dir=export_root))
                except OSError:
                    raise HTTPException(503, 'Cannot allocate temporary export storage') from None
                for path, _, _ in pieces:
                    self.pins[path] = self.pins.get(path, 0) + 1
                token = secrets.token_urlsafe(24)
                multi = len(ids) > 1
                job = {'token': token, 'state': 'processing', 'phase': 'preparing', 'completed': 0,
                       'total': len(pieces), 'error': '', 'gaps': plans[0][0]['gaps'] if not multi else [],
                       'mode': plans[0][0]['mode'] if not multi else mode, 'directory': directory,
                       'created': time.monotonic(), 'reservation': estimate, 'cameras': cameras,
                       'format': 'zip' if multi else 'mp4', 'partial': False,
                       'filename': f'archive-{start:%Y%m%dT%H%M%SZ}-{end:%Y%m%dT%H%M%SZ}.zip' if multi else
                                   f'camera-{ids[0]}-{start:%Y%m%dT%H%M%SZ}-{end:%Y%m%dT%H%M%SZ}.mp4'}
                self.jobs[token] = job
            worker = None
            try:
                worker = threading.Thread(target=self.worker, args=(job, plans, pieces, start, end, mode), daemon=True)
                self.workers.add(worker)
                worker.start()
            except Exception:
                self.unpin(pieces)
                shutil.rmtree(directory, ignore_errors=True)
                del self.jobs[token]
                self.workers.discard(worker)
                raise HTTPException(503, 'Cannot start export worker') from None
            self.schedule_expiry()
            return self.status(token)

    def unpin(self, pieces):
        import video
        with video.ARCHIVE_LOCK:
            for path, _, _ in pieces:
                count = self.pins[path] - 1
                if count:
                    self.pins[path] = count
                else:
                    del self.pins[path]

    def check_space(self, directory):
        size = 0
        for path in directory.rglob('*'):
            try:
                if path.is_file():
                    size += path.stat().st_size
            except FileNotFoundError:  # Another camera/job may finish while space is checked.
                continue
        if size > 8 * 1024**3 or shutil.disk_usage(directory.parent).free < 256 * 1024**2:
            raise RuntimeError('Temporary export space limit reached; choose a shorter interval.')

    def run(self, args, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError()
        # Diagnostics go to disk, rather than accumulating FFmpeg output in memory.
        with tempfile.TemporaryFile() as errors:
            with self.lock:
                if self.stopping:
                    raise RuntimeError('Export cancelled during shutdown')
                process = subprocess.Popen(['ffmpeg', '-hide_banner', '-nostdin', '-loglevel', 'error', '-xerror', '-y', *args],
                                           stdout=subprocess.DEVNULL, stderr=errors)
                self.processes.add(process)
            try:
                process_deadline = time.monotonic() + min(900, remaining)
                while True:
                    try:
                        returncode = process.wait(timeout=1)
                        break
                    except subprocess.TimeoutExpired:
                        if time.monotonic() >= process_deadline:
                            raise TimeoutError()
                        with self.lock:
                            for job in self.jobs.values():
                                if job['state'] == 'processing':
                                    self.check_space(job['directory'])
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                with self.lock:
                    self.processes.discard(process)
        if returncode:
            raise RuntimeError('A recording is damaged, unavailable, or incompatible with this export mode. Try exact mode or a shorter interval.')

    def probe(self, path):
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                                     '-show_entries', 'stream=codec_name,profile,level,width,height,pix_fmt,time_base,extradata:format=duration',
                                     '-show_data', '-of', 'json', str(path)],
                                    stdout=output, stderr=subprocess.DEVNULL, timeout=30)
            output.seek(0)
            metadata = json.loads(output.read(1024 * 1024))
        if result.returncode or not metadata.get('streams') or float(metadata.get('format', {}).get('duration', 0)) <= 0:
            raise RuntimeError('A recording has no usable video in the requested interval.')
        return metadata['streams'][0]

    def startup(self):
        """Remove abandoned exports after a process restart (one backend worker)."""
        from db import PATH
        self.stopping = False
        root = PATH.parent / 'exports'
        if root.exists():
            for directory in root.glob('job-*'):
                if directory.is_dir() and not directory.is_symlink():
                    shutil.rmtree(directory)

    def render(self, job, result, pieces, whole, directory, deadline):
        signature = None
        encoding = []
        if result['mode'] == 'exact':
            encoders = subprocess.check_output(['ffmpeg', '-hide_banner', '-encoders'], stderr=subprocess.DEVNULL, timeout=10)
            encoding = (['-c:v', 'libx264', '-preset', 'fast', '-crf', '18'] if b'libx264' in encoders
                        else ['-c:v', 'libopenh264', '-b:v', '8M'])
        for index, (path, offset, duration) in enumerate(pieces):
            args = ['-ss', str(offset), '-threads', '2', '-i', str(path), '-t', str(duration), '-map', '0:v:0', '-an']
            if result['mode'] == 'copy':
                args += ['-c:v', 'copy']
                if not whole:
                    args += ['-avoid_negative_ts', 'make_zero']
            else:
                # Match full segments' codec/parameters as well as trimmed boundaries.
                # Camera resolution changes fail rather than making a misleading file.
                args += [*encoding, '-threads', '2', '-pix_fmt', 'yuv420p']
            self.run([*args, str(directory / f'{index}.mp4')], deadline)
            self.check_space(job['directory'])
            current = self.probe(directory / f'{index}.mp4')
            if signature is not None and current != signature:
                raise RuntimeError('Recording codec or resolution changed during the interval. Choose a shorter interval or exact mode.')
            signature = current
            with self.lock:
                result['completed'] = index + 1
                job['completed'] += 1
        (directory / 'list.txt').write_text(''.join(f"file '{index}.mp4'\n" for index in range(len(pieces))))
        self.run(['-f', 'concat', '-safe', '1', '-i', str(directory / 'list.txt'), '-c', 'copy', '-movflags', '+faststart', str(directory / 'export.mp4')], deadline)
        # Stream copying does not decode packets; validate the joined video so
        # corrupt payloads and concatenation failures cannot look successful.
        self.run(['-threads', '2', '-err_detect', 'explode', '-i', str(directory / 'export.mp4'),
                  '-map', '0:v:0', '-an', '-f', 'null', '-'], deadline)
        self.check_space(job['directory'])
        for path in directory.iterdir():
            if path.name != 'export.mp4':
                path.unlink()

    def worker(self, job, plans, pieces, start, end, mode):
        directory = job['directory']
        outcome = 'failed'
        try:
            deadline = time.monotonic() + 3600
            with self.lock:
                job['phase'] = 'processing'
            for result, selected, whole in plans:
                work = directory / str(result['camera_id'])
                work.mkdir()
                with self.lock:
                    result['state'] = 'processing'
                try:
                    self.render(job, result, selected, whole, work, deadline)
                    filename = f"camera-{result['camera_id']}_{start:%Y-%m-%d_%H%M%S}_{end:%Y-%m-%d_%H%M%S}Z.mp4"
                    (work / 'export.mp4').rename(directory / (filename if job['format'] == 'zip' else 'export.mp4'))
                    with self.lock:
                        result.update(state='ready', filename=filename)
                except Exception as error:
                    with self.lock:
                        result.update(state='failed', error=str(error) if isinstance(error, RuntimeError) else
                                      'Export failed or timed out. Check storage space and FFmpeg availability.')
                    if self.stopping or time.monotonic() >= deadline:
                        raise RuntimeError('Export cancelled or timed out') from error
                finally:
                    shutil.rmtree(work, ignore_errors=True)
            if not any(c['state'] == 'ready' for c in job['cameras']):
                raise RuntimeError(next(c['error'] for c in job['cameras'] if c['error']))
            if job['format'] == 'zip':
                with self.lock:
                    job['phase'] = 'packaging'
                manifest = {'start': start.isoformat(), 'end': end.isoformat(), 'timezone': 'UTC',
                            'mode': mode, 'cameras': job['cameras']}
                with zipfile.ZipFile(directory / 'export.zip', 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
                    archive.writestr('manifest.json', json.dumps(manifest, indent=2))
                    for result in job['cameras']:
                        if result['state'] == 'ready':
                            self.check_space(directory)
                            archive.write(directory / result['filename'], arcname=result['filename'])
                            (directory / result['filename']).unlink()
                self.check_space(directory)
            with self.lock:
                job['partial'] = any(c['state'] == 'failed' for c in job['cameras'])
            outcome = 'ready'
        except Exception as error:
            shutil.rmtree(directory, ignore_errors=True)
            with self.lock:
                job['error'] = str(error) if isinstance(error, RuntimeError) else 'Export failed or timed out. Check storage space and FFmpeg availability.'
        finally:
            self.unpin(pieces)
            with self.lock:
                job['state'] = outcome
                job['phase'] = outcome
                job['created'] = time.monotonic()
                self.workers.discard(threading.current_thread())

    def close(self):
        with self.lock:
            self.stopping = True
            if self.expiry_timer:
                self.expiry_timer.cancel()
                self.expiry_timer = None
            workers = list(self.workers)
            for process in self.processes:
                if process.poll() is None:
                    process.kill()
        for worker in workers:
            worker.join(timeout=45)  # Includes an in-flight bounded ffprobe.
        with self.lock:
            for token in list(self.jobs):
                self.finish_download(token)

    def schedule_expiry(self):
        # One timer per manager, even when users consume many short exports.
        with self.lock:
            if self.expiry_timer is None and self.jobs and not self.stopping:
                self.expiry_timer = threading.Timer(60, self.expire)
                self.expiry_timer.daemon = True
                self.expiry_timer.start()

    def expire(self):
        with self.lock:
            self.expiry_timer = None
            self.reap()
            self.schedule_expiry()

    def reap(self):
        with self.lock:
            for token, job in list(self.jobs.items()):
                if job['state'] in ('ready', 'failed') and time.monotonic() - job['created'] >= 3600:
                    shutil.rmtree(job['directory'], ignore_errors=True)
                    del self.jobs[token]

    def download(self, token):
        with self.lock:
            self.status(token)
            job = self.jobs[token]
            if job['state'] != 'ready':
                raise HTTPException(409, 'Export is not ready or is already downloading')
            job['state'] = 'downloading'
            return job

    def finish_download(self, token):
        with self.lock:
            job = self.jobs.pop(token, None)
            if job:
                shutil.rmtree(job['directory'], ignore_errors=True)


jobs = ExportJobs()
