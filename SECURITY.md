# Security

TupoNVR is intended for a trusted home or small-installation LAN. It is not designed as a publicly exposed camera gateway.

## Reporting a vulnerability

Do not post exploit details, credentials, private camera URLs, or recordings in a public issue.

Use GitHub private vulnerability reporting through this repository’s **Security → Report a vulnerability** flow. The owner must enable this channel before public release. If that option is unavailable, do not post sensitive details in public issues; ask for the private reporting channel to be enabled without disclosing the vulnerability. No personal email address is required.

Include the affected version, reproduction steps using synthetic data, impact, and a suggested mitigation if known. No response-time guarantee or extended support policy is established yet. After release, fixes target the latest published release; upgrade when a security fix is available.

## Deployment boundaries

- Configure both `AUTH_USERNAME` and `AUTH_PASSWORD` for a shared deployment. Empty values deliberately disable application authentication.
- UI/API authentication does **not** protect MediaMTX's direct WebRTC signaling/media ports. Limit LAN access using firewall rules or a separately secured proxy. Do not expose internal MediaMTX API/RTSP ports on the public host.
- Use HTTPS and `COOKIE_SECURE=true` when proxying the UI. WHEP needs its own HTTPS endpoint to avoid mixed-content blocking.
- Camera credentials are stored in SQLite in plaintext. Protect `.env`, the data directory, recordings, storage markers, and backups. Diagnostic output avoids exposing credentials, but review anything attached to an issue.
- Sessions are signed, expire after 24 hours, and share the configured installation credentials. There is no per-session revocation or multi-user authorization. Changing credentials invalidates existing signatures after restarting the application.
- Storage IDs protect against missing mounts; they are neither secrets nor encryption. Mount availability, NAS permissions, and kernel I/O behavior remain host responsibilities.
- The default container runs as root for compatibility with existing bind mounts. A configured numeric non-root identity works when its directories are writable; see the README. No privileged mode, host networking, or Docker socket is required by the application.
- No telemetry, analytics, or crash-reporting service is added. Outbound traffic is to configured cameras, MediaMTX, and optional webhooks. Dependency/image downloads are deployment/build operations.
