# Installation time and synchronized archive investigation

[Documentation](README.md) · [Русский](time-and-archive_RU.md)

## Timestamp audit and domain model

- SQLite `segments.started_at` and `ended_at` are TEXT containing aware UTC ISO 8601 timestamps (`+00:00`). The recorder/indexer writes normalized UTC, and archive queries normalize offset-bearing inputs into the same representation before comparison. No SQLite local-time interpretation is involved.
- FFmpeg runs with `TZ=UTC`, names recordings with UTC calendar components and a second-resolution UTC timestamp, and resets media time inside each segment. Indexing takes the UTC start from the existing filename and adds the probed media duration for the end. Neither filenames nor recordings are changed by installation timezone settings.
- Progress, diagnostic checks, JSON logs, retention comparisons, webhook `occurred_at` and delivery timestamps remain UTC. These are machine-oriented absolute timestamps. Displayed Overview progress/latest-file times and Storage webhook delivery times use the installation timezone.
- Previously, Archive used UTC calendar dates and a fixed 24-hour timeline; its list filtered only segment start dates. Time lookup already accepted explicit absolute timestamps. Previous/next lookup skipped overlapping footage and reported gaps. The browser formatted UTC but supplied its own timezone for newly created camera schedules.
- Existing recording metadata provides the absolute start/end and each file's relative seek offset, sufficient for practical multi-camera synchronization. The inherited filename start precision is one second; this is not frame-accurate broadcast timecode.

## Configuration ownership

Timezone aliases are normalized using the server timezone database’s link metadata (for example, US/Eastern → America/New_York). The installation has one explicit IANA timezone, shared by every user/browser and recording schedule. The default is `APP_TIMEZONE=UTC`. This environment value initializes an installation that has no saved preference; it does not override a timezone saved through the UI.

**Account → Preferences → Change timezone** provides a searchable native datalist of zones from the server's timezone database, the configured zone, and current local server time. Saving applies immediately to schedule expectations. Other open clients pick up the setting through existing configuration polling, normally within ten seconds.

The preference lives in `settings.json` beside `DATABASE_PATH`, normally `/data/settings.json` on the existing data volume. Writes use a private temporary file, file/directory fsync, an atomic rename, and an in-process lock. No table, column, index, migration, new dependency, or infrastructure is introduced. `SETTINGS_PATH` can override the file location for direct deployments. Preserve this file with the data directory. Manual edits require an application restart. Invalid configuration fails startup rather than silently changing recording times.

**Upgrade note:** legacy `recording_schedule.timezone` values remain stored and accepted for API compatibility, but are no longer independent runtime overrides. Set the installation timezone to the intended zone before relying on existing schedules. Every schedule's wall-clock windows now use that installation timezone. Changing it can start or stop scheduled recording; it never rewrites footage timestamps. The camera editor shows the effective installation zone.

Schedules still use inclusive starts, exclusive ends, start-day weekdays, overnight windows including Sunday→Monday, and `00:00`→`24:00` for a full day. Python `zoneinfo` resolves offsets: an autumn repeated hour is eligible twice; a spring missing hour never occurs. No fixed-offset schedule arithmetic is added.

## Local archive boundaries

The browser formats absolute instants with `Intl` and the configured zone, independently of its own timezone. The server resolves local civil input with `zoneinfo`. Local-day bounds can span 23, 24, 25 or fractional hours; timeline positions use the actual UTC duration. Records overlapping the selected local day are included even when their start belongs to a different date.

For a repeated local time, the resolver returns both absolute instants and their explicit offsets. The user chooses one before playback starts. A nonexistent local time or skipped civil date produces an actionable error; it is never silently shifted forward. Existing `date=` archive API filtering remains UTC for compatibility; the updated UI uses explicit `start`/`end` bounds.

## Archive workflow and playback architecture

Archive opens with **Choose cameras…** selected by default; direct live-camera links select that camera. Select one camera, several via the expandable checklist, or all; **Select all** and **Clear** affect only temporary investigation state. Choose a local date/time or click/drag the common timeline, then play. Each camera has an aligned availability/gap track. Pointer dragging previews locally and performs one lookup on release; keyboard arrows (five minutes on a full day; one minute on quick ranges)/Home/End preview, and Enter/Space seeks.

Historical playback has its own state, separate from live Multiview and its saved layout. An adaptive grid balances selected cameras and stacks on narrow screens. Each camera can independently enter fullscreen, keeping the same media elements and shared session.

A shared logical clock anchors one absolute UTC instant to `performance.now()`. All offsets are `(master UTC time − segment UTC start)`. Initial playback waits for available media metadata, with a five-second readiness deadline so a slow camera cannot hold the session indefinitely. Thereafter buffering, lookup failure, missing files, unsupported codecs or gaps affect the individual camera. The master remains independent of any one video.

