"""Guarded deletion shared by retention and manual cleanup.

Only indexed, conventionally named segments are eligible. Directory descriptors
anchor unlink to the verified destination; no path component follows symlinks.
"""
import os
import re
import stat
from pathlib import Path


class MetadataUpdateError(RuntimeError):
    def __init__(self, reclaimed_bytes):
        super().__init__('File deletion completed but index update failed')
        self.reclaimed_bytes = reclaimed_bytes


def remove_segment(record, active=()):
    import video
    from db import db
    from storage import MARKER_FILE
    path = Path(record['path'])
    from exports import jobs
    if path in jobs.pins:
        return 'active', 0
    try:
        parts = path.relative_to(video.ROOT).parts
    except ValueError:
        return 'failed', 0
    if (len(parts) != 7 or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', parts[0])
            or parts[1] != str(record['camera_id'])
            or not re.fullmatch(r'\d{4}/\d{2}/\d{2}/\d{2}/\d{8}T\d{6}\.mp4', '/'.join(parts[2:]))):
        return 'failed', 0
    if video.is_active_path(path, record['camera_id'], active):
        return 'active', 0
    descriptors = []
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        root = os.open(video.ROOT, flags)
        descriptors.append(root)
        destination = os.open(parts[0], flags, dir_fd=root)
        descriptors.append(destination)
        with db() as c:
            row = c.execute('SELECT expected_marker FROM destinations WHERE name=?', (parts[0],)).fetchone()
        marker = row['expected_marker'] if row else None
        if marker:
            fd = os.open(MARKER_FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=destination)
            with os.fdopen(fd, 'rb') as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > 256 or handle.read(256).decode('ascii').strip() != marker:
                    return 'failed', 0

        def destination_unchanged():
            identity = os.stat(parts[0], dir_fd=root, follow_symlinks=False)
            opened = os.fstat(destination)
            return (identity.st_dev, identity.st_ino) == (opened.st_dev, opened.st_ino)

        parent = destination
        for component in parts[1:-1]:
            try:
                parent = os.open(component, flags, dir_fd=parent)
            except FileNotFoundError:
                return ('missing', 0) if destination_unchanged() else ('failed', 0)
            descriptors.append(parent)
        # Verify the pathname still names the opened destination, including mount identity.
        if not destination_unchanged():
            return 'failed', 0
        if video.is_active_path(path, record['camera_id'], active):
            return 'active', 0
        try:
            info = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return 'missing', 0
        if not stat.S_ISREG(info.st_mode):
            return 'failed', 0
        os.unlink(parts[-1], dir_fd=parent)
        return 'deleted', info.st_size
    except (OSError, UnicodeError):
        return 'failed', 0
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def delete_indexed(record, active=()):
    """Caller holds ARCHIVE_LOCK. Commit each successful unlink independently.

    A crash after unlink leaves a missing row that the next cleanup reconciles.
    Failed unlinks retain metadata. Missing files reclaim zero bytes.
    """
    from db import db
    result, size = remove_segment(record, active)
    if result in ('deleted', 'missing'):
        try:
            with db() as c:
                c.execute('DELETE FROM segments WHERE id=? AND path=?', (record['id'], record['path']))
        except Exception as error:
            raise MetadataUpdateError(size) from error
    return result, size
