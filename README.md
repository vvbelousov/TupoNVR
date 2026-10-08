# TupoNVR

**A deliberately simple, self-hosted NVR for RTSP cameras.**

[Русская версия](README_RU.md) · [Operating guide (English)](docs/operations.md)

[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
[![Docker Compose](https://img.shields.io/badge/deployment-Docker_Compose-blue)](docker-compose.yml)

Add cameras. Watch live video. Record footage. Browse the archive. Nothing unnecessary.

TupoNVR keeps IP camera recording manageable for a home, small CCTV installation, or homelab. Simplicity is a design decision, not a temporary limitation.

![TupoNVR archive: three cameras playing synthetic footage and a fourth with a recording gap](docs/images/archive-synchronized.png)

*Actual application screenshot from browser tests, using synthetic video. See [Screenshots and demo](#screenshots-and-demo).*

## Why TupoNVR?

Sometimes you just need an NVR that records your cameras and lets you find what happened. TupoNVR follows the Unix philosophy and KISS principle: a focused application, established tools, and a small deployment you can understand and maintain yourself.

One application container and one MediaMTX container handle recording, live view, and archive browsing. Recording continues when every browser tab is closed. Recording uses stream copying; optional exact interval exports encode video through the same FFmpeg installation.

## Features

- Add, edit, enable, disable, and check RTSP cameras; see connectivity and recording health separately.
- Live view for individual cameras and saved Multiview layouts with draggable, resizable tiles and optional camera substreams.
- Original video recording into MP4 segments, configurable retention, and downloads.
- Browse one or several cameras on a shared archive timeline with play/pause, seek, speed controls, gaps, and independent segment transitions.
- Export a camera interval as one MP4, mark boundaries at the timeline cursor, and save live/archive frames as PNG. Open recent recordings directly from live view and zoom to 5/15/30/60 minutes.
- Weekly recording schedules and an installation-wide timezone, including DST-aware archive selection.
- Per-destination storage checks and optional identity protection for missing external mounts.
- English/Russian interface, optional UI/API login, generic webhooks, and health/metrics endpoints.

## Quick Start

Use a Linux host with **Docker Engine and Docker Compose v2**, network access to the cameras, and writable storage sized for their bitrate. You do not need host Python, Node, or FFmpeg. Viewing requires a modern browser with WebRTC and compatible MP4 playback; use H.264 as the starting point.

1. Get the deployment files and create the configuration:

   ```sh
   git clone https://github.com/vvbelousov/TupoNVR.git
   cd TupoNVR
   cp .env.example .env
   ```

2. For LAN access, set `NVR_BIND=0.0.0.0` and both `AUTH_USERNAME` and `AUTH_PASSWORD` in `.env`. Keep `WEBRTC_HOST` blank for the Linux LAN deployment below. Keep `./recordings` for local storage, or set `DEFAULT_RECORDING_PATH` to a host directory. Mount external storage first. Localhost-only use can keep the bind and authentication defaults.

3. Set the image in `.env`, choosing `0.1.0`, `latest`, or another published tag from [Docker Hub](https://hub.docker.com/r/vvbelousov/tuponvr/tags). Pin a version for predictable updates; `latest` follows the current stable release.

   ```dotenv
   NVR_IMAGE=vvbelousov/tuponvr:0.1.0
   ```

   Pull and start the containers:

   ```sh
   docker compose -f docker-compose.yml -f compose.lan.yml up -d --no-build --pull always
   ```

   This Linux Docker Engine option requires Compose **2.24.4+**. It uses MediaMTX’s native host-interface discovery, so a typical LAN needs no `WEBRTC_HOST`. For the unreleased changes in this checkout, build them with `NVR_IMAGE=tuponvr:local docker compose -f docker-compose.yml -f compose.lan.yml up -d --build` instead of pulling an older release.

4. Open `http://<host-address>:8080`, go to **Cameras**, and add the camera's main RTSP URL and credentials. Cameras and recording are enabled by default; recording starts when storage is ready. Use **Check**, then verify recording progress in **Overview**. Use **Watch** to open live video and **Archive** to find recorded footage after the first recording segment closes (normally 10 minutes, or at the hour boundary). Configure your timezone in **Account → Preferences** before using schedules or local archive times.

The application binds to localhost by default. The original bridge deployment remains available with `docker compose up -d --no-build --pull always`; it requires `WEBRTC_HOST` to be the host LAN address for remote viewers. Linux LAN mode binds MediaMTX API, RTSP and signaling to loopback; these host ports (9997, 8554, 8889) must be free. Both containers share the host network, so local processes are part of the trust boundary. Allow **TCP 8080 and UDP 8189** through the firewall. Login protects API, archive and browser live viewing. Keep MediaMTX TCP ports private. See [SECURITY.md](SECURITY.md) and [migration notes](docs/security-hardening.md).

The command pulls the selected image without building locally. See [image deployment and source builds](docs/operations.md#published-image-deployment) for alternatives.

See [installation and troubleshooting](docs/installation.md) for storage initialization, network alternatives and actionable camera diagnostics.

## Screenshots and demo

The [archive screenshot above](docs/images/archive-synchronized.png) shows four selected cameras, a shared playback clock, and independent players; the fourth camera has no footage at the selected time. It demonstrates the interface, not real-camera network compatibility. No hosted demo or additional screenshots are included. You can try the application with synthetic cameras using the [contribution validation instructions](CONTRIBUTING.md#validation).

## Architecture

```mermaid
flowchart LR
    Camera["RTSP IP camera"] -->|RTSP| MTX["MediaMTX"]
    MTX -->|WebRTC / WHEP live video| Browser["Browser / React"]
    MTX -->|Internal RTSP| FFmpeg["FFmpeg recorder"]
    FFmpeg -->|Video stream copy| MP4["MP4 recordings"]
    Browser <-->|HTTP API / archive playback| API["FastAPI"]
    API <-->|Metadata / settings| DB["SQLite / settings.json"]
    API -->|Manage stream paths| MTX
    API -->|Supervise| FFmpeg
    MP4 -->|Files / downloads| API
```

FastAPI, React assets, SQLite, and one FFmpeg recorder per active camera belong to the **application container**; MediaMTX is the **second service**. MediaMTX shares a main RTSP upstream between recording and viewers. Optional substreams use separate on-demand paths. Direct connectivity checks briefly open an additional RTSP session to the camera.

Run exactly one application worker to avoid duplicate recorders. See [component ownership](docs/architecture.md) and [recording internals](docs/operations.md#video-architecture).

## Configuration and storage

Copy [.env.example](.env.example) to `.env`; it contains all Compose settings. When using Linux LAN mode, include `-f docker-compose.yml -f compose.lan.yml` in every Compose command, including updates and shutdown. Apply environment changes with `docker compose up -d --no-build --pull always` when using a published image.

| Variable | Default in `.env.example` | Purpose |
|---|---|---|
| `NVR_PORT` | `8080` | Host application TCP port |
| `NVR_BIND` | `127.0.0.1` | Application bind address; set `0.0.0.0` for LAN access |
| `WEBRTC_UDP_PORT` | `8189` | WebRTC media UDP port |
| `WEBRTC_HOST` | blank | Linux LAN mode: native host-interface detection. Bridge mode: falls back to `127.0.0.1`. Explicit comma-separated IPs/DNS names add ICE candidates in both modes. |
| `DATA_DIR` | `./data` | Host directory mounted at `/data`: SQLite and timezone settings |
| `DEFAULT_RECORDING_PATH` | `./recordings` | Host directory mounted at `/recordings` |
| `SEGMENT_SECONDS` | `600` | Target segment length in seconds; boundaries depend on keyframes |
| `MIN_FREE_SPACE_GB` | `5` | Free-space reserve (GiB) on recording filesystems |
| `AUTH_USERNAME`, `AUTH_PASSWORD` | empty | Optional shared login; set both or neither |
| `COOKIE_SECURE` | `false` | Secure session cookie for HTTPS |
| `DEFAULT_LANGUAGE` | `en` | Initial interface language: `en` or `ru` |
| `APP_TIMEZONE` | `UTC` | Initial installation timezone; a UI-saved value takes precedence |
| `LOG_LEVEL` | `INFO` | Backend logging level |
| `NVR_UID`, `NVR_GID` | `0`, `0` | Application container identity; non-root needs writable host directories |
| `NVR_IMAGE` | `vvbelousov/tuponvr:0.1.0` | Published application image reference; blank uses `tuponvr:local` |
| `WEBHOOK_URL`, `WEBHOOK_TOKEN` | empty | Optional HTTP(S) endpoint and Bearer token |
| `WEBHOOK_DEBOUNCE_SECONDS` | `60` | Continuous failure duration before notification |
| `WEBHOOK_COOLDOWN_SECONDS` | `600` | Minimum interval between new incident notifications per subject |

Recordings contain **video only**, copied into roughly ten-minute MP4 segments. Filenames and metadata remain UTC; the interface and schedules use the installation timezone. Archive shows finalized segments after indexing, normally on a five-minute scan, and also after a clean recorder stop. **Export interval** joins available footage and reports omitted gaps. Missing or damaged files fail explicitly. Temporary exports expire after one hour or are removed after download. See the [retrieval workflow and API limits](docs/time-and-archive.md#interval-exports-and-frame-retrieval).

Storage settings also provide [manual recording cleanup](docs/recording-cleanup.md): preview and delete recordings by camera, period, or individual selection. Clearing everything requires typing `DELETE`; active recording directories are excluded.

Each camera records into a destination subdirectory such as `default`. Retention defaults to **7 days** and accepts 1–3650 days; blank means no age limit. Deleted-camera footage uses a seven-day age limit from the recording's start, not the deletion date. Under low space, cleanup removes the oldest eligible indexed files, protecting the current hour of active cameras. The free-space reserve cannot always be guaranteed, and unlimited retention still permits low-space deletion.

Mount NFS/SMB storage on the host and verify write access and visibility inside the container. Enable destination identity protection before recording to an external mount. Unavailable storage pauses its cameras' recording while other destinations and live view continue. See [storage protection](docs/operations.md#per-destination-storage-protection).

Preserve and back up the complete data directory, recordings, `.env`, and storage IDs before updates. Camera credentials are stored in plaintext in SQLite: protect data and backups. Read [update and recovery instructions](docs/operations.md#updating-backup-and-recovery), [container permissions](docs/operations.md#container-permissions), and [HTTPS/proxy configuration](docs/operations.md#configuration).

## Project philosophy

Simplicity means predictable behavior, visible failures, and a system you can maintain. TupoNVR uses established components for streaming, recording, and storage, keeps dependencies and services limited, and aims to do one job well: make camera footage available to watch and review. Features should improve that workflow without requiring unnecessary infrastructure.

## Non-goals

TupoNVR is not intended to grow into an enterprise video management system (VMS) with distributed infrastructure, complex organizational access policies, or a large surveillance automation platform. Practical improvements to recording, live view, archive browsing, and reliability fit the project; complexity for its own sake does not.

## Known limitations

- No audio recording, detection, PTZ, ONVIF discovery/control, multi-user management, or native mobile application is implemented.
- Live/archive playback uses original footage; browser codec support determines playback. Exact interval exports encode H.264, while original-quality exports copy streams with keyframe-aligned cuts. H.265 support varies; a camera being Online does not guarantee browser playback.
- Archive synchronization is practical rather than frame-accurate. Timestamp precision, camera latency, bandwidth, and browser decoder capacity limit accuracy and camera count; no capacity benchmark is claimed.
- Sudden power loss can leave incomplete MP4 files. There is no automatic repair or backup; files missing the MP4 `moov` atom can be deleted by the indexer after 24 hours.
- MediaMTX TCP endpoints must stay private. Login protects browser signaling through the application; use HTTPS and secure cookies outside a trusted LAN.
- NAS mounting and recovery are host responsibilities; storage checks cannot fix hung kernel I/O or guarantee the current segment survives a mount loss.
- Native ARM execution and long-running real-camera/NAS soak testing remain unverified in the documented release preparation. ARM CI is configured; verify target hardware before advertising support.

See the [operating guide](docs/operations.md) for diagnostics and the [time/archive model](docs/time-and-archive.md) for playback/API limits.

## Camera compatibility

The repository includes synthetic H.264 RTSP integration tests, not a verified brand/model compatibility list. A usable RTSP source is required; automatic ONVIF discovery is unavailable. Cameras with strict connection limits may reject the additional diagnostic probe session.

Please submit compatibility reports through [Issues](https://github.com/vvbelousov/TupoNVR/issues): camera model, firmware, codec, resolution, main/substream behavior, browser, and results for live view and recording/archive playback. Remove credentials, private URLs, and footage from reports.

## Contributing and feedback

[Issues](https://github.com/vvbelousov/TupoNVR/issues) and [pull requests](https://github.com/vvbelousov/TupoNVR/pulls) are welcome: bug reports, compatibility testing, focused fixes, translations, and documentation all help. Discuss substantial features first so the deployment stays simple.

See [CONTRIBUTING.md](CONTRIBUTING.md) for development and `scripts/validate.sh`, the [operating guide](docs/operations.md#development-and-validation) for test coverage, [CHANGELOG.md](CHANGELOG.md) for release notes, and the [maintainer guide](docs/releasing.md) for publication preparation. Report vulnerabilities through the private channel described in [SECURITY.md](SECURITY.md).

## License

TupoNVR uses [Apache-2.0](LICENSE). Dependencies keep their own licenses; see [third-party notices](docs/third-party.md) and the vendored MediaMTX reader's [MIT license](frontend/public/MEDIAMTX-LICENSE.txt).
