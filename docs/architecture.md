# Architecture

[Documentation](README.md) · [Русский](architecture_RU.md)

```text
Browser: React UI
  ├── HTTP → FastAPI: configuration, diagnostics, archive metadata/files
  └── authenticated WHEP → FastAPI → private MediaMTX; negotiated WebRTC UDP → MediaMTX

Application (one Uvicorn worker)
  ├── SQLite: cameras, layouts, destinations, segment metadata, webhook queue
  ├── settings.json: installation timezone
  ├── MediaMTX API: managed main/substream RTSP paths
  ├── ffprobe: bounded direct camera checks and completed-file indexing
  └── FFmpeg recorder per active camera → original video copied into MP4 storage
```

`backend/main.py` owns authenticated HTTP APIs, application lifetime, and reconciliation. `db.py` initializes SQLite/WAL and performs the existing startup upgrades. It stores camera credentials; API serializers hide private URLs/passwords from general responses.

`video.py` owns MediaMTX path configuration, direct connectivity probe caching/debouncing, FFmpeg supervision, indexing, and retention. Recording is independent of browser viewers. FFmpeg copies the video track without decoding/transcoding and receives a bounded graceful shutdown. Exactly one application worker must own these processes.

`storage.py` checks each destination in bounded subprocesses, verifies write/fsync and free-space reserves, and checks optional `.nvr-storage-id` identity before creating recording directories. The application does not mount filesystems or manage NAS recovery. Protected unavailable storage blocks its recorders without disabling other destinations or live viewing.

`policies.py` evaluates weekly recording windows in the installation timezone. `timeconfig.py` persists that timezone atomically beside SQLite; absolute recording timestamps and filenames remain UTC. No timezone database migration is needed.

`notifications.py` debounces failures, persists its queue in SQLite, and delivers generic webhooks asynchronously. It has no provider-specific integrations and does not report complete server outages.

`frontend/src/main.tsx` provides navigation, camera editing, Overview, and live Multiview. `Storage.tsx` owns storage operations. `Archive.tsx` and `archiveClock.ts` maintain a separate historical investigation session: one monotonic absolute clock, independent native MP4 players, batch metadata resolution, and 750 ms drift correction. `time.ts` formats installation-local civil time and anchors display to server time. Live and archive state are separate.

Persistent directories are `/data` (SQLite, WAL/SHM, timezone settings) and `/recordings` (destinations and UTC camera/date/hour directories). MediaMTX paths are rebuilt from application configuration after restart. See [time and archive details](time-and-archive.md) and [release/deployment decisions](releasing.md).

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

Segments default to about ten minutes; exact boundaries depend on camera keyframes. Archive lists finalized MP4 files whose duration passes `ffprobe` checks; this is not a full integrity scan of every frame. Unindexed damaged files, including MP4s missing the `moov` atom, are left on disk for inspection; they are not automatically repaired or removed by the indexer. FastAPI serves playback and downloads, including HTTP byte ranges. Browser playback requires a supported video codec.
