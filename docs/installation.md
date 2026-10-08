# Installation and first-camera troubleshooting

## Deployment choices

The original `docker-compose.yml` remains a bridge deployment with private
MediaMTX TCP ports and one published UDP port. Existing `.env` files and
`WEBRTC_HOST` values keep working. Docker cannot infer a LAN address from a
container interface. Set `WEBRTC_HOST` explicitly in this mode.

On Linux Docker Engine, add `-f compose.lan.yml` after `-f docker-compose.yml`
to use host networking for both existing services. MediaMTX gathers the host's
interface addresses using its native `webrtcIPsFromInterfaces` capability.
No address-discovery service, STUN dependency, browser-hostname inference or
SDP rewriting is involved. Interface candidates are possibilities, not proof
that a client can reach them. Firewalls must allow the configured UDP port.
This mode requires Compose 2.24.4+ (`!reset` overrides) and free host TCP ports
8554, 8889 and 9997. It is intended for Linux Engine, not Docker Desktop.
Host networking gives the containers access to host network services. MediaMTX
TCP listeners remain on loopback; trusted local processes can reach them.

MediaMTX supports additional IPs/DNS names in `WEBRTC_HOST` (comma separated).
For VPN, NAT or reverse proxies, explicitly advertise addresses reachable by
the viewers. A reverse proxy carries signaling, not WebRTC UDP media. Configure
MediaMTX ICE/TURN settings in `mediamtx.yml` if direct media connectivity is
impossible. You can restrict `webrtcIPsFromInterfacesList`, exclude interfaces
with `webrtcIPsFromInterfacesExcludeList`, or disable `webrtcIPsFromInterfaces`
when only explicit candidates should be advertised. No TURN service is mandatory.
See the [MediaMTX configuration reference](https://mediamtx.org/docs/references/configuration-file)
and [installation guidance](https://mediamtx.org/docs/kickoff/install).

Use the same Compose files for every lifecycle command. Apply `.env` changes
with `up -d` (a restart alone does not recreate container environment). Combine
`compose.image.yml` if desired; do not switch an existing deployment to host
networking without checking port conflicts. Authentication and HTTPS cookie
settings work the same in both modes. Set both authentication values before
sharing the application. A partial credential configuration fails startup.

## Storage and recording defaults

Compose creates the `./data` and `./recordings` bind directories in a default
root deployment. SQLite initializes automatically and no cameras are seeded.
Local recording storage creates its `default` directory automatically. For
`NVR_UID`/`NVR_GID`, pre-create writable directories with matching ownership.
Mount external storage before starting; use Storage protection for removable
or network mounts to avoid writing to the underlying disk when a mount is lost.
Do not reduce the 5 GB reserve merely to hide a storage warning.

Cameras and recording default to enabled, 7-day retention, TCP RTSP, video-only
stream-copy recording and 600-second segments. H.264 is the recommended starting
point for browser playback; successful probing does not guarantee browser codec
support or MP4 recording compatibility. Completed footage appears in Archive
after a segment closes, normally within 10 minutes or at the next hour boundary.
Container restarts remain `unless-stopped`; the application health check does
not claim every camera or storage destination is healthy.

## First camera

1. Select Add camera in the empty Overview and enter a name, RTSP URL and credentials.
2. Save, then use Check. A disabled camera can also be checked without enabling recording.
3. Keep Recording enabled and verify Writing in Overview. Open Storage if blocked.
4. Use Watch on the camera card or Overview for live video.
5. Use Archive on the camera card or player for recorded footage.

## Diagnosing failures

| Symptom | Next step |
| --- | --- |
| Invalid RTSP URL | Use `rtsp://host[:port]/path` or `rtsps://…`; verify the port and avoid URL fragments. |
| Authentication failure | Check camera credentials; separate username/password fields override URL credentials. |
| Stream not found | Verify the vendor's main-stream path. |
| Camera unavailable | Check power, address, DNS, RTSP port and access from the NVR host. |
| Timeout | Check routing, VLAN/firewall rules and camera connection limits. |
| Unreadable video | Verify the stream path and codec; try H.264. Some camera errors cannot be classified more precisely. |
| Recording reconnecting/stalled | Run Check, verify codec/MP4 compatibility and inspect Storage. A running process alone does not mean it is writing. |
| Storage blocked | Check writable permissions, mount identity and available space above the reserve. |
| Check succeeds but live fails | Check browser codec support and UDP access. In bridge mode verify `WEBRTC_HOST`; behind NAT/VPN/proxies verify reachable ICE candidates or configure TURN. |

Checks reuse the existing bounded, cached FFprobe scheduler. They classify
structured FFprobe errors locally and return fixed messages; camera error text,
passwords and URL query options are never returned or logged. An authentication
failure may be reported as unreadable media if a camera masks its reason.
Do not paste credential-bearing RTSP URLs or raw camera output into issue reports.

## Installation review

The Dockerfile already bundles the UI, FFmpeg, CA certificates and timezone
data; host runtimes and manual database setup are unnecessary. Published images
avoid a local build, but an older release cannot include unreleased UI/backend
changes. The optional LAN file reduces address configuration without changing
the established bridge deployment. Storage mount/ownership, auth, camera URLs
and external firewall rules remain operator choices. No security defaults,
published media TCP ports, retention policies or extra mandatory services were
changed to hide those choices.
