"""One-shot portable camera configuration. SQLite remains authoritative."""
import hashlib
import hmac
import json
import logging
import re
import secrets
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from pydantic import ValidationError
from camera_config import CameraInput, CameraUpdate, camera_data, editable_url
from timeconfig import get_timezone

MAX_BYTES = 1024 * 1024
BOOTSTRAP_PATH = Path('/config/bootstrap/cameras.yaml')
FIELDS = tuple(k for k in CameraInput.model_fields if k != 'clear_substream')
SECRET_FIELDS = {'username', 'password', 'rtsp_url', 'substream_url'}
SIGNING_KEY = secrets.token_bytes(32)
log = logging.getLogger('nvr')


class ConfigError(ValueError):
    def __init__(self, errors):
        self.errors = errors
        super().__init__('Invalid camera configuration')


class SafeLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        keys = [self.construct_object(key, deep=deep) for key, _ in node.value]
        if any(not isinstance(key, str) for key in keys) or len(set(keys)) != len(keys):
            raise ValueError('Duplicate or non-string mapping keys')
        return super().construct_mapping(node, deep=deep)


def parse(content):
    if len(content) > MAX_BYTES:
        raise ConfigError([{'field': 'document', 'message': 'YAML exceeds 1 MiB'}])
    try:
        content = content.decode('utf-8-sig')
        # Reject aliases and bound nesting before constructing Python objects.
        depth = count = 0
        for event in yaml.parse(content, Loader=SafeLoader):
            count += 1
            if isinstance(event, yaml.AliasEvent) or count > 30000:
                raise ValueError()
            if isinstance(event, (yaml.MappingStartEvent, yaml.SequenceStartEvent)):
                depth += 1
                if depth > 20:
                    raise ValueError()
            elif isinstance(event, (yaml.MappingEndEvent, yaml.SequenceEndEvent)):
                depth -= 1
        value = yaml.load(content, Loader=SafeLoader)
    except (yaml.YAMLError, ValueError, UnicodeError, TypeError, RecursionError):
        raise ConfigError([{'field': 'document', 'message': 'Invalid YAML: use unique string keys, no aliases or custom tags'}]) from None
    if not isinstance(value, dict) or set(value) != {'version', 'cameras'}:
        raise ConfigError([{'field': 'document', 'message': 'Expected version and cameras fields'}])
    if type(value['version']) is not int or value['version'] != 1:
        raise ConfigError([{'field': 'version', 'message': 'Unsupported schema version; expected 1'}])
    if not isinstance(value['cameras'], list) or len(value['cameras']) > 500:
        raise ConfigError([{'field': 'cameras', 'message': 'Expected at most 500 cameras'}])
    return value['cameras']


def export_yaml(rows, include_credentials=False):
    cameras = []
    for row in sorted(rows, key=lambda r: r['key']):
        camera = {'key': row['key']}
        for field in FIELDS:
            value = row[field]
            if field in ('username', 'password') and not include_credentials:
                continue
            if field in ('enabled', 'recording_enabled'):
                value = bool(value)
            elif field == 'recording_schedule' and value:
                value = json.loads(value)
            elif field in ('rtsp_url', 'substream_url') and not include_credentials:
                value = editable_url(value)
            camera[field] = value
        cameras.append(camera)
    return yaml.safe_dump({'version': 1, 'cameras': cameras}, sort_keys=False, allow_unicode=True)


def strict_fields(item):
    """YAML must not silently coerce booleans, strings, or misspelled fields."""
    errors = []
    for field, value in item.items():
        if field not in FIELDS and field != 'key':
            errors.append({'field': 'camera', 'message': 'Unknown camera field'})
        elif field in ('enabled', 'recording_enabled') and type(value) is not bool:
            errors.append({'field': field, 'message': 'Expected boolean'})
        elif field == 'retention_days' and value is not None and type(value) is not int:
            errors.append({'field': field, 'message': 'Expected integer or null'})
        elif field in ('name', 'rtsp_url', 'description') and not isinstance(value, str):
            errors.append({'field': field, 'message': 'Expected string'})
        if field in ('username', 'password', 'rtsp_url', 'substream_url'):
            if value is not None and (not isinstance(value, str) or len(value) > 4096 or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)):
                errors.append({'field': field, 'message': 'Expected credential/URL string without control characters (max 4096)'})
    schedule = item.get('recording_schedule')
    if isinstance(schedule, dict):
        if set(schedule) - {'timezone', 'windows'}:
            errors.append({'field': 'recording_schedule', 'message': 'Unknown schedule field'})
        windows = schedule.get('windows')
        if isinstance(windows, list) and any(isinstance(w, dict) and set(w) - {'days', 'start', 'end'} for w in windows):
            errors.append({'field': 'recording_schedule.windows', 'message': 'Unknown window field'})
    return errors


