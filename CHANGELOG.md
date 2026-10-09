# Changelog

## 0.2.1

- Optional one-shot YAML camera bootstrap, plus authenticated camera YAML import/export on the Cameras page.
- Stable portable camera keys, complete recording settings/schedules/retention, partial updates and idempotent imports.
- Credential-free exports by default, explicit secret backups, field-level previews, confirmation, stale-preview checks and transactional import rollback.
- Safe bounded YAML parsing, optional read-only Compose bootstrap overlay and bilingual schema/migration documentation.

### Upgrade notes

Back up SQLite and recordings before upgrading. Startup adds stable keys to existing camera records without changing database IDs or recording associations. Existing installations need no bootstrap file or extra services. SQLite remains authoritative; bootstrap only imports into an empty camera database and never overwrites UI edits on restart. Malformed optional bootstrap files produce warnings and startup continues.

Use `vvbelousov/tuponvr:0.2.1` after publication. Keep the same recording/data volumes and Compose overlays. YAML backs up camera settings only; destination protection, installation timezone and footage need separate backup/migration. Older images may require the pre-upgrade database backup for rollback. See [camera configuration](docs/camera-configuration.md).

## 0.2.0

- Dedicated single-camera live view, reloadable camera links, and fullscreen controls.
- PNG frame snapshots and MP4 interval exports with exact or keyframe-aligned cuts, gap reporting, and protection against concurrent cleanup.
- Recent-recording shortcuts and quick 5/15/30/60-minute archive ranges.
- Manual recording cleanup with filtering, previews, confirmation, progress, and protection for active files and missing mounts. Unindexed damaged files are left for inspection.
- Login screen and Account page; random revocable 24-hour sessions, same-origin write checks, and authenticated WebRTC signaling through the application.
- Optional Linux LAN deployment with native WebRTC interface discovery; first-camera guidance and credential-safe connection diagnostics.
- Concise READMEs and organized English/Russian technical documentation.

### Upgrade notes

Back up the data directory (including SQLite WAL/SHM and timezone settings), recordings, `.env`, and storage identity markers while recording is stopped. Read [backup and recovery](docs/storage.md#updating-backup-and-recovery).

Recreate **both** Compose services using the 0.2.0 deployment files; updating only the application image leaves previously published MediaMTX TCP ports exposed. Preserve the same Compose overlays on every lifecycle command.

- Application access defaults to `127.0.0.1`. For shared LAN access set `NVR_BIND=0.0.0.0` and both `AUTH_USERNAME` and `AUTH_PASSWORD`.
- MediaMTX TCP 8554/8889/9997 must remain private. Browser signaling now uses `/api/media` on the application origin; `WEBRTC_PORT` is obsolete. Allow application TCP (8080 by default) and WebRTC UDP (8189 by default).
- In bridge mode, set `WEBRTC_HOST` to a reachable host address for remote viewers. The optional `compose.lan.yml` requires Linux Docker Engine, Compose 2.24.4+, and free MediaMTX TCP host ports; blank `WEBRTC_HOST` enables host-interface discovery.
- Restarts invalidate login sessions. For HTTPS configure `COOKIE_SECURE=true` and trusted reverse proxy IPs in `FORWARDED_ALLOW_IPS`.
- The template selects `NVR_IMAGE=vvbelousov/tuponvr:0.2.0`; use it after the image is published. Contributor build instructions are in [development](docs/development.md).

See [security and migration](docs/security-hardening.md) and [installation](docs/installation.md). An older image may not support newer persistent data; rollback may require the matching pre-upgrade backup.

## 0.1.0

- Lightweight two-container deployment with RTSP cameras, MediaMTX live viewing, and FFmpeg stream-copy MP4 recording.
- Camera management, direct connectivity diagnostics, recording health, and live Multiview layouts.
- Single-camera and synchronized multi-camera archive investigation, shared timeline/controls, gap rejoining, and independent segment transitions.
- Installation timezone, DST-aware archive selection, weekly recording schedules, and English/Russian interface.
- Per-destination storage checks, optional missing-mount identity protection, retention, and generic debounced webhooks.
- Local validation, synthetic-camera recording tests, CI, and release-image workflow preparation.

Compatibility may evolve during 0.x; consult each release's upgrade notes.
