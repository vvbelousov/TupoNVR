# Installation time and synchronized archive investigation

## Timestamp audit and domain model

- SQLite `segments.started_at` and `ended_at` are TEXT containing aware UTC ISO 8601 timestamps (`+00:00`). The recorder/indexer writes normalized UTC, and archive queries normalize offset-bearing inputs into the same representation before comparison. No SQLite local-time interpretation is involved.
- FFmpeg runs with `TZ=UTC`, names recordings with UTC calendar components and a second-resolution UTC timestamp, and resets media time inside each segment. Indexing takes the UTC start from the existing filename and adds the probed media duration for the end. Neither filenames nor recordings are changed by installation timezone settings.
- Progress, diagnostic checks, JSON logs, retention comparisons, webhook `occurred_at` and delivery timestamps remain UTC. These are machine-oriented absolute timestamps. Displayed Overview progress/latest-file times and Storage webhook delivery times use the installation timezone.
- Previously, Archive used UTC calendar dates and a fixed 24-hour timeline; its list filtered only segment start dates. Time lookup already accepted explicit absolute timestamps. Previous/next lookup skipped overlapping footage and reported gaps. The browser formatted UTC but supplied its own timezone for newly created camera schedules.
- Existing recording metadata provides the absolute start/end and each file's relative seek offset, sufficient for practical multi-camera synchronization. The inherited filename start precision is one second; this is not frame-accurate broadcast timecode.

## Configuration ownership

Timezone aliases are normalized using the server timezone database’s link metadata (for example, US/Eastern → America/New_York). The installation has one explicit IANA timezone, shared by every user/browser and recording schedule. The default is `APP_TIMEZONE=UTC`. This environment value initializes an installation that has no saved preference; it does not override a timezone saved through the UI.

**Overview → Appliance time → Change timezone** provides a searchable native datalist of zones from the server's timezone database, the configured zone, and current local server time. Saving applies immediately to schedule expectations. Other open clients pick up the setting through existing configuration polling, normally within ten seconds.

The preference lives in `settings.json` beside `DATABASE_PATH`, normally `/data/settings.json` on the existing data volume. Writes use a private temporary file, file/directory fsync, an atomic rename, and an in-process lock. No table, column, index, migration, new dependency, or infrastructure is introduced. `SETTINGS_PATH` can override the file location for direct deployments. Preserve this file with the data directory. Manual edits require an application restart. Invalid configuration fails startup rather than silently changing recording times.

**Upgrade note:** legacy `recording_schedule.timezone` values remain stored and accepted for API compatibility, but are no longer independent runtime overrides. Set the installation timezone to the intended zone before relying on existing schedules. Every schedule's wall-clock windows now use that installation timezone. Changing it can start or stop scheduled recording; it never rewrites footage timestamps. The camera editor shows the effective installation zone.

Schedules still use inclusive starts, exclusive ends, start-day weekdays, overnight windows including Sunday→Monday, and `00:00`→`24:00` for a full day. Python `zoneinfo` resolves offsets: an autumn repeated hour is eligible twice; a spring missing hour never occurs. No fixed-offset schedule arithmetic is added.

## Local archive boundaries

The browser formats absolute instants with `Intl` and the configured zone, independently of its own timezone. The server resolves local civil input with `zoneinfo`. Local-day bounds can span 23, 24, 25 or fractional hours; timeline positions use the actual UTC duration. Records overlapping the selected local day are included even when their start belongs to a different date.

For a repeated local time, the resolver returns both absolute instants and their explicit offsets. The user chooses one before playback starts. A nonexistent local time or skipped civil date produces an actionable error; it is never silently shifted forward. Existing `date=` archive API filtering remains UTC for compatibility; the updated UI uses explicit `start`/`end` bounds.

## Archive workflow and playback architecture

Select one camera, several via the expandable checklist, or all; **Select all** and **Clear** affect only temporary investigation state. Choose a local date/time or click/drag the common timeline, then play. Each camera has an aligned availability/gap track. Pointer dragging previews locally and performs one lookup on release; keyboard arrows/Home/End preview, and Enter/Space seeks.

