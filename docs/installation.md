# Installation

[Documentation](README.md) · [Русский](installation_RU.md)

## Prerequisites

Git, Docker Engine and Compose v2; network access to the cameras; writable recording storage with free space above `MIN_FREE_SPACE_GB` (default 5 GiB). Docker bundles the UI, Python, FFmpeg/ffprobe, CA certificates, and timezone data. A modern WebRTC/MP4 browser and a compatible camera codec are required; start with H.264. There are no seeded cameras or manual database setup.

The [README Quick Start](../README.md#quick-start) builds the `dev` source on Linux Engine using host networking. That option requires **Compose 2.24.4+** and free host TCP ports 8554, 8889, 9997; it is not intended for Docker Desktop. See [networking](networking.md) for mode selection.

## Prepare a deployment

```sh
git clone --branch dev https://github.com/vvbelousov/TupoNVR.git
cd TupoNVR
cp .env.example .env
```

Edit `.env` before starting:

- LAN: set `NVR_BIND=0.0.0.0` and both `AUTH_USERNAME` and `AUTH_PASSWORD`. For localhost-only access, keep `NVR_BIND=127.0.0.1`; leaving both credentials empty disables authentication. Setting only one fails startup.
- Linux LAN overlay: leave `WEBRTC_HOST` blank for host-interface discovery. Bridge deployment: set it to the host LAN IP for remote viewers, or keep blank for localhost.
- Keep `DATA_DIR=./data` and `DEFAULT_RECORDING_PATH=./recordings`, or select host directories. Mount external storage first and verify its visibility inside the container.
- For the default root identity, Compose creates bind directories and the application creates local destination `default`. For non-root, pre-create writable directories with ownership matching `NVR_UID`/`NVR_GID`; see [configuration](configuration.md#container-permissions).

## Build the checkout

Linux LAN:

```sh
NVR_IMAGE=tuponvr:local docker compose -f docker-compose.yml -f compose.lan.yml up -d --build
```

Bridge / localhost:

```sh
NVR_IMAGE=tuponvr:local docker compose up -d --build
```

The inline value overrides the published image reference in `.env`. For subsequent source-build commands, set `NVR_IMAGE=tuponvr:local` in `.env` or repeat the prefix. Unreleased checkout changes require a source build.

## Image deployment

Use deployment files from the same reviewed release as the selected image: `docker-compose.yml`, `mediamtx.yml`, `.env.example`, and any overlays you use. The example sets `NVR_IMAGE=vvbelousov/tuponvr:0.2.0`; check [Docker Hub tags](https://hub.docker.com/r/vvbelousov/tuponvr/tags) for availability and select a fixed version or digest. If unavailable, build from source. A `latest` tag, when published, can change across updates.

Linux LAN:

```sh
docker compose -f docker-compose.yml -f compose.lan.yml up -d --no-build --pull always
```

Bridge:

```sh
docker compose up -d --no-build --pull always
```

The optional `compose.image.yml` enforces a nonempty `NVR_IMAGE` and `pull_policy: always`:

```sh
docker compose -f docker-compose.yml -f compose.image.yml up -d --no-build
```

For LAN add `-f compose.lan.yml` after the base file and before the image overlay. Images require no local Node/Python installation or build.

## First run

Open `http://<host-address>:8080` (`http://localhost:8080` for localhost-only use), log in if configured, and select your installation timezone in **Account → Preferences** before using schedules or local archive times.

1. Add a camera in **Cameras** or the empty **Overview**: name, main RTSP URL and credentials. Separate credential fields override embedded URL credentials.
2. Use **Check**; disabled cameras can also be checked. Successful probing does not guarantee browser codec or MP4 compatibility.
3. Cameras and recording default to enabled, TCP RTSP, video-only copying, 600-second segments and seven-day retention. Verify **Writing** in Overview. If blocked, inspect Storage; do not reduce the reserve just to hide a warning. Enable [identity protection](storage.md#per-destination-storage-protection) before recording to external mounts.
4. Open **Watch**, then **Archive**. A segment must close (normally about ten minutes or the next hour boundary) and pass indexing (periodic scan every five minutes or after clean recorder stop).

## Lifecycle

Always use the same Compose files for a deployment. `.env` changes require `up -d` to recreate the environment; `restart` alone is insufficient. Source builds need `--build`; selected published images use `--no-build --pull always`.

Linux LAN examples:

```sh
docker compose -f docker-compose.yml -f compose.lan.yml ps
docker compose -f docker-compose.yml -f compose.lan.yml logs --tail=100 nvr-app
docker compose -f docker-compose.yml -f compose.lan.yml stop nvr-app
docker compose -f docker-compose.yml -f compose.lan.yml down
```

Containers use `restart: unless-stopped`. `/health` is process liveness, not camera/storage health; `/ready` checks MediaMTX connectivity. See [backup and updates](storage.md#updating-backup-and-recovery), [security](security-hardening.md), and [troubleshooting](troubleshooting.md).
