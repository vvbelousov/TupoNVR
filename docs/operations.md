# Deployment and operating guide

[English README](../README.md) · [Русский README](../README_RU.md)

Detailed deployment, storage, API, diagnostics, and validation information moved from the README. For a first installation using a published image, start with the README Quick Start. This shared technical reference is in English.

## Published-image deployment

Application images use the Docker Hub repository [vvbelousov/tuponvr](https://hub.docker.com/r/vvbelousov/tuponvr/tags). Obtain `docker-compose.yml`, `compose.image.yml`, `mediamtx.yml`, and `.env.example` from the same reviewed release into one directory, or clone the repository as shown in the README. Copy `.env.example` to `.env`, configure storage/network/authentication, and select a published image tag:

```dotenv
NVR_IMAGE=vvbelousov/tuponvr:0.1.0
```

Use `0.1.0`, `latest`, or another tag listed on Docker Hub. A fixed version gives predictable updates; `latest` follows the current stable release. With the base Compose file, explicitly pull the image and disable local building:

```sh
docker compose up -d --no-build --pull always
```

Alternatively, the included image overlay sets `pull_policy: always` and requires `NVR_IMAGE`:

```sh
docker compose -f docker-compose.yml -f compose.image.yml up -d --no-build
```

Both commands preserve the same configuration and volumes without requiring Node/Python on the host. Compose v2 is required. If the selected tag is unavailable, choose an existing published tag or build from source.

### Source build

Clone the repository, copy `.env.example` to `.env`, configure storage/network/authentication, and clear `NVR_IMAGE` (set `NVR_IMAGE=`). Then run:

```sh
docker compose up -d --build
```

## Video architecture

```text
RTSP camera ── one upstream ── MediaMTX ── WebRTC/WHEP ── browser
                                 │
                                 └── local RTSP ── FFmpeg -c:v copy ── MP4 segments
```

For a substream, MediaMTX creates a separate path and connects to the camera only while Multiview viewers are present. The main stream stays connected when recording is permitted by the schedule and storage is ready; otherwise it connects on demand. Each viewer has its own WebRTC session, while MediaMTX reuses the source RTSP upstream.

React uses MediaMTX's official `reader.js`; see [its license](../frontend/public/MEDIAMTX-LICENSE.txt). Tiles switch between `contain` and `cover` in the browser without server resizing. Multiview layouts are saved and support up to 12 columns, dragging, and resizing.

FFmpeg copies the video track without decoding (`-c:v copy`); audio is not recorded. Each process belongs to one `ProcessSupervisor`, without shell interpolation. On normal shutdown, FFmpeg receives SIGINT and up to 15 seconds to finalize MP4 before SIGKILL. Recorder restarts use backoff capped at 60 seconds, plus up to one second of jitter; a run lasting over 30 seconds resets the delay to two seconds. The recorder rotates cleanly at each UTC hour boundary. MediaMTX reconnects to the source independently.

Recordings use this UTC directory and filename structure:

```text
<storage>/<destination>/<camera_id>/YYYY/MM/DD/HH/YYYYMMDDTHHMMSS.mp4
```

Segments default to about ten minutes; exact boundaries depend on camera keyframes. Archive lists finalized MP4 files whose duration passes `ffprobe` checks; this is not a full integrity scan of every frame. Files missing the MP4 `moov` atom can be removed after 24 hours. FastAPI serves playback and downloads, including HTTP byte ranges. Browser playback requires a supported video codec.

## Storage and retention

`DEFAULT_RECORDING_PATH` in `.env` is a **host path** mounted at `/recordings` in the application container. It can be a local disk or an already mounted NFS/SMB directory. For example:

```sh
sudo mount -t nfs nas:/export/nvr /srv/nvr
# Then set DEFAULT_RECORDING_PATH=/srv/nvr in .env.
```

A camera's `recording_destination` is a subdirectory name, such as `default` or `garage`; absolute paths and `..` are rejected. A separate filesystem can be mounted inside that directory on the host. Configure mounts, write permissions, and mounting after reboot on the host. Check them before enabling recording, including behavior when NFS/SMB becomes unavailable.

Retention defaults to seven days and accepts 1–3650 days; an empty value means no age limit. Recordings from deleted cameras use a seven-day age limit measured from each segment’s start, not from camera deletion. Every five minutes, the worker indexes recordings and removes expired segments. When space is low, it deletes the oldest indexed files toward the `MIN_FREE_SPACE_GB` reserve on each affected filesystem. The current hour of an active camera is protected from deletion. If active segments occupy all available space, the reserve cannot be guaranteed: size storage appropriately, configure retention, and monitor `/metrics`.

Individual segments can be deleted through the API. SQLite uses WAL, and the worker removes metadata for missing files. Camera IDs are not reused after deletion. Existing databases receive the application's established schema upgrades at startup while preserving rows and accounting for existing recordings and layouts.

## Configuration

| Variable | Default in `.env.example` | Purpose |
|---|---|---|
| `NVR_PORT` | `8080` | Application port on the host |
| `WEBRTC_PORT` | `8889` | WHEP HTTP signaling port |
| `WEBRTC_UDP_PORT` | `8189` | WebRTC media UDP port |
| `WEBRTC_HOST` | `127.0.0.1` | Host IP/DNS for ICE; use the LAN IP for other devices |
| `DATA_DIR` | `./data` | Host directory for SQLite and persisted settings |
| `DEFAULT_RECORDING_PATH` | `./recordings` | Host recording root |
| `SEGMENT_SECONDS` | `600` | Target segment duration in seconds |
| `MIN_FREE_SPACE_GB` | `5` | Free-space reserve |
| `AUTH_USERNAME`, `AUTH_PASSWORD` | empty | Optional UI/API login; set both |
| `COOKIE_SECURE` | `false` | Secure session cookie; enable for HTTPS access |
| `LOG_LEVEL` | `INFO` | Logging level |
| `DEFAULT_LANGUAGE` | `en` | Initial interface language: `en` or `ru` |
| `APP_TIMEZONE` | `UTC` | Initial installation timezone; a UI-saved preference takes precedence |
| `NVR_IMAGE` | `vvbelousov/tuponvr:0.1.0` | Published application image/version; blank uses the local source-build image |
| `NVR_UID`, `NVR_GID` | `0`, `0` | Optional numeric container identity; non-root requires writable host directories |
| `WEBHOOK_URL` | empty | Generic HTTP(S) notification endpoint; empty disables notifications |
| `WEBHOOK_TOKEN` | empty | Optional Bearer token |
| `WEBHOOK_DEBOUNCE_SECONDS` | `60` | Continuous failure duration before the first event |
| `WEBHOOK_COOLDOWN_SECONDS` | `600` | Minimum interval between new incident events for the same subject |

Browser requests that change state validate Origin. A reverse proxy must preserve the original Host. For HTTPS, set `COOKIE_SECURE=true`.

The application runs one Uvicorn process (`--workers 1`); a second worker could start duplicate FFmpeg recorders. The database contains camera passwords in plaintext: protect `DATA_DIR` and backups. SQLite files are created with permissions `0600`. Structured backend events omit private URLs and passwords, and Uvicorn access logging is disabled.

Do not expose MediaMTX and the NVR directly to the internet. **Optional authentication protects the UI, API, and archive files, but not MediaMTX's direct WebRTC port.** Restrict 8889/8189 with a firewall or provide a separately protected reverse proxy when access must be limited. With an HTTPS UI proxy, configure HTTPS for the WHEP endpoint as well; browsers otherwise block mixed content.

### Container permissions

Root remains the default for compatibility with existing bind mounts; it is not required by FastAPI, FFmpeg, or MediaMTX API calls. To run the application as a non-root user, create the data/recording directories first with ownership matching `NVR_UID`/`NVR_GID`, then set those numeric IDs in `.env`. Existing root-owned databases, settings, and markers may need an intentional ownership change before switching. Do not loosen permissions globally or change NAS ownership blindly.

The application uses a normal bridge network, drops `NET_RAW`, and prevents new privileges. It does not need privileged mode, host networking, or a Docker socket. Writable data/recording mounts and temporary-file space remain necessary. The image healthcheck checks `/health`; `/ready` separately verifies MediaMTX connectivity.

## Updating, backup, and recovery

Keep `DATA_DIR`, `DEFAULT_RECORDING_PATH`, `.env`, and any host/NAS mount configuration across updates. The data directory includes SQLite, its WAL/SHM files when present, and timezone `settings.json`; storage markers live on their respective recording destinations.

Before updating, stop the application cleanly with `docker compose stop nvr-app` (include the published-image `-f` arguments if using that deployment). Back up the complete data directory, configuration, recording directories, and `.nvr-storage-id` files while recording is stopped. Protect backups like camera credentials. Do not copy only an actively written SQLite file or regenerate storage IDs during restoration. There is no automated backup/recovery feature.

For a published-image deployment, read the release notes, set `NVR_IMAGE` to the desired fixed version, and run:

```sh
docker compose -f docker-compose.yml -f compose.image.yml pull
docker compose -f docker-compose.yml -f compose.image.yml up -d --no-build
```

For a source build, update the reviewed source and run `docker compose up -d --build`. Check `/health`, `/ready`, storage readiness, and recording progress afterward. The application performs its existing database upgrades at startup. An older image may not support an upgraded database; rollback can require restoring the matching pre-upgrade data backup. Do not delete persistent directories when recreating containers.

To recover, keep the application stopped, restore the complete matching backup with the expected ownership and storage mounts, select the matching application version, and start it. Check destination IDs and readiness before recording. Verify your backup procedure on disposable data; recovery has no dedicated UI or automatic migration rollback.

## Interface and API

- **Account:** language, installation timezone, configured username and logout. `POST /api/logout` clears the browser session cookie; cached HTTP Basic credentials do not authenticate application requests. Account passwords are configured through the environment, with no UI password-change flow.
- **Overview:** camera counts, connectivity, recording progress, free space and errors. Watch opens `/cameras/<id>/live` for that camera’s main stream. Refresh and browser navigation preserve the camera; saved Multiview selections stay independent.
- **Cameras:** add, edit, disable, check, and delete cameras. Blank passwords or RTSP URLs preserve saved values during editing; a separate switch removes the substream. List URLs hide paths and query parameters that may contain secrets.
- **Multiview:** add live cameras, drag tile headers, resize using the corner, and choose `contain`/`cover`. Individual viewing uses the main stream.
- **Archive:** investigate one, several, or all cameras at a shared local date/time. Aligned availability tracks expose gaps; shared controls provide play/pause, seek, and speed. Each camera can independently enter fullscreen. Segment transitions happen independently. Single-camera previous/next, automatic-next, pagination, and downloads remain available.
- **Storage:** destination availability, write access, free-space reserves, missing-mount protection, and webhook delivery status.

OpenAPI is available at `/docs`. Main routes include:

| Area | Routes |
|---|---|
| Cameras | `GET/POST /api/cameras`, `GET/PUT/PATCH/DELETE /api/cameras/{id}`, `GET /api/cameras/{id}/edit`, `POST /api/cameras/{id}/start`, `/stop`, `/check`, `GET /api/cameras/{id}/status` |
| Archive files | `GET /api/recordings?camera_id=&date=YYYY-MM-DD&limit=&offset=`, `GET/DELETE /api/recordings/{id}`, `GET /api/recordings/{id}/download` |
| Archive lookup | `GET /api/recordings/timeline?camera_id=&start=&end=`, `GET /api/recordings/at?camera_id=&time=`, `GET /api/recordings/{id}/adjacent?direction=next\|previous`, `POST /api/recordings/timelines`, `POST /api/recordings/resolve` |
| Time and language | `GET /api/config`, `GET/PUT /api/time`, `GET /api/time/day?date=YYYY-MM-DD`, `POST /api/time/resolve`, `GET /api/language` |
| Layout and dashboard | `GET/PUT /api/layout`, `GET /api/dashboard` |
| Storage and notifications | `GET /api/storage/status`, `GET /api/storage/destinations`, `PUT /api/storage/destinations/{name}`, `POST /api/storage/destinations/{name}/protection`, `GET /api/notifications/status` |
| Service health | `GET /health`, `GET /ready`, `GET /metrics` |

When authentication is enabled, `POST /api/login` creates an HttpOnly session cookie. CLI clients use the same login endpoint and retain its session cookie. Unauthenticated protected APIs return 401 without an HTTP Basic challenge.

## Diagnostics

```sh
docker compose ps
docker compose logs --tail=100 nvr-app
docker compose logs --tail=100 mediamtx
curl http://localhost:8080/health
curl http://localhost:8080/ready
```

- **Online but no video:** check the camera codec. H.264 has the widest browser support; H.265 support varies. Check `WEBRTC_HOST`, UDP 8189, TCP 8889, and the browser console.
- **No recording:** check mounts, directory permissions, free space, and whether MediaMTX can access the main RTSP path. Status reports FFmpeg errors without exposing private URLs.
- **Empty archive:** the segment currently being written is not indexed. Wait for completion and the next scan (up to five minutes), or stop recording cleanly. MP4 files that fail indexing checks are excluded; files missing the MP4 `moov` atom can be removed after 24 hours.
- **Changed WebRTC port:** recreate the Compose services. The UI reads the port from backend runtime configuration.
- **Unstable RTSP:** MediaMTX reconnects and FFmpeg restarts with backoff. There is one FFmpeg process per recording camera.

## Development and validation

Requirements: Python 3.12+, Node 22+, FFmpeg/ffprobe, and the operating system timezone database (`tzdata`).

```sh
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements-dev.txt
npm ci --prefix frontend
scripts/validate.sh
```

The validation script runs `pip check`, Ruff's Python correctness rules (F), frontend reader/clock tests, strict TypeScript checking, the Vite build, and pytest. There is no repository-wide formatting standard. `NVR_PYTHON` selects another Python executable. Tests isolate SQLite and recordings in temporary directories.

Integration and browser tests are skipped by default and required in CI. To run them locally:

```sh
# Download and extract official MediaMTX 1.21.1 for your operating system.
.venv/bin/python -m playwright install chromium
NVR_MEDIAMTX_BIN=/absolute/path/to/mediamtx NVR_RUN_BROWSER=1 scripts/validate.sh
# Set NVR_CHROME_EXECUTABLE to use an installed Chrome executable instead.
```

The integration test needs a free `localhost:8554`; other ports are selected automatically. It starts a synthetic H.264 RTSP camera, MediaMTX, and the application, checking FFmpeg progress, active diagnostics, storage ID loss/recovery, scheduled stop/resume, MP4 finalization/indexing, downloads, and HTTP Range.

Chromium tests exercise the built React assets in English and Russian, including login, schedule editing, storage protection, diagnostics, archive pagination, sequential playback, and layout persistence during polling. Synchronized archive tests use real API/SQLite responses and native videos, with a browser timezone different from the installation timezone. Backend and clock tests cover timezone conversion, DST, schedules, unchanged recording timestamps, independent offsets, gaps, shared controls, and drift correction.

[The CI workflow](../.github/workflows/ci.yml) runs checks on Python 3.12 / Node 22, verifies the downloaded MediaMTX checksum, builds the Docker image, and checks `/health`, static assets, and SQLite upgrades inside the container. CI does not publish or deploy anything. Long-running soak tests with real cameras and NFS/SMB have not been performed.

## Current limitations

There is no audio recording, detection, PTZ, ONVIF, multi-user management, background transcoding, or native mobile app. The API does not calculate FPS/bitrate; it reads `bytes_received` from MediaMTX without decoding video. MP4 files damaged by sudden power loss are not repaired automatically. Back up SQLite, persisted settings, and recordings independently.

The direct WebRTC endpoint is accessible within the LAN without UI authentication. Retention controls database and recording growth, but unlimited retention requires external free-space monitoring. Archive synchronization is practical rather than frame-accurate: second-resolution recording starts, camera latency, codec support, bandwidth, and browser decoder capacity limit accuracy and large selections.

## Recording health and camera checks

Overview and Cameras display separate connectivity and recording indicators in both languages. **Online** is green and **Offline** is red. Intentional pauses, schedule pauses, and disabled cameras are neutral. STARTING/RECONNECTING are warnings; STALLED/STORAGE_UNAVAILABLE are errors. Old recorder errors are not shown as active incidents after recording stops. Paused cameras show **No active recording** instead of stale progress.

`GET /api/cameras/{id}/status` returns connectivity fields: `state`, `connectivity_state` (ONLINE/OFFLINE/UNKNOWN/DISABLED), `online` (true/false/null), `connectivity_checked_at`, and `connectivity_last_success`. UNKNOWN means an initial or stale check; DISABLED means the entire camera is disabled. Both are neutral. Recording fields include `recording_enabled`, `recording_expected` (including the schedule), `recording_health`, `recorder_running`, `last_progress_at`, `progress_age_seconds`, and `latest_segment`.

The `camera_online` metric is 1/0, or NaN for unknown/disabled cameras. A live FFmpeg process without advancing frame/out_time_us is not WRITING: after 30 seconds it is STALLED. Health and probe caches live in memory, not SQLite.

One backend worker probes **the configured main RTSP source directly** with ffprobe, independently of recording, viewers, and MediaMTX. Probes read video metadata with a codec and positive dimensions, without transcoding, with bounded probesize/analyzeduration and a ten-second total timeout. At most two probes run concurrently. The normal interval is 60 seconds after completion; the first failure retries after 15 seconds, and two consecutive failures mark Offline. Success immediately restores Online. Until failure is confirmed, the previous result remains; results older than 180 seconds become UNKNOWN.

The worker checks its queue every five seconds; delays also depend on camera count and response time. Fully disabled cameras are not probed periodically. Source or credential changes invalidate the cache.

The **Check** button uses the same probe and can check disabled cameras. Concurrent requests share a probe, and results are reused for ten seconds. A disabled camera's check finishes before its cache is cleared. Changing the source during a check returns HTTP 409 asking for a retry. UI/API polling does not start probes. Diagnostics preserve MediaMTX sourceOnDemand behavior and do not create temporary media paths.

Cameras must accept a brief additional RTSP session; devices with strict connection limits may reject probes. Results are availability snapshots, not continuous guarantees or WebRTC/ICE/firewall playback tests. H.264 is marked as likely browser-compatible. Raw camera messages and stderr are discarded; private URLs and credentials are excluded from logs, metrics, UI, and API errors.

## Per-destination storage protection

Each destination is checked independently for availability, free space on its filesystem, a real temporary write/fsync, and the `MIN_FREE_SPACE_GB` reserve. If unavailable, unwritable, or below reserve, its cameras stop recording and resume after recovery. Other destinations and live viewing continue. Checks run roughly every five seconds in bounded subprocesses with a three-second timeout. Stale status is not considered ready. Archive metadata for an unavailable destination is not removed as if its files were missing.

Enable identity protection for external mounts **before enabling camera recording**:

1. Mount the resource under `DEFAULT_RECORDING_PATH` on the host, for example `/srv/nvr/nas`. Configure write permissions and mounting after reboot.
2. In **Storage**, choose **Create protection ID**. The application creates a random 256-bit ID, atomically publishes `.nvr-storage-id` without overwriting, and verifies readback. The destination must already exist; this operation does not create a directory in place of a missing mount. If it is not visible as a separate mount point, creation requires explicit confirmation of intentionally local storage. Do not confirm this for a missing external resource.
3. If an ID already exists, choose **Use existing ID**. A mismatch with the expected ID requires explicit confirmation before adopting the connected resource's ID. Creation never replaces an existing file or configured expected ID. Repair an invalid marker manually on the intended resource. If the filesystem lacks hard-link/fsync support, automatic creation fails and manual setup remains available.
4. **Manual configuration:** create `.nvr-storage-id` on the resource with 1–128 characters (ASCII letters, digits, `_`, `-`) and save the expected ID. Changing or disabling protection requires UI confirmation. `PUT /api/storage/destinations/nas` accepts `{"expected_marker":"nas-primary-01"}`. `POST /api/storage/destinations/{name}/protection` accepts `action` (`create`/`use_existing`), `previous_expected_marker` (including null), `replace_existing`, and `allow_local`. Operations apply only to configured destinations and use bounded subprocesses, a three-second timeout, safe errors, and protection against symlinks/directory replacement. If creation fails after writing the marker, it may remain; verify the resource and adopt its existing ID.
5. Confirm that the destination is ready, then enable recording. If it is not listed yet, register it through the manual configuration API or add a camera with recording disabled and that destination selected.

If a mount disappears or its ID is absent/mismatched, the NVR does not create the protected directory/ID or start recording into the underlying local directory. `{"expected_marker":null}` disables protection. Ordinary local destinations, including `default`, retain automatic directory creation.

The ID checks identity; it is not a secret or a replacement for an OS mount. Already open file descriptors remain under OS control after resource loss. The NVR cannot guarantee the current segment survives or resolve hung kernel I/O on NFS/SMB. For nested host mounts, configure suitable Compose bind mounts and verify visibility inside the container; the NVR does not manage mount propagation.

## Editing cameras

The editor loads saved fields through authenticated `GET /api/cameras/{id}/edit`: the full URL path without userinfo, username, substream, and other settings. Passwords are never returned and the password field stays blank. URL query options are hidden because they can contain vendor tokens; they are preserved when a changed URL supplies no new query options. A new query replaces the old query entirely.

Embedded credentials are preserved when editing the safe URL without changing username/password. A new password replaces the old one; a blank password preserves it. A new username retains the saved password; an empty username removes URL userinfo. Overview and general camera lists continue to display shortened URLs.

The form sends only changed fields using `PATCH /api/cameras/{id}`. Omitted fields remain unchanged; `substream_url:null` explicitly removes the substream. `PUT` also supports partial updates and retains the established blank-URL/password and `clear_substream` behavior. The create API is unchanged. Validation errors omit the original request to avoid returning passwords or tokens.

## Weekly recording schedules

In the camera editor, enable **Limit recording hours**, configure the installation timezone in **Account**, and specify 1–14 windows with weekdays and times. No schedule means continuous recording. `enabled` and `recording_enabled` remain the main switches: schedules do not enable disabled cameras or restrict live viewing.

Example `recording_schedule` value in a camera create/update request:

```json
{"timezone":"Europe/Moscow","windows":[{"days":[0,1,2,3,4],"start":"22:00","end":"06:00"}]}
```

The `timezone` field remains for compatibility with older clients; all cameras' effective windows use the installation timezone. Days 0–6 mean Monday–Sunday and refer to the window's start day. An end before the start crosses midnight, including Sunday→Monday. Starts are inclusive and ends exclusive. Use `00:00`→`24:00` for a full day; equal times are rejected.

DST uses local civil time: the repeated autumn hour occurs twice and the missing spring hour is skipped. Boundaries apply at the next sync (normally within five seconds), followed by up to 15 seconds for FFmpeg to finalize. Completed files remain in Archive. `null` removes the schedule; older clients that omit the field during updates preserve the saved schedule.

## Generic webhook notifications

Set `WEBHOOK_URL` (HTTP(S)) and optionally `WEBHOOK_TOKEN`, then recreate the application container. There are no provider-specific integrations or extra services. A failure of expected recording or its storage must persist for `WEBHOOK_DEBOUNCE_SECONDS`; one continuous failure produces one event. Recovery sends `recovery`; disabling recording or leaving its schedule closes the incident as `resolved`. New incidents respect `WEBHOOK_COOLDOWN_SECONDS`. A shared destination failure generates a storage event and suppresses duplicate camera events.

Example JSON POST:

```json
{"id":"stable-event-uuid","kind":"recording","camera_id":1,"destination":"nas","reason":"stalled","state":"failure","occurred_at":"2026-10-06T09:00:00+00:00"}
```

Storage events omit `camera_id`. Payloads exclude camera names, private URLs, passwords, filesystem paths, and raw errors. `X-NVR-Event-ID` repeats `id`; a configured token adds `Authorization: Bearer ...`. The endpoint and token are not displayed in UI/logs. Use HTTPS outside a trusted LAN. Redirects are not followed and proxy environment settings are ignored.

The queue and debounce state persist in existing SQLite tables. Delivery runs in a separate coroutine without blocking recording: a five-second timeout and up to three attempts, with 10/20-second retry delays. Only successful HTTP responses count. Events use FIFO ordering, but permanently failed deliveries do not indefinitely block recovery events. Receivers must deduplicate by `id`; restarts can cause repeated POSTs.

The queue holds at most 1000 pending events; completed/failed events are removed after seven days. Delivery status appears in **Storage** and `/api/notifications/status`. Changing the URL redirects queued deliveries to the new endpoint; an empty URL pauses delivery and observation. This reports incidents while the NVR is running, not complete server outages, and is not a permanent incident log.

## Interface language

The interface supports English and Russian. English is the default regardless of browser language. Set `DEFAULT_LANGUAGE=en` or `DEFAULT_LANGUAGE=ru` in `.env` and recreate the container (`docker compose up -d --build`) to change the initial language. Other values fail startup with a clear error.

The **Language** selector is in **Account → Preferences** (`/account`), accessed from the bottom of the sidebar (beside the product name on mobile). Changes apply immediately and are remembered in browser local storage, overriding the server default. The login page uses the saved browser preference or server default. Dates use the selected language and installation timezone. With browser storage disabled, switching works for the current visit; the next visit uses the server default. Remove the `nvr-language` local-storage key to restore the server default.

`GET /api/language` returns only `{"default_language":"en"}` (or `ru`) without authentication so login can use the configured language. Normal configuration and camera/storage APIs retain their authentication requirements.

## Installation timezone and synchronized archive

Configure the installation timezone in **Account → Preferences → Change timezone**. `APP_TIMEZONE=UTC` is the initial default; a UI-saved preference takes precedence and persists in `/data/settings.json` on the existing data volume. No database migration is required. Recording metadata, filenames, logs, and webhook payloads remain UTC.

**Upgrade note:** existing per-camera schedule timezone fields are retained for compatibility, but every schedule now follows the installation timezone. Configure it before relying on schedules after upgrading.

Archive supports one, several, or all cameras on one absolute playback clock. Selection is temporary and separate from saved live Multiview layouts. Aligned availability tracks show gaps. Shared play/pause/seek and native 0.5×/1×/2×/4× speeds apply to active cameras; fullscreen expands an individual camera without losing synchronization.

The master clock uses monotonic browser time and UTC segment metadata. Independent seek offsets align files with different start times; drift above 750 ms is corrected. Each camera transitions across segments independently. Missing footage does not stop other cameras; a camera rejoins when its next known segment starts. Refresh updates metadata and active lookups to discover newly indexed footage. Shared playback stops when all selected cameras have exhausted their indexed footage.

Local archive dates use their actual DST-aware bounds and include segments overlapping the day, even when they started earlier. Repeated local times require an offset choice; nonexistent times produce an error. The browser timezone does not determine the query instant. Original recordings are served independently without transcoding or media composition.

Timeline requests require timestamps with `Z` or an explicit offset and a range of at most 31 days. The single-camera endpoint allows up to 5000 segments; the batch endpoint allows up to 20,000. Shorten the range if exceeded. Lists load 200 records at a time with **Load more**. Batch timeline and playback lookup APIs use existing SQLite metadata and camera/time indexes, without new services or per-pixel requests.

See [the time model, configuration, API details, synchronization strategy, and limitations](time-and-archive.md).
