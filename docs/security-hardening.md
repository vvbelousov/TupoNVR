# Authentication and media security

## Audit findings and fixes

The previous deterministic signed cookie remained usable after logout, and two logins in the same second produced the same cookie. Random, server-held sessions now provide individual revocation, absolute 24-hour expiry, and credential binding. Copied expired/revoked cookies fail with a plain 401, without browser Basic Auth challenges. Cookies remain HttpOnly, SameSite=Strict and optionally Secure. Write requests check Origin (including scheme) and reject cross-site browser fetches; non-browser clients without Origin continue to work.

The previous published TCP 8889 exposed unauthenticated WHEP playback independently of FastAPI. Browser OPTIONS/POST/PATCH/DELETE now pass through `/api/media`, which checks application sessions, restricts paths to camera playback, and binds each WHEP resource to its creating session. It forwards SDP, ICE links and ETags, rewrites Location, bounds request sizes, and removes Basic Auth challenges. Logout requests deletion of active resources; expiry cleanup runs every five seconds and retries failed deletions. Application startup removes orphaned WebRTC sessions.

Camera ingestion is unchanged: MediaMTX pulls configured RTSP camera URLs with their existing camera credentials. FFmpeg reads the relay privately. The application manages the private MediaMTX API. HLS, RTMP, SRT and MoQ stay disabled; publishing streams through the browser proxy is unsupported. No external session service or new dependency is needed.

## Deployment and migration

1. Rebuild/update the application and recreate **both** Compose services using the updated configuration. Merely updating the application image leaves the old exposed media port vulnerable. Existing camera, storage and recording data need no migration.
2. For a shared LAN set both `AUTH_USERNAME` and `AUTH_PASSWORD`, `NVR_BIND=0.0.0.0`, and `WEBRTC_HOST` to the host LAN IP. The new default bind is `127.0.0.1`; installations expecting LAN access must explicitly change it. Empty authentication values still support a deliberately trusted LAN and emit a startup warning.
3. Allow the application TCP port (default 8080) and WebRTC UDP (default 8189). TCP 8889 is no longer published; `WEBRTC_PORT` is retained in legacy API configuration for compatibility but no longer controls browser signaling. Do not publish TCP 8554/8889/9997, attach untrusted containers to the media network, or use host networking. MediaMTX's internal listeners remain unauthenticated, so their network isolation is part of the secured deployment.
4. Native installations must keep MediaMTX TCP endpoints private with loopback binding or a firewall. Configure `MEDIAMTX_WEBRTC=http://127.0.0.1:8889` alongside the existing API/RTSP host settings. Do not reverse-proxy direct MediaMTX endpoints for clients.
5. An HTTPS reverse proxy needs only the application origin and must forward `/api/media` methods and SDP content types. Preserve the external Host, pass trusted scheme headers, and set `COOKIE_SECURE=true`. Set `FORWARDED_ALLOW_IPS` to your actual proxy IP(s); do not trust arbitrary client-supplied forwarded headers or use `*` on a directly reachable application. UDP still requires browser-to-host connectivity.
6. Existing cookies are intentionally invalidated on upgrade/restart. To change a password, edit environment credentials and run `docker compose up -d --force-recreate nvr-app`. There is no password-change UI. Run the existing single-worker command; sessions live in process memory.

## Remaining boundaries

MediaMTX service ports are not safe to expose directly. A compromised host or container on the private service network can access streams/API. This is a deployment boundary, not a claim that FastAPI protects all MediaMTX listeners. UDP without negotiated signaling does not grant playback.

HTTP LAN traffic can reveal credentials and cookies; use HTTPS outside a trusted LAN. Credentials in environment/SQLite and recordings are not encrypted. Authentication is one shared account, with no login brute-force limiter; restrict network access and choose a strong password. Session creation and media resources have memory limits. Failed gateway deletion can delay termination of an already negotiated stream; revoked cookies cannot initiate further application requests. TLS/DTLS cannot protect a compromised host or authorized viewer copying footage.
