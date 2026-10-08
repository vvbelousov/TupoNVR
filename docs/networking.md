# Networking and WebRTC

[Documentation](README.md) · [Русский](networking_RU.md)

## Choose a network mode

| Mode | Application TCP | MediaMTX TCP | WebRTC UDP | Address candidates |
|---|---|---|---|---|
| Base `docker-compose.yml` (bridge) | Host `NVR_BIND:NVR_PORT` → container 8000 | 8554 / 8889 / 9997 on private `media` network; unpublished | Host `WEBRTC_UDP_PORT` (8189) | Blank `WEBRTC_HOST` falls back to 127.0.0.1; set host LAN IP for other devices |
| Base + `compose.lan.yml` (Linux host network) | Host `NVR_BIND:NVR_PORT` | Host loopback 8554 / 8889 / 9997 | Host `WEBRTC_UDP_PORT` (8189) | Blank `WEBRTC_HOST` uses native host-interface discovery |

Both modes default to application localhost port 8080. LAN access requires `NVR_BIND=0.0.0.0`, both authentication values, and firewall access to application TCP and media UDP for trusted viewers. LAN host mode needs Linux Engine, Compose 2.24.4+, and free media TCP ports. Both containers can access host network services; local processes can reach unauthenticated MediaMTX loopback endpoints. Bridge mode trusts members of the `media` network. Do not attach untrusted containers or publish media TCP ports.

Use the same `-f` arguments for every lifecycle command; see [installation](installation.md). Check port conflicts before migrating between modes.

## Signaling and media

The browser negotiates WHEP through application `/api/media`; the application proxies signaling to private MediaMTX. Negotiated WebRTC UDP flows directly between browser and MediaMTX. `WEBRTC_PORT` is a legacy API value, not a published signaling port. HLS, RTMP, SRT and MoQ are disabled in the supplied configuration.

Native discovery sees container interfaces in bridge mode and host interfaces in LAN mode. No discovery service, browser-hostname inference, SDP rewriting, or mandatory STUN service is used. An advertised ICE candidate does not prove that a viewer can reach it; verify routing, VLANs, and UDP firewall rules.

## VPN, NAT, and HTTPS

Prefer LAN-only access or a VPN over exposing the NVR publicly. Advertise addresses reachable by VPN viewers through `WEBRTC_HOST` (comma-separated IPs or DNS names) when automatic candidates are unsuitable. A VPN must route both application TCP and media UDP.

An HTTPS reverse proxy carries the UI, API, archive, and WHEP signaling; it does not carry WebRTC UDP. Preserve Host, forward `/api/media` OPTIONS/POST/PATCH/DELETE and SDP content types, set `COOKIE_SECURE=true`, and configure `FORWARDED_ALLOW_IPS` to actual trusted proxy IPs. Keep direct MediaMTX HTTP/API/RTSP private; see [security](security-hardening.md).

If direct media connectivity is impossible, configure MediaMTX ICE/TURN settings in `mediamtx.yml`; a TURN service is not supplied or required for an ordinary LAN. MediaMTX also supports `webrtcIPsFromInterfacesList`, `webrtcIPsFromInterfacesExcludeList`, and disabling `webrtcIPsFromInterfaces` for explicit candidates only. Consult the [MediaMTX configuration reference](https://mediamtx.org/docs/references/configuration-file) for your deployed version. See [troubleshooting](troubleshooting.md) if Check succeeds but live video fails.
