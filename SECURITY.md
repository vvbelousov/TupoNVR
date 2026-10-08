# Security

TupoNVR is intended for a trusted home or small-installation LAN. It is not designed as a publicly exposed camera gateway.

## Reporting a vulnerability

Do not post exploit details, credentials, private camera URLs, or recordings in a public issue.

Use GitHub private vulnerability reporting through this repository’s **Security → Report a vulnerability** flow. The owner must enable this channel before public release. If that option is unavailable, do not post sensitive details in public issues; ask for the private reporting channel to be enabled without disclosing the vulnerability. No personal email address is required.

Include the affected version, reproduction steps using synthetic data, impact, and a suggested mitigation if known. No response-time guarantee or extended support policy is established yet. After release, fixes target the latest published release; upgrade when a security fix is available.

## Deployment boundaries

- Configure both `AUTH_USERNAME` and `AUTH_PASSWORD` for a shared deployment. Empty values deliberately disable application authentication.
- Browser WHEP signaling goes through authenticated `/api/media` routes. Compose publishes only the application (localhost by default) and WebRTC UDP. UDP requires negotiated ICE/DTLS credentials. MediaMTX API, RTSP and WHEP HTTP are trusted service endpoints: never publish or proxy them directly or attach untrusted containers to the media network. The optional Linux LAN overlay uses host networking with these TCP listeners on loopback; local host processes are trusted. Native deployments must bind these TCP listeners to loopback or firewall them from clients.
- Use HTTPS and `COOKIE_SECURE=true` when proxying the UI. WHEP uses the same origin. Forward `/api/media`, including OPTIONS, POST, PATCH and DELETE. Preserve Host and configure trusted proxy headers so the application sees the external HTTPS scheme.
- Camera credentials are stored in SQLite in plaintext. Protect `.env`, the data directory, recordings, storage markers, and backups. Diagnostic output avoids exposing credentials, but review anything attached to an issue.
- Random server-side sessions expire after 24 hours. Logout and repeated login revoke the current session. Changing environment credentials and recreating the application invalidates all sessions; restart also requires login again. Run exactly one worker. Active WebRTC resources are deleted on logout; expired sessions are reaped every five seconds. Failed media deletions are retried, so a gateway outage can delay termination. Startup removes orphaned WebRTC sessions. There is no multi-user authorization or password-change UI.
- Storage IDs protect against missing mounts; they are neither secrets nor encryption. Mount availability, NAS permissions, and kernel I/O behavior remain host responsibilities.
- The default container runs as root for compatibility with existing bind mounts. A configured numeric non-root identity works when its directories are writable; see [container permissions](docs/configuration.md#container-permissions). No privileged mode or Docker socket is required; host networking is optional.
- No telemetry, analytics, or crash-reporting service is added. Outbound traffic is to configured cameras, MediaMTX, and optional webhooks. Dependency/image downloads are deployment/build operations.

Deployment details: [English](docs/security-hardening.md) · [Русский](docs/security-hardening_RU.md).