A 250 ms controller tick applies common play/pause/rate and corrects drift only beyond **750 ms**, or on an explicit seek/resume/rate change. It waits for metadata and avoids interfering with a seek already in progress. Pending native play promises are not repeatedly issued. The displayed clock updates on whole-second changes. Rates 0.5×, 1×, 2× and 4× use native playback; no transcoding or composition is performed.

At a camera's segment end, batch lookup resolves the file covering the current master instant, including any overlap offset. For a gap, the response caches the next segment's absolute start; the camera rejoins when the clock reaches it, without polling during that gap. Different cameras transition independently. Metadata lookups time out after ten seconds; obsolete requests are cancelled on session changes. Lookup failures retry after five seconds, with errors local to the affected camera. Static absence of future footage does not trigger repeated requests; use Refresh or seek again to discover newly indexed footage.

Single-camera previous/next, download, pagination and the automatic-next preference remain available. With automatic next disabled, single-camera playback pauses at the segment boundary. Shared playback stops when all selected cameras have exhausted their indexed footage. Fullscreen preserves the clock. Language is changed on Account; leaving Archive follows the existing route behavior and ends its playback.

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

Existing single-camera timeline/at/adjacent/file APIs remain compatible. Metadata responses omit paths and credentials. Naive absolute timestamps are rejected. Serving existing MP4s preserves byte-range support. The single-camera timeline is limited to 5000 segments; the UI list loads 200 at a time with **Load more**.

## Limits and verification

The browser and host must have current timezone databases. Camera/recorder clocks and second-resolution indexing limit real-world accuracy; the 750 ms correction threshold concerns browser drift, not camera capture-time guarantees. Native codec/seekability support, bandwidth and decoder capacity limit large selections. Media is delivered independently, so many cameras consume correspondingly more network/browser resources. Logs and webhook payloads deliberately remain UTC.

Tests cover UTC/Moscow/DST and fractional-day boundaries, repeated/missing times, overnight schedules, unchanged SQLite schema/recording timestamps, persisted atomic configuration, auth, local-date overlap, batch offsets/gaps and API validation. Clock tests cover independent offsets, pause/resume/rate, drift tolerance, segment boundaries and rejected play promises. Chromium uses real API/SQLite responses and native videos with a deliberately different browser timezone, exercises gap rejoining, independent transitions, shared seeks, individual-camera fullscreen, selection actions, DST choices and responsive layouts in both languages.


## Interval exports and frame retrieval

Multiview and Archive keep camera actions in the camera name row. Archive includes snapshot, fullscreen, download, and single-camera previous/next controls; Multiview includes Archive, fit/fill, snapshot, fullscreen, main-stream, and remove actions. Controls wrap in narrow tiles and remain available in fullscreen.

Live players include **Archive**, and the single-camera toolbar includes **Recent recordings**. Both open a reloadable `/archive?camera=…&at=…Z` link five minutes before the current server time, with that camera selected and a 15-minute timeline window. Quick **5/15/30/60 min** buttons show a window ending near the cursor (or current server time before playback); **Full day** restores the configured-zone civil day. Changing the range preserves camera selection, video elements, playback position, speed, and play/pause state. Ranges can cross local midnight or DST boundaries; availability queries use absolute UTC bounds. Dark tracks still represent gaps.

**Export clips** lives directly below the Daily recordings timeline and starts collapsed. Opening or closing it preserves playback and entered values. The timeline's upper row keeps its seek cursor; the amber export band beneath it selects an interval by dragging, with separate start/end handles. Recording tracks continue to seek normally. Handles support Left/Right (one second), Shift+Left/Right (one minute), Home and End. Manual date-time fields and handles synchronize using absolute timestamps. Zooming or changing date preserves export boundaries; **Locate start/end** brings an off-screen boundary into a five-minute view without seeking playback.

Choose one, several or all cameras using independent export checkboxes, **Select all** and **Clear**. The default follows Archive camera selection until export cameras are edited. One camera produces an MP4; several produce a ZIP with one independent MP4 per successful camera and `manifest.json`. No camera grid is encoded. The summary retains the selected interval and camera count while collapsed.

Fields use the configured installation timezone. Timeline handle selections retain the precise absolute second, including its occurrence in a repeated hour. Typed repeated times reveal explicit earlier/later choices; missing spring times are rejected by the existing resolver. Changing the installation timezone redisplays resolved boundaries without changing their instants; unresolved manual values are cleared. Start is inclusive and end exclusive, at the video frame precision available in the source.

- **Exact boundaries (encode)** trims partial segments with FFmpeg and encodes H.264 at original dimensions (libx264 CRF 18 in the production image; libopenh264 at 8 Mb/s where libx264 is unavailable). This is lossy encoding. Full-segment intervals automatically use stream copying. When partial segments are encoded, all selected pieces are encoded consistently so they can be joined.
- **Original quality (keyframe cuts)** copies video streams without re-encoding. Native seeking/cuts may include frames outside the requested bounds or differ by a GOP; use exact mode when boundaries matter. Playback codec compatibility remains browser-dependent.
- Gaps, including uncovered leading/trailing time, are omitted rather than filled with synthetic frames; the UI lists their absolute intervals in the configured timezone. Available footage is concatenated without waiting through gaps. Overlaps are trimmed so time is not intentionally duplicated. Exports contain video only, matching recordings.
- Indexed missing/deleted/unavailable files fail selection; files removed externally during processing, damaged media, unusable clips, and incompatible codec/resolution changes fail the job. The result is never silently presented as a complete interval with those files skipped. A fully deleted interval has no indexed footage and returns 404. Choose a shorter interval when a camera changed resolution; exact mode can accommodate original codec changes when encoded outputs remain compatible.

