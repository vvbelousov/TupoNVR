# Configuration

Copy [`.env.example`](../.env.example) to `.env`; these are Compose settings. Apply changes by recreating containers with the same files and image choice; see [installation](installation.md#lifecycle).

[Documentation](README.md) · [Русский](configuration_RU.md)

| Variable | Default in `.env.example` | Purpose |
|---|---|---|
| `NVR_PORT` | `8080` | Application port on the host |
| `NVR_BIND` | `127.0.0.1` | Application bind address; use `0.0.0.0` for authenticated LAN access |
| `WEBRTC_UDP_PORT` | `8189` | WebRTC media UDP port |
| `WEBRTC_HOST` | blank | LAN host-network mode discovers interfaces; bridge mode defaults to localhost. Explicit IPs/DNS names remain supported. |
| `DATA_DIR` | `./data` | Host directory for SQLite and persisted settings |
| `DEFAULT_RECORDING_PATH` | `./recordings` | Host recording root |
| `SEGMENT_SECONDS` | `600` | Target segment duration in seconds |
| `MIN_FREE_SPACE_GB` | `5` | Free-space reserve in GiB |
| `AUTH_USERNAME`, `AUTH_PASSWORD` | empty | Optional UI/API login; set both |
| `COOKIE_SECURE` | `false` | Secure session cookie; enable for HTTPS access |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Trusted reverse proxy IPs for Uvicorn scheme headers |
| `LOG_LEVEL` | `INFO` | Logging level |
| `DEFAULT_LANGUAGE` | `en` | Initial interface language: `en` or `ru` |
| `APP_TIMEZONE` | `UTC` | Initial installation timezone; a UI-saved preference takes precedence |
| `NVR_IMAGE` | `vvbelousov/tuponvr:0.2.0` | Published application image/version; blank uses the local source-build image |
| `NVR_UID`, `NVR_GID` | `0`, `0` | Optional numeric container identity; non-root requires writable host directories |
| `WEBHOOK_URL` | empty | Generic HTTP(S) notification endpoint; empty disables notifications |
| `WEBHOOK_TOKEN` | empty | Optional Bearer token |
| `WEBHOOK_DEBOUNCE_SECONDS` | `60` | Continuous failure duration before the first event |
| `WEBHOOK_COOLDOWN_SECONDS` | `600` | Minimum interval between new incident events for the same subject |

Browser requests that change state validate Origin. A reverse proxy must preserve the original Host. For HTTPS, set `COOKIE_SECURE=true` and `FORWARDED_ALLOW_IPS` to the actual trusted proxy IP(s). Do not use `*` when untrusted clients can reach the application directly. See [networking](networking.md).

The application runs one Uvicorn process (`--workers 1`); a second worker could start duplicate FFmpeg recorders. The database contains camera passwords in plaintext: protect `DATA_DIR` and backups. SQLite files are created with permissions `0600`. Structured backend events omit private URLs and passwords, and Uvicorn access logging is disabled.

Use authentication for shared deployments and HTTPS with secure cookies outside a trusted LAN. Browser signaling uses `/api/media` on the application origin. Keep MediaMTX TCP 8889, 8554 and 9997 private; Compose does not publish them. See [security and migration](security-hardening.md).

## Container permissions

Root remains the default for compatibility with existing bind mounts; it is not required by FastAPI, FFmpeg, or MediaMTX API calls. To run the application as a non-root user, create the data/recording directories first with ownership matching `NVR_UID`/`NVR_GID`, then set those numeric IDs in `.env`. Existing root-owned databases, settings, and markers may need an intentional ownership change before switching. Do not loosen permissions globally or change NAS ownership blindly.

The default deployment uses a bridge network, drops `NET_RAW`, and prevents new privileges. The optional Linux LAN overlay uses the host network for native WebRTC interface discovery. Privileged mode and a Docker socket are unnecessary. Writable data/recording mounts and temporary-file space remain necessary. The image healthcheck checks `/health`; `/ready` separately verifies MediaMTX connectivity.
