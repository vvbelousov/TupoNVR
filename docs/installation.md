# Installation

[Documentation](README.md) · [Русский](installation_RU.md)

## Prerequisites

Docker Engine and Compose v2; network access to the cameras; writable recording storage with free space above `MIN_FREE_SPACE_GB` (default 5 GiB). Docker bundles the UI, Python, FFmpeg/ffprobe, CA certificates, and timezone data. A modern WebRTC/MP4 browser and a compatible camera codec are required; start with H.264. There are no seeded cameras or manual database setup.

The standard installation uses the prebuilt `vvbelousov/tuponvr:0.2.1` image after the 0.2.1 release is published ([Docker Hub](https://hub.docker.com/r/vvbelousov/tuponvr/tags?name=0.2.1)); this source tree prepares that release. Git, Python, Node.js and compilation are unnecessary. Linux LAN needs **Compose 2.24.4+** and free host TCP ports 8554, 8889, 9997; use bridge mode for Docker Desktop. See [networking](networking.md). Contributor workflows are in [development](development.md).

## Prepare a deployment

```sh
mkdir -p TupoNVR
cd TupoNVR
for file in docker-compose.yml compose.lan.yml mediamtx.yml .env.example; do
  curl -fL "https://raw.githubusercontent.com/vvbelousov/TupoNVR/dev/$file" -o "$file"
done
cp .env.example .env
```

Edit `.env` before starting:

- LAN: set `NVR_BIND=0.0.0.0` and both `AUTH_USERNAME` and `AUTH_PASSWORD`. For localhost-only access, keep `NVR_BIND=127.0.0.1`; leaving both credentials empty disables authentication. Setting only one fails startup.
- Linux LAN overlay: leave `WEBRTC_HOST` blank for host-interface discovery. Bridge deployment: set it to the host LAN IP for remote viewers, or keep blank for localhost.
- Keep `DATA_DIR=./data` and `DEFAULT_RECORDING_PATH=./recordings`, or select host directories. Mount external storage first and verify its visibility inside the container.
- For the default root identity, Compose creates bind directories and the application creates local destination `default`. For non-root, pre-create writable directories with ownership matching `NVR_UID`/`NVR_GID`; see [configuration](configuration.md#container-permissions).

## Start the prebuilt image

Keep `NVR_IMAGE=vvbelousov/tuponvr:0.2.1` in `.env`. The base Compose file has no build configuration. Avoid floating tags. Configuration downloads come from `dev`; the image is pinned independently, so unpublished UI changes need a new release. Retain the downloaded files for subsequent commands.

Linux LAN:

```sh
docker compose -f docker-compose.yml -f compose.lan.yml pull
docker compose -f docker-compose.yml -f compose.lan.yml up -d --no-build
```

Bridge / localhost:

```sh
docker compose pull
docker compose up -d --no-build
```

The optional `compose.image.yml` requires a nonempty `NVR_IMAGE` and always pulls the image. Download it from the same URL and add `-f compose.image.yml` after other files. If the selected image is unavailable, stop and check Docker Hub; do not substitute a local build in the installation workflow.

## First run

Open `http://<host-address>:8080` (`http://localhost:8080` for localhost-only use), log in if configured, and select your installation timezone in **Account → Preferences** before using schedules or local archive times.

1. Add a camera in **Cameras** or the empty **Overview**: name, main RTSP URL and credentials. Separate credential fields override embedded URL credentials.
2. Use **Check**; disabled cameras can also be checked. Successful probing does not guarantee browser codec or MP4 compatibility.
3. Cameras and recording default to enabled, TCP RTSP, video-only copying, 600-second segments and seven-day retention. Verify **Writing** in Overview. If blocked, inspect Storage; do not reduce the reserve just to hide a warning. Enable [identity protection](storage.md#per-destination-storage-protection) before recording to external mounts.
4. Open **Watch**, then **Archive**. A segment must close (normally about ten minutes or the next hour boundary) and pass indexing (periodic scan every five minutes or after clean recorder stop).

## Lifecycle

Always use the same Compose files for a deployment. `.env` changes require `up -d` to recreate the environment; `restart` alone is insufficient. Pull the selected published version, then use `up -d --no-build`.

Linux LAN examples:

```sh
docker compose -f docker-compose.yml -f compose.lan.yml ps
docker compose -f docker-compose.yml -f compose.lan.yml logs --tail=100 nvr-app
docker compose -f docker-compose.yml -f compose.lan.yml stop nvr-app
docker compose -f docker-compose.yml -f compose.lan.yml down
```

Containers use `restart: unless-stopped`. `/health` is process liveness, not camera/storage health; `/ready` checks MediaMTX connectivity. See [backup and updates](storage.md#updating-backup-and-recovery), [security](security-hardening.md), and [troubleshooting](troubleshooting.md).