Historical playback has its own state, separate from live Multiview and its saved layout. An adaptive grid balances selected cameras and stacks on narrow screens. Focus enlarges a camera within that grid, keeping the same media elements and shared session.

A shared logical clock anchors one absolute UTC instant to `performance.now()`. All offsets are `(master UTC time − segment UTC start)`. Initial playback waits for available media metadata, with a five-second readiness deadline so a slow camera cannot hold the session indefinitely. Thereafter buffering, lookup failure, missing files, unsupported codecs or gaps affect the individual camera. The master remains independent of any one video.

A 250 ms controller tick applies common play/pause/rate and corrects drift only beyond **750 ms**, or on an explicit seek/resume/rate change. It waits for metadata and avoids interfering with a seek already in progress. Pending native play promises are not repeatedly issued. The displayed clock updates on whole-second changes. Rates 0.5×, 1×, 2× and 4× use native playback; no transcoding or composition is performed.

At a camera's segment end, batch lookup resolves the file covering the current master instant, including any overlap offset. For a gap, the response caches the next segment's absolute start; the camera rejoins when the clock reaches it, without polling during that gap. Different cameras transition independently. Metadata lookups time out after ten seconds; obsolete requests are cancelled on session changes. Lookup failures retry after five seconds, with errors local to the affected camera. Static absence of future footage does not trigger repeated requests; use Refresh or seek again to discover newly indexed footage.

Single-camera previous/next, download, pagination and the automatic-next preference remain available. With automatic next disabled, single-camera playback pauses at the segment boundary. Shared playback stops when all selected cameras have exhausted their indexed footage. Focus and language changes preserve the clock.

## API additions

All these endpoints use existing authentication:

- `GET /api/config`: adds `timezone` and UTC `now`; existing WebRTC configuration remains.
- `GET /api/time`: configured `timezone`, UTC `now`, valid `timezones`.
- `PUT /api/time`: `{ "timezone": "Europe/Moscow" }`, persists configuration and reconciles schedules.
- `GET /api/time/day?date=2026-10-06`: installation-zone `start`, `end` in UTC and offset-bearing local timeline ticks.
- `POST /api/time/resolve`: `{ "date": "2026-10-06", "time": "14:32:17" }`; returns `instants` with UTC `time` and offset-bearing `local` representation.
- `GET /api/recordings`: adds `start`, `end`, and comma-separated `camera_ids`; overlapping records, existing limit/offset pagination.
- `POST /api/recordings/timelines`: `{ "camera_ids": [1,2], "start": "...Z", "end": "...Z" }`; merged intervals/gaps grouped by camera. One range query, maximum 31 days/20,000 segments; shorten the range if exceeded.
- `POST /api/recordings/resolve`: `{ "camera_ids": [1,2], "time": "...Z" }`; current segment, seek seconds and next segment per camera. One HTTP request and one SQL statement with indexed camera/time lookups. Missing cameras/footage produce empty choices rather than failing the whole request.

Existing single-camera timeline/at/adjacent/file APIs remain compatible. Metadata responses omit paths and credentials. Naive absolute timestamps are rejected. Serving existing MP4s preserves byte-range support.

## Limits and verification

The browser and host must have current timezone databases. Camera/recorder clocks and second-resolution indexing limit real-world accuracy; the 750 ms correction threshold concerns browser drift, not camera capture-time guarantees. Native codec/seekability support, bandwidth and decoder capacity limit large selections. Media is delivered independently, so many cameras consume correspondingly more network/browser resources. Logs and webhook payloads deliberately remain UTC.

Tests cover UTC/Moscow/DST and fractional-day boundaries, repeated/missing times, overnight schedules, unchanged SQLite schema/recording timestamps, persisted atomic configuration, auth, local-date overlap, batch offsets/gaps and API validation. Clock tests cover independent offsets, pause/resume/rate, drift tolerance, segment boundaries and rejected play promises. Chromium uses real API/SQLite responses and native videos with a deliberately different browser timezone, exercises gap rejoining, independent transitions, shared seeks, focus, selection actions, DST choices and responsive layouts in both languages.