def plan(content, rows):
    result = {'create': [], 'update': [], 'unchanged': [], 'errors': [], 'conflicts': [], 'warnings': []}
    try:
        items = parse(content)
    except ConfigError as error:
        result['errors'] = error.errors
        return result, []
    existing = {row['key']: dict(row) for row in rows}
    seen, writes = set(), []
    for index, item in enumerate(items):
        prefix = f'cameras.{index}'
        if not isinstance(item, dict):
            result['errors'].append({'field': prefix, 'message': 'Expected camera mapping'})
            continue
        key = item.get('key')
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', key):
            result['errors'].append({'field': prefix + '.key', 'message': 'Key must be 1–64 letters, digits, underscores or hyphens'})
            continue
        if key in seen:
            result['conflicts'].append({'key': key, 'message': 'Duplicate camera key'})
            continue
        seen.add(key)
        errors = strict_fields(item)
        if errors:
            result['errors'].extend({**e, 'field': prefix + '.' + e['field']} for e in errors)
            continue
        old = existing.get(key)
        values = {k: v for k, v in item.items() if k != 'key'}
        # Empty credentials never request removal, even for username.
        for field in ('username', 'password'):
            if field in values and not values[field]:
                values.pop(field)
        try:
            model = (CameraUpdate if old else CameraInput).model_validate(values)
            data = camera_data(model, old)
            if not data['rtsp_url']:
                raise ValueError('RTSP URL required')
            if not old:
                urls = [urlsplit(data[f]) for f in ('rtsp_url', 'substream_url') if data[f]]
                if (data['username'] and not data['password']) or (not data['username'] and any(u.username and not u.password for u in urls)):
                    result['errors'].append({'field': prefix + '.password', 'message': 'Password required for a new camera with a username'})
                    continue
                if not (data['username'] or any(u.username for u in urls)):
                    result['warnings'].append({'key': key, 'message': 'Credentials omitted; camera must allow anonymous access or be edited after import'})
        except ValidationError as error:
            # Never include Pydantic input values or exception context (secrets).
            result['errors'].extend({'field': prefix + '.' + '.'.join(str(part) for part in e['loc']),
                                     'message': e['msg'].removeprefix('Value error, ')} for e in error.errors())
            continue
        except ValueError:
            result['errors'].append({'field': prefix + '.rtsp_url', 'message': 'RTSP URL required'})
            continue
        data['key'] = key
        changes = {}
        for field in FIELDS:
            before = old[field] if old else None
            after = data[field]
            equal = before == after
            if field == 'recording_schedule':
                equal = (json.loads(before) if before else None) == (json.loads(after) if after else None)
            if old and equal:
                continue
            if field in SECRET_FIELDS:
                changes[field] = {'changed': True}
            else:
                if field == 'recording_schedule':
                    before = json.loads(before) if before else None
                    after = json.loads(after) if after else None
                changes[field] = {'before': before, 'after': after}
        action = 'create' if not old else 'update' if changes else 'unchanged'
        result[action].append({'key': key, 'name': data['name'], 'changes': changes})
        if action != 'unchanged':
            writes.append((old['id'] if old else None, data))
    if not result['errors'] and not result['conflicts']:
        # HMAC prevents guessing credentials from an exposed database digest.
        payload = json.dumps({'rows': [dict(r) for r in rows], 'content': content.decode('utf-8-sig'),
                              'timezone': get_timezone()}, sort_keys=True).encode()
        result['fingerprint'] = hmac.new(SIGNING_KEY, payload, hashlib.sha256).hexdigest()
    return result, writes


def write(c, writes):
    for cid, data in writes:
        if cid is None:
            fields = ('key', *FIELDS)
            c.execute(f"INSERT INTO cameras({','.join(fields)}) VALUES({','.join('?' for _ in fields)})", [data[f] for f in fields])
        else:
            c.execute(f"UPDATE cameras SET {','.join(f'{f}=?' for f in FIELDS)} WHERE id=?", [data[f] for f in FIELDS] + [cid])


def bootstrap(path=None):
    from db import db
    path = path or BOOTSTRAP_PATH
    try:
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute('SELECT 1 FROM cameras LIMIT 1').fetchone():
                log.info('camera_bootstrap_skipped_existing')
                return
            if not path.exists():
                return
            with path.open('rb') as source:
                content = source.read(MAX_BYTES + 1)
            preview, writes = plan(content, [])
            if preview['errors'] or preview['conflicts']:
                for error in preview['errors']:
                    log.warning('camera_bootstrap_invalid field=%s message=%s', error['field'], error['message'])
                log.warning('camera_bootstrap_failed errors=%d conflicts=%d', len(preview['errors']), len(preview['conflicts']))
                return
            write(c, writes)
        log.info('camera_bootstrap_imported count=%d', len(writes))
    except Exception:
        # Optional configuration must not prevent recovery through the UI.
        log.warning('camera_bootstrap_failed_io_or_database; continuing startup')
