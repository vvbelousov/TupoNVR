# Operating guide

[Documentation](README.md) · [Русский](operations_RU.md)

Installation is covered in [installation](installation.md), settings in [configuration](configuration.md), backups in [storage](storage.md), and playback/export in [archive](time-and-archive.md).

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

The interface supports English and Russian. English is the default regardless of browser language. Set `DEFAULT_LANGUAGE=en` or `DEFAULT_LANGUAGE=ru` in `.env` and recreate the container according to [installation](installation.md#lifecycle) to change the initial language. Other values fail startup with a clear error.

The **Language** selector is in **Account → Preferences** (`/account`), accessed from the bottom of the sidebar (beside the product name on mobile). Changes apply immediately and are remembered in browser local storage, overriding the server default. The login page uses the saved browser preference or server default. Dates use the selected language and installation timezone. With browser storage disabled, switching works for the current visit; the next visit uses the server default. Remove the `nvr-language` local-storage key to restore the server default.

`GET /api/language` returns only `{"default_language":"en"}` (or `ru`) without authentication so login can use the configured language. Normal configuration and camera/storage APIs retain their authentication requirements.


## Current limitations

There is no audio recording, detection, PTZ, ONVIF, multi-user management, background transcoding, or native mobile app. The API does not calculate FPS/bitrate; it reads `bytes_received` from MediaMTX without decoding video. MP4 files damaged by sudden power loss are not repaired automatically. Back up SQLite, persisted settings, and recordings independently.

Browser signaling requires application authentication when configured; direct MediaMTX TCP access must remain private. Retention controls database and recording growth, but unlimited retention requires external free-space monitoring. Archive synchronization is practical rather than frame-accurate: second-resolution recording starts, camera latency, codec support, bandwidth, and browser decoder capacity limit accuracy and large selections.


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
