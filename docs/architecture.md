# Architecture

```text
Browser: React UI
  ├── HTTP → FastAPI: configuration, diagnostics, archive metadata/files
  └── WHEP/WebRTC → MediaMTX: live video

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
