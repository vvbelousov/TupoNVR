# TupoNVR

**A deliberately simple, self-hosted NVR for RTSP cameras.**

[Русский](README_RU.md) · [Documentation](docs/README.md)

Watch IP cameras live, record their video, and find footage on a shared archive timeline. Built for homes, small CCTV installations, and homelabs: one application container and one MediaMTX container, with recording independent of your browser. [Get started](#quick-start) with Docker Compose.

[<img src="docs/images/archive-synchronized.png" alt="TupoNVR synchronized archive" height="360">](docs/images/archive-synchronized.png)

*Browser-test screenshot with synthetic footage; the fourth camera has a gap. Click to enlarge.*

Camera settings can be bootstrapped, backed up and imported with YAML on the Cameras page; imports preview changes before confirmation. See [camera configuration](docs/camera-configuration.md).

## Key features

- RTSP camera management with separate connectivity and recording status.
- Individual live view and saved, draggable, resizable Multiview layouts; optional camera substreams.
- Video-only MP4 recording without transcoding, weekly schedules, configurable retention, and storage mount identity protection.
- Multi-camera archive timeline, downloads, interval exports, and PNG frame capture.
- English/Russian interface, installation timezone, optional shared login, webhooks, and health/metrics endpoints.

## Quick Start

Requires **Linux, Docker Engine, Docker Compose 2.24.4+**, network access to RTSP cameras, and writable storage with more than the default **5 GiB** free-space reserve. Use a modern WebRTC/MP4-capable browser; H.264 is the starting point. Host Python, Node, and FFmpeg are unnecessary.

1. Download the deployment configuration (curl or a browser; no source checkout):

   ```sh
   mkdir -p TupoNVR
   cd TupoNVR
   for file in docker-compose.yml compose.lan.yml mediamtx.yml .env.example; do
     curl -fL "https://raw.githubusercontent.com/vvbelousov/TupoNVR/dev/$file" -o "$file"
   done
   cp .env.example .env
   ```

2. Edit `.env`: set `NVR_IMAGE=vvbelousov/tuponvr:0.2.1` and `NVR_BIND=0.0.0.0`, set both `AUTH_USERNAME` and `AUTH_PASSWORD`, and leave `WEBRTC_HOST` blank. Local recordings use `./recordings`; mount external storage before using it. Allow **TCP 8080 and UDP 8189** for trusted viewers; host TCP ports **8554, 8889, 9997** must be free.

3. Pull the pinned Docker Hub image and start it:

   ```sh
   docker compose -f docker-compose.yml -f compose.lan.yml pull
   docker compose -f docker-compose.yml -f compose.lan.yml up -d --no-build
   ```

4. Open **`http://<host-address>:8080`** and log in. Set your timezone in **Account → Preferences**. In **Cameras**, add a main RTSP URL and camera credentials, use **Check**, and verify **Writing** in **Overview**. Cameras and recording start enabled when storage is ready. Open **Watch** for live video; Archive needs a closed segment (normally about 10 minutes or the next hour boundary) and indexing, which can take another five minutes.

This Linux LAN option uses host networking with MediaMTX TCP listeners on loopback. **Application login does not protect direct MediaMTX access**; keep those endpoints private and trust local host processes. Use the same Compose files for subsequent commands. See [installation](docs/installation.md) for localhost/bridge deployment, and [security](docs/security-hardening.md) for HTTPS and VPN access.

## Documentation

[Documentation index](docs/README.md) — English and Russian guides.

- [Installation](docs/installation.md) · [Configuration](docs/configuration.md) · [Operating guide](docs/operations.md)
- [Storage and retention](docs/storage.md) · [Recording and archive](docs/time-and-archive.md) · [Manual cleanup](docs/recording-cleanup.md)
- [Security and authentication](docs/security-hardening.md) · [Networking and WebRTC](docs/networking.md)
- [Troubleshooting](docs/troubleshooting.md) · [Architecture](docs/architecture.md) · [API](docs/api.md)

## Project philosophy

TupoNVR follows the Unix philosophy and KISS: established tools, a focused job, and a deployment you can understand and maintain. Simplicity is intentional. Practical improvements to watching and reviewing footage fit the project; distributed enterprise surveillance infrastructure is outside its scope.

## Known limitations

- No audio recording, detection, PTZ, ONVIF discovery/control, multi-user roles, or native mobile app.
- Playback depends on browser codec support; H.265 varies. Camera Online status does not guarantee playback.
- Archive synchronization is practical, not frame-accurate. No camera-count or performance benchmark is claimed.
- Power loss can leave incomplete MP4s; no automatic repair or backup. NAS mounting/recovery is the host's responsibility.
- One shared account, no login brute-force limiter; keep deployments on a trusted LAN or VPN and use HTTPS for remote access. Direct MediaMTX endpoints bypass login.
- No verified camera-model list, native ARM deployment, or long-running real-camera/NAS soak results are documented. See [compatibility and diagnostics](docs/troubleshooting.md).

## Contributing

[Issues](https://github.com/vvbelousov/TupoNVR/issues) and [pull requests](https://github.com/vvbelousov/TupoNVR/pulls) are welcome. Discuss substantial features first; see [CONTRIBUTING.md](CONTRIBUTING.md) for development and validation, and [CHANGELOG.md](CHANGELOG.md) for release notes. Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).

## License

[Apache-2.0](LICENSE). Dependencies retain their own licenses; see [third-party notices](docs/third-party.md).
