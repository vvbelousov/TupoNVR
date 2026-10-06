"""Storage checks run in bounded subprocesses so slow mounts do not block ASGI."""
import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
import secrets
import stat
import time
from pathlib import Path

NAME_PATTERN = r'[A-Za-z0-9_-]{1,64}'
MARKER_FILE = '.nvr-storage-id'


def read_marker(path):
    with path.open() as handle:
        return handle.read(129).strip()


def is_mount(path):
    if os.path.ismount(path):
        return True
    # Linux bind mounts on the same filesystem are not detected by ismount().
    try:
        with open('/proc/self/mountinfo') as handle:
            return any(re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), line.split()[4]) == str(path) for line in handle)
    except OSError:
        return False


def marker_operation(root, name, action, allow_local=False):
    """No mkdir, symlinks, arbitrary paths or replacement of existing markers."""
    result = {'reason': 'unavailable'}
    directory_fd = None
    scratch = None
    try:
        if not re.fullmatch(NAME_PATTERN, name) or action not in ('create', 'use_existing'):
            return {'reason': 'unsafe_path'}
        path = Path(root).resolve() / name
        directory_fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        identity = os.fstat(directory_fd)
        if action == 'create':
            # Detect an existing file even if malformed; never replace it.
            try:
                os.stat(MARKER_FILE, dir_fd=directory_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                return {'reason': 'marker_exists'}
            if not is_mount(path) and not allow_local:
                return {'reason': 'mount_confirmation_required'}
            identifier = secrets.token_hex(32)
            scratch = '.nvr-id-' + secrets.token_hex(16)
            fd = os.open(scratch, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory_fd)
            with os.fdopen(fd, 'w') as handle:
                handle.write(identifier + '\n')
                handle.flush()
                os.fsync(handle.fileno())
            # Publish the complete file atomically, failing if another writer
            # created the marker. Both names are anchored to the opened directory.
            os.link(scratch, MARKER_FILE, src_dir_fd=directory_fd, dst_dir_fd=directory_fd, follow_symlinks=False)
            os.fsync(directory_fd)
        fd = os.open(MARKER_FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        with os.fdopen(fd, 'rb') as handle:
            metadata = os.fstat(handle.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 256:
                return {'reason': 'invalid_marker'}
            stored = handle.read(256).decode('ascii').strip()
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', stored):
            return {'reason': 'invalid_marker'}
        if action == 'create' and stored != identifier:
            return {'reason': 'identity_changed'}
        current = path.stat(follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino):
            return {'reason': 'identity_changed'}
        return {'identifier': stored, 'reason': 'ok'}
    except FileExistsError:
        result['reason'] = 'marker_exists'
    except PermissionError:
        result['reason'] = 'not_writable'
    except UnicodeError:
        result['reason'] = 'invalid_marker'
    except OSError:
        pass
    finally:
        if directory_fd is not None:
            if scratch:
                try:
                    os.unlink(scratch, dir_fd=directory_fd)
                except OSError:
                    pass
            os.close(directory_fd)
    return result


def probe(root, name, marker=None, camera_id=None, hour=None):
    result = {'available': False, 'writable': False, 'total_bytes': None, 'free_bytes': None, 'root_total_bytes': None, 'root_free_bytes': None, 'reason': 'unavailable', 'mounted': False}
    try:
        if not re.fullmatch(NAME_PATTERN, name):
            return result
        root = Path(root).resolve()
        path = root / name
        if not path.resolve().is_relative_to(root):
            result['reason'] = 'unsafe_path'
            return result
        root_usage = shutil.disk_usage(root)
        result.update(root_total_bytes=root_usage.total, root_free_bytes=root_usage.free)
        if marker:
            # Never create the marker or a missing protected destination.
            marker_path = path / MARKER_FILE
            if marker_path.is_symlink() or read_marker(marker_path) != marker:
                result['reason'] = 'identity_missing'
                return result
        else:
            path.mkdir(parents=True, exist_ok=True)
        identity = path.stat()
        result['mounted'] = is_mount(path)
        usage = shutil.disk_usage(path)
        result.update(available=True, total_bytes=usage.total, free_bytes=usage.free, reason='not_writable')
        fd, scratch = tempfile.mkstemp(prefix='.nvr-check-', dir=path)
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(b'nvr')
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            Path(scratch).unlink(missing_ok=True)
        if camera_id is not None:
            target = path / str(camera_id) / hour
            if not target.resolve().is_relative_to(root):
                result.update(available=False, reason='unsafe_path')
                return result
            target.mkdir(parents=True, exist_ok=True)
        current = path.stat()
        if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino) or (marker and read_marker(path / MARKER_FILE) != marker):
            result.update(available=False, reason='identity_changed')
            return result
        result.update(writable=True, reason='ok')
    except (OSError, UnicodeError):
        if marker and not result['available']:
            result['reason'] = 'identity_missing'
    return result


class StorageMonitor:
    def __init__(self, root, minimum_free, timeout=3):
        self.root = root
        self.root_usage = {'total_bytes': None, 'free_bytes': None}
        self.minimum_free = minimum_free
        self.timeout = timeout
        self.statuses = {}
        self.markers = {}
        self.processes = {}
        self.lock = asyncio.Lock()
        self.semaphore = asyncio.Semaphore(4)

    def status(self, name):
        state = self.statuses.get(name)
        if state is None or time.monotonic() - state['_tick'] > 15:
            return {'name': name, 'protected': bool(self.markers.get(name)), 'available': False, 'writable': False, 'ready': False, 'total_bytes': None, 'free_bytes': None, 'reason': 'check_pending'}
        return {k: v for k, v in state.items() if not k.startswith('_')}

    async def inspect(self, name, camera_id=None, hour=None, action=None, allow_local=False):
        async with self.semaphore:
            previous = self.processes.get(name)
            if previous is not None and previous.returncode is None:
                return {'available': False, 'writable': False, 'total_bytes': None, 'free_bytes': None, 'reason': 'check_timeout'}
            try:
                proc = await asyncio.create_subprocess_exec(
                    sys.executable, str(Path(__file__).resolve()), str(self.root), name,
                    self.markers.get(name) or '', str(camera_id or ''), hour or '', action or '', str(int(allow_local)),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            except OSError:
                return {'available': False, 'writable': False, 'total_bytes': None, 'free_bytes': None, 'reason': 'check_failed'}
            self.processes[name] = proc
            try:
                output, _ = await asyncio.wait_for(proc.communicate(), self.timeout)
                if proc.returncode != 0:
                    raise ValueError('Probe failed')
                return json.loads(output)
            except (asyncio.TimeoutError, ValueError, OSError):
                return {'available': False, 'writable': False, 'total_bytes': None, 'free_bytes': None, 'reason': 'check_timeout'}
            finally:
                if proc.returncode is None:
                    proc.kill()
                    try:
                        await asyncio.wait_for(proc.wait(), 1)
                    except asyncio.TimeoutError:
                        pass  # Keep this process registered; do not spawn replacements.

    async def refresh(self, names, markers):
        async with self.lock:
            self.markers = dict(markers)
            async def check(name):
                result = await self.inspect(name)
                ready = result['available'] and result['writable'] and result['free_bytes'] >= self.minimum_free
                if result['reason'] == 'ok' and not ready:
                    result['reason'] = 'low_space'
                if name == 'default':
                    self.root_usage = {'total_bytes': result.get('root_total_bytes'), 'free_bytes': result.get('root_free_bytes')}
                self.statuses[name] = {**result, 'name': name, 'protected': bool(markers.get(name)), 'ready': ready, '_tick': time.monotonic()}
            await asyncio.gather(*(check(name) for name in sorted(set(names))))
            self.statuses = {name: state for name, state in self.statuses.items() if name in names}

    async def prepare(self, row, hour):
        # Recheck the identity immediately before creating recorder directories.
        async with self.lock:
            result = await self.inspect(row['recording_destination'], row['id'], hour)
        return result['available'] and result['writable'] and result['free_bytes'] >= self.minimum_free

    async def available(self, name):
        # A graceful FFmpeg stop can outlast the status cache. Check the mount
        # again before indexing, including when storage is below its reserve.
        async with self.lock:
            return (await self.inspect(name))['available']

    async def close(self):
        for proc in self.processes.values():
            if proc.returncode is None:
                proc.kill()
        await asyncio.gather(*(self._reap(proc) for proc in self.processes.values()), return_exceptions=True)

    async def _reap(self, proc):
        try:
            await asyncio.wait_for(proc.wait(), 1)
        except asyncio.TimeoutError:
            pass


if __name__ == '__main__':
    result = marker_operation(sys.argv[1], sys.argv[2], sys.argv[6], sys.argv[7] == '1') if len(sys.argv) > 6 and sys.argv[6] else probe(
        sys.argv[1], sys.argv[2], sys.argv[3] or None, int(sys.argv[4]) if sys.argv[4] else None, sys.argv[5] or None)
    print(json.dumps(result))