The authenticated API uses offset-bearing timestamps, normalized to UTC:

- `POST /api/recordings/exports`: `{ "camera_id": 1, "start": "2026-10-06T11:32:00Z", "end": "2026-10-06T11:37:00Z", "mode": "exact" }` (`mode` also accepts `copy`); returns 202 with a random token, `state`, `completed`, `total`, `gaps`, `error`, and effective `mode`. Alternatively send `camera_ids: [1, 2]` instead of `camera_id`; never send both. The list accepts 1–64 distinct positive IDs belonging to configured cameras or indexed camera archives (including deleted cameras).
- `GET /api/recordings/exports/{token}`: segment-level progress; states `processing`, `ready`, `failed`, or `downloading`. Additive fields: `phase` (`preparing`, `processing`, `packaging`, `ready`, `failed`), `format` (`mp4` or `zip`), `partial`, and `cameras` with each ID, state, completed/total pieces, gaps, effective mode, filename and error. Progress counts completed pieces, not an estimated percentage.
- `GET /api/recordings/exports/{token}/download`: an MP4 for one requested camera or a ZIP for multiple requested cameras (even if only one succeeds); one download consumes the temporary result. Unready/duplicate downloads return 409; expired/consumed tokens return 404.

Jobs run as bounded threads in the existing backend process, with at most two processing jobs and sixteen retained jobs. The same 31-day absolute range limit applies; exports additionally allow at most 2,000 segments. FFmpeg processes have a 15-minute per-command and one-hour job deadline, with two encoding threads. Data and diagnostics stay on disk; media is never assembled in Python memory. Temporary files live in an `exports` directory beside the database. Admission reserves conservative headroom across jobs, and rejects insufficient space with 507; shorter intervals may help. Each job has an 8 GiB temporary data limit, with 256 MiB minimum free space monitored during FFmpeg. Disk/storage failures produce a failed job. Segment progress is available after each piece; final assembly and decoding validation follow before the result becomes ready. A large individual segment can take time before the counter advances. Copy mode still decodes the final file for validation, without encoding it.

Multi-camera jobs process cameras sequentially using the same FFmpeg pipeline and one shared deadline, preserving the two-job concurrency limit. Failures are reported per camera; other cameras continue when safe. If none succeed the job fails. Otherwise `partial` identifies omitted cameras, and the ZIP manifest records the UTC interval, requested mode and every requested camera's outcome, effective mode and known gaps. Numeric-ID-based filenames are predictable and cannot contain paths. ZIP entries stream from disk with ZIP64 and no compression of MP4s. Complete-segment copy optimization is disclosed through effective modes. The 2,000-segment limit applies across all cameras. API authentication follows the existing installation-wide access model. There is no per-user camera permission model or interactive export cancellation endpoint; shutdown terminates active jobs.

Selection and pinning share `video.ARCHIVE_LOCK` with retention and manual deletion. Pins are reference-counted, and every guarded deletion skips pinned files as active. The lock is released before FFmpeg runs, allowing cleanup of other footage. Pins are released on success, failure, timeout, or graceful shutdown. Current recording files are rejected to avoid exporting files being written. Once the independent result is ready, original footage can be retained/deleted normally. Failed intermediates are removed immediately; ready/failed jobs expire one hour after processing; download completion/disconnection removes the result; graceful shutdown terminates export FFmpeg processes and removes jobs. Startup removes abandoned job directories after a crash. This uses the existing single backend worker deployment; no media service, database schema change, or dependency is added.

**Save frame** captures the currently decoded live/archive video into a native-resolution PNG using a browser canvas. It preserves playback and fullscreen state. If media has not decoded, canvas is unavailable, or browser security/codec support blocks capture, the player displays an actionable message. No backend fallback can recover the exact displayed frame when the browser has no decoded frame, so the user can retry after playback becomes available. Downloads use temporary object URLs that are revoked after use.

Retrieval tests decode real exported files and cover single/multiple segments, stream-copy preservation, overlaps/gaps, missing/deleted/corrupt files, invalid/naive ranges, retention and manual cleanup races, shutdown/restart cleanup, and UTC offsets across midnight and both DST transitions. Browser tests exercise native live/archive PNG downloads, camera-preserving live navigation, range changes without disturbing playback, timeline boundaries, multi-segment export/download, omitted gaps, unsupported frame feedback, both languages, and mobile width.
