"""Installation civil time at boundaries; recording timestamps remain UTC."""
import json
import os
import tempfile
import threading
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from zoneinfo import TZPATH, ZoneInfo, ZoneInfoNotFoundError, available_timezones

_lock = threading.RLock()
_cached = None


def settings_path():
    return Path(os.getenv('SETTINGS_PATH', str(Path(os.getenv('DATABASE_PATH', '/data/nvr.sqlite3')).parent / 'settings.json')))


@lru_cache(maxsize=1)
def zone_links():
    links = {}
    for root in TZPATH:
        metadata = Path(root) / 'tzdata.zi'
        if metadata.is_file():
            for line in metadata.read_text().splitlines():
                parts = line.split()
                if len(parts) >= 3 and parts[0] in ('L', 'Link'):
                    links.setdefault(parts[2], parts[1])
    return links


def canonical_zone(value):
    seen = set()
    while value != 'UTC' and value in zone_links() and value not in seen:
        seen.add(value)
        value = zone_links()[value]
    return value


def valid_timezones():
    # Host-local symlinks/placeholders are not installation timezone identifiers.
    return {canonical_zone(value) for value in available_timezones() - {'localtime', 'posixrules', 'Factory'}}


def validate_zone(value):
    if value not in available_timezones() - {'localtime', 'posixrules', 'Factory'}:
        raise ValueError('Unknown IANA timezone')
    value = canonical_zone(value)
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError('Unknown IANA timezone')
    return value


def get_timezone():
    # Cache per file/bootstrap, so schedule checks do not read disk every five seconds.
    global _cached
    path = settings_path()
    initial = os.getenv('APP_TIMEZONE', 'UTC')
    with _lock:
        if _cached is None or _cached[:2] != (path, initial):
            data = json.loads(path.read_text()) if path.exists() else {}
            zone = validate_zone(data.get('timezone', initial))
            _cached = (path, initial, zone)
        return _cached[2]


def save_timezone(zone):
    global _cached
    zone = validate_zone(zone)
    path = settings_path()
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.loads(path.read_text()) if path.exists() else {}
        data['timezone'] = zone
        descriptor, temporary = tempfile.mkstemp(prefix='.settings-', dir=path.parent)
        try:
            with os.fdopen(descriptor, 'w') as stream:
                json.dump(data, stream)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            # The rename is already visible, even if confirming directory durability fails.
            _cached = (path, os.getenv('APP_TIMEZONE', 'UTC'), zone)
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return zone


def local_instants(local, zone):
    """Round-trip folds to reject imaginary times and expose repeated hours."""
    if local.tzinfo is not None:
        raise ValueError('Local date and time must not contain a timezone offset')
    tz = ZoneInfo(zone)
    result = set()
    for fold in (0, 1):
        utc = local.replace(tzinfo=tz, fold=fold).astimezone(timezone.utc)
        if utc.astimezone(tz).replace(tzinfo=None) == local:
            result.add(utc)
    return sorted(result)


def day_start(day, zone):
    local = datetime.combine(day, time())
    # Some zones change offset at midnight. A civil day can start later than 00:00.
    for minute in range(24 * 60):
        values = local_instants(local + timedelta(minutes=minute), zone)
        if values:
            if minute:
                # Historical IANA transitions can have second-resolution offsets.
                candidate = local + timedelta(minutes=minute - 1)
                for second in range(1, 60):
                    earlier = local_instants(candidate + timedelta(seconds=second), zone)
                    if earlier:
                        return earlier[0]
            return values[0]
    raise ValueError('This local date does not exist in the configured timezone')


def day_range(day, zone):
    start = day_start(day, zone)
    following = day + timedelta(days=1)
    # A whole skipped date (e.g. Apia 2011-12-30) is not a 24-hour UTC day.
    for _ in range(3):
        try:
            end = day_start(following, zone)
            break
        except ValueError:
            following += timedelta(days=1)
    else:
        raise ValueError('Cannot resolve local date boundary')
    ticks = []
    cursor = start
    while cursor < end:
        ticks.append({'time': cursor.isoformat(), 'local': cursor.astimezone(ZoneInfo(zone)).isoformat()})
        cursor += timedelta(hours=1)
    ticks.append({'time': end.isoformat(), 'local': end.astimezone(ZoneInfo(zone)).isoformat()})
    return {'timezone': zone, 'start': start.isoformat(), 'end': end.isoformat(), 'ticks': ticks}


def resolve_local(day: date, clock: time, zone):
    values = local_instants(datetime.combine(day, clock), zone)
    if not values:
        raise ValueError('This local time does not exist because of a timezone transition')
    tz = ZoneInfo(zone)
    return [{'time': value.isoformat(), 'local': value.astimezone(tz).isoformat()} for value in values]
