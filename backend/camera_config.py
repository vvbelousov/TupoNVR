"""Shared camera validation and write normalization for CRUD and YAML imports."""
import json
from urllib.parse import urlsplit, urlunsplit, unquote
from pydantic import BaseModel, Field, field_validator
from policies import RecordingSchedule

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
    def valid_url(cls, value, info):
        if value is None:
            return value
        if value == '' and (info.field_name == 'substream_url' or cls is CameraUpdate):
            # Legacy updates use an empty main URL to preserve the saved source.
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


def camera_data(camera, old=None, patch=True):
    changes = camera.model_dump(exclude_unset=old is not None)
    clear = changes.pop('clear_substream', False)
    if 'recording_schedule' in changes:
        changes['recording_schedule'] = json.dumps(changes['recording_schedule']) if changes['recording_schedule'] else None
    if old is None:
        changes['substream_url'] = changes['substream_url'] or None
        url = urlsplit(changes['rtsp_url'])
        if changes['password'] and not changes['username']:
            changes['username'] = unquote(url.username or '') or None
        if changes['username'] and not changes['password']:
            changes['password'] = unquote(url.password or '') or None
        return changes
    for key in ('rtsp_url', 'substream_url'):
        if key in changes:
            if key == 'substream_url' and changes[key] is None and patch:
                continue
            if not changes[key]:
                changes.pop(key)
            else:
                changes[key] = preserve_url_secrets(changes[key], old[key])
    if clear:
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
    return data
