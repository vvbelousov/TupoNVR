"""Bounded, disk-backed archive exports using the recorder's FFmpeg installation."""
import json
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
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
            return {key: job[key] for key in ('token', 'state', 'completed', 'total', 'error', 'gaps', 'mode')}

    def create(self, camera_id, start, end, mode, validate_path):
        from db import db
        import video
        with self.lock:
            self.reap()
            if self.stopping:
                raise HTTPException(503, 'Export service is shutting down')
            if sum(job['state'] == 'processing' for job in self.jobs.values()) >= 2 or len(self.jobs) >= 16:
                raise HTTPException(429, 'Too many exports; retry later')
            # Selection and pinning are atomic with every supported deletion path.
            with video.ARCHIVE_LOCK:
                with db() as c:
                    records = c.execute('SELECT * FROM segments WHERE camera_id=? AND started_at<? AND ended_at>? ORDER BY started_at,id LIMIT 2001',
                                        (camera_id, end.isoformat(), start.isoformat())).fetchall()
                if not records:
                    raise HTTPException(404, 'No recordings in this interval')
                if len(records) > 2000:
                    raise HTTPException(422, 'Too many segments; choose a shorter interval')
                pieces, gaps, cursor, whole = [], [], start, True
                for row in records:
                    a = max(start, cursor, datetime.fromisoformat(row['started_at']))
                    b = min(end, datetime.fromisoformat(row['ended_at']))
                    if b <= a:
                        continue
                    path = validate_path(row['id'])  # Missing footage fails explicitly, never silently skipped.
                    if video.is_active_path(path, camera_id):
                        raise HTTPException(409, 'Recording is still being written; choose an earlier end time')
                    if cursor < a:
                        gaps.append({'start': cursor.isoformat(), 'end': a.isoformat()})
                    pieces.append((path, (a - datetime.fromisoformat(row['started_at'])).total_seconds(), (b - a).total_seconds()))
                    whole = whole and a == datetime.fromisoformat(row['started_at']) and b == datetime.fromisoformat(row['ended_at'])
                    cursor = b
                if cursor < end:
                    gaps.append({'start': cursor.isoformat(), 'end': end.isoformat()})
                from db import PATH
                export_root = PATH.parent / 'exports'
                try:
                    export_root.mkdir(mode=0o700, parents=True, exist_ok=True)
                    directory = Path(tempfile.mkdtemp(prefix='job-', dir=export_root))
                except OSError:
                    raise HTTPException(503, 'Cannot allocate temporary export storage') from None
                for path, _, _ in pieces:
                    self.pins[path] = self.pins.get(path, 0) + 1
                token = secrets.token_urlsafe(24)
                job = {'token': token, 'state': 'processing', 'completed': 0, 'total': len(pieces), 'error': '',
                       'gaps': gaps, 'whole': whole, 'mode': 'copy' if whole else mode, 'directory': directory, 'created': time.monotonic(),
                       'filename': f'camera-{camera_id}-{start:%Y%m%dT%H%M%SZ}-{end:%Y%m%dT%H%M%SZ}.mp4'}
                self.jobs[token] = job
            worker = None
            try:
                worker = threading.Thread(target=self.worker, args=(job, pieces), daemon=True)
                self.workers.add(worker)
                worker.start()
            except Exception:
                with video.ARCHIVE_LOCK:
                    for path, _, _ in pieces:
                        self.pins[path] -= 1
                        if not self.pins[path]:
                            del self.pins[path]
                shutil.rmtree(directory, ignore_errors=True)
                del self.jobs[token]
                self.workers.discard(worker)
                raise HTTPException(503, 'Cannot start export worker') from None
            self.schedule_expiry()
            return self.status(token)

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
                returncode = process.wait(timeout=min(900, remaining))
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

    def worker(self, job, pieces):
        import video
        directory = job['directory']
        outcome = 'failed'
        try:
            deadline = time.monotonic() + 3600
            signature = None
            encoding = []
            if job['mode'] == 'exact':
                encoders = subprocess.check_output(['ffmpeg', '-hide_banner', '-encoders'], stderr=subprocess.DEVNULL, timeout=10)
                encoding = (['-c:v', 'libx264', '-preset', 'fast', '-crf', '18'] if b'libx264' in encoders
                            else ['-c:v', 'libopenh264', '-b:v', '8M'])
            for index, (path, offset, duration) in enumerate(pieces):
                args = ['-ss', str(offset), '-threads', '2', '-i', str(path), '-t', str(duration), '-map', '0:v:0', '-an']
                if job['mode'] == 'copy':
                    args += ['-c:v', 'copy']
                    if not job['whole']:
                        args += ['-avoid_negative_ts', 'make_zero']
                else:
                    # Match full segments' codec/parameters as well as trimmed boundaries.
                    # Camera resolution changes fail rather than making a misleading file.
                    args += [*encoding, '-threads', '2', '-pix_fmt', 'yuv420p']
                self.run([*args, str(directory / f'{index}.mp4')], deadline)
                current = self.probe(directory / f'{index}.mp4')
                if signature is not None and current != signature:
                    raise RuntimeError('Recording codec or resolution changed during the interval. Choose a shorter interval or exact mode.')
                signature = current
                with self.lock:
                    job['completed'] = index + 1
            (directory / 'list.txt').write_text(''.join(f"file '{index}.mp4'\n" for index in range(len(pieces))))
            self.run(['-f', 'concat', '-safe', '1', '-i', str(directory / 'list.txt'), '-c', 'copy', '-movflags', '+faststart', str(directory / 'export.mp4')], deadline)
            # Stream copying does not decode packets; validate the joined video so
            # corrupt payloads and concatenation failures cannot look successful.
            self.run(['-threads', '2', '-err_detect', 'explode', '-i', str(directory / 'export.mp4'),
                      '-map', '0:v:0', '-an', '-f', 'null', '-'], deadline)
            for path in directory.iterdir():
                if path.name != 'export.mp4':
                    path.unlink()
            outcome = 'ready'
        except Exception as error:
            shutil.rmtree(directory, ignore_errors=True)
            with self.lock:
                job['error'] = str(error) if isinstance(error, RuntimeError) else 'Export failed or timed out. Check storage space and FFmpeg availability.'
        finally:
            with video.ARCHIVE_LOCK:
                for path, _, _ in pieces:
                    count = self.pins[path] - 1
                    if count:
                        self.pins[path] = count
                    else:
                        del self.pins[path]
            with self.lock:
                job['state'] = outcome
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
