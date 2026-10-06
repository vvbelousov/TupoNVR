"""Weekly wall-clock recording schedules; no scheduler service required."""
import json
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, StrictInt, field_validator, model_validator
from timeconfig import get_timezone


class ScheduleWindow(BaseModel):
    days: list[StrictInt] = Field(min_length=1, max_length=7)
    start: str
    end: str

    @field_validator('days')
    @classmethod
    def valid_days(cls, value):
        if len(set(value)) != len(value) or any(day < 0 or day > 6 for day in value):
            raise ValueError('Days must be unique integers: Monday=0 through Sunday=6')
        return value

    @field_validator('start', 'end')
    @classmethod
    def valid_time(cls, value, info):
        if info.field_name == 'end' and value == '24:00':
            return value
        if not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', value):
            raise ValueError('Expected HH:MM; 24:00 is allowed only as an end')
        return value

    @model_validator(mode='after')
    def nonempty(self):
        if self.start == self.end:
            raise ValueError('An all-day window is 00:00–24:00')
        return self


class RecordingSchedule(BaseModel):
    # Retained for older clients; runtime schedules use the installation timezone.
    timezone: str = Field(default_factory=get_timezone, max_length=100)
    windows: list[ScheduleWindow] = Field(min_length=1, max_length=14)

    @field_validator('timezone')
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError('Unknown IANA timezone')
        return value


def schedule_active(schedule, now=None, zone=None):
    if not schedule:
        return True
    if isinstance(schedule, str):
        schedule = json.loads(schedule)
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        raise ValueError('A timezone-aware timestamp is required')
    local = instant.astimezone(ZoneInfo(zone or get_timezone()))
    minute = local.hour * 60 + local.minute
    day = local.weekday()
    for window in schedule['windows']:
        start = int(window['start'][:2]) * 60 + int(window['start'][3:])
        end = int(window['end'][:2]) * 60 + int(window['end'][3:])
        if start < end:
            if day in window['days'] and start <= minute < end:
                return True
        elif (day in window['days'] and minute >= start) or ((day - 1) % 7 in window['days'] and minute < end):
            return True
    return False


def recording_expected(row, now=None):
    row = dict(row)
    return bool(row['enabled'] and row['recording_enabled'] and schedule_active(row.get('recording_schedule'), now))
