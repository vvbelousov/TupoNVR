# Changelog

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
- The template selects `NVR_IMAGE=vvbelousov/tuponvr:0.2.0`; use it after the image is published. Source builds can set `NVR_IMAGE=tuponvr:local` and use `--build`.

See [security and migration](docs/security-hardening.md) and [installation](docs/installation.md). An older image may not support newer persistent data; rollback may require the matching pre-upgrade backup.

## 0.1.0

- Lightweight two-container deployment with RTSP cameras, MediaMTX live viewing, and FFmpeg stream-copy MP4 recording.
- Camera management, direct connectivity diagnostics, recording health, and live Multiview layouts.
- Single-camera and synchronized multi-camera archive investigation, shared timeline/controls, gap rejoining, and independent segment transitions.
- Installation timezone, DST-aware archive selection, weekly recording schedules, and English/Russian interface.
- Per-destination storage checks, optional missing-mount identity protection, retention, and generic debounced webhooks.
- Local validation, synthetic-camera recording tests, CI, and release-image workflow preparation.

Compatibility may evolve during 0.x; consult each release's upgrade notes.
