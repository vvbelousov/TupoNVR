# Troubleshooting

[Documentation](README.md) · [Русский](troubleshooting_RU.md)

## Diagnostics

For bridge; in LAN mode add `-f docker-compose.yml -f compose.lan.yml` to every Compose command:

```sh
docker compose ps
docker compose logs --tail=100 nvr-app
docker compose logs --tail=100 mediamtx
curl http://localhost:8080/health
curl http://localhost:8080/ready
```

- **Online but no video:** check the camera codec. H.264 has the widest browser support; H.265 support varies. Check `WEBRTC_HOST`, UDP 8189, the application TCP port, and the browser console.
- **No recording:** check mounts, directory permissions, free space, and whether MediaMTX can access the main RTSP path. Status reports FFmpeg errors without exposing private URLs.
- **Empty archive:** the segment currently being written is not indexed. Wait for completion and the next scan (up to five minutes), or stop recording cleanly. MP4 files that fail indexing checks are excluded from Archive and left on disk for inspection, including files missing the MP4 `moov` atom. The indexer does not repair or automatically remove them.
- **Changed WebRTC UDP port:** recreate Compose services. HTTP signaling uses the application origin; `WEBRTC_PORT` is obsolete.
- **Unstable RTSP:** MediaMTX reconnects and FFmpeg restarts with backoff. There is one FFmpeg process per recording camera.


## Recording health and camera checks

Overview and Cameras display separate connectivity and recording indicators in both languages. **Online** is green and **Offline** is red. Intentional pauses, schedule pauses, and disabled cameras are neutral. STARTING/RECONNECTING are warnings; STALLED/STORAGE_UNAVAILABLE are errors. Old recorder errors are not shown as active incidents after recording stops. Paused cameras show **No active recording** instead of stale progress.

`GET /api/cameras/{id}/status` returns connectivity fields: `state`, `connectivity_state` (ONLINE/OFFLINE/UNKNOWN/DISABLED), `online` (true/false/null), `connectivity_checked_at`, and `connectivity_last_success`. UNKNOWN means an initial or stale check; DISABLED means the entire camera is disabled. Both are neutral. Recording fields include `recording_enabled`, `recording_expected` (including the schedule), `recording_health`, `recorder_running`, `last_progress_at`, `progress_age_seconds`, and `latest_segment`.

The `camera_online` metric is 1/0, or NaN for unknown/disabled cameras. A live FFmpeg process without advancing frame/out_time_us is not WRITING: after 30 seconds it is STALLED. Health and probe caches live in memory, not SQLite.

One backend worker probes **the configured main RTSP source directly** with ffprobe, independently of recording, viewers, and MediaMTX. Probes read video metadata with a codec and positive dimensions, without transcoding, with bounded probesize/analyzeduration and a ten-second total timeout. At most two probes run concurrently. The normal interval is 60 seconds after completion; the first failure retries after 15 seconds, and two consecutive failures mark Offline. Success immediately restores Online. Until failure is confirmed, the previous result remains; results older than 180 seconds become UNKNOWN.

The worker checks its queue every five seconds; delays also depend on camera count and response time. Fully disabled cameras are not probed periodically. Source or credential changes invalidate the cache.

The **Check** button uses the same probe and can check disabled cameras. Concurrent requests share a probe, and results are reused for ten seconds. A disabled camera's check finishes before its cache is cleared. Changing the source during a check returns HTTP 409 asking for a retry. UI/API polling does not start probes. Diagnostics preserve MediaMTX sourceOnDemand behavior and do not create temporary media paths.

Cameras must accept a brief additional RTSP session; devices with strict connection limits may reject probes. Results are availability snapshots, not continuous guarantees or WebRTC/ICE/firewall playback tests. H.264 is marked as likely browser-compatible. Raw camera messages and stderr are discarded; private URLs and credentials are excluded from logs, metrics, UI, and API errors.

## First-camera errors

| Symptom | Next step |
|---|---|
| Invalid RTSP URL | Use `rtsp://host[:port]/path` or `rtsps://…`; check port and avoid URL fragments. |
| Authentication failure | Check camera credentials; separate fields override embedded URL credentials. |
| Stream not found | Verify the vendor main-stream path. |
| Camera unavailable | Check power, address, DNS, RTSP port and access from the NVR host. |
| Timeout | Check routes, VLAN/firewall and camera connection limits. |
| Unreadable video | Check path/codec, try H.264. Some errors cannot be classified more precisely. |
| Recording reconnecting/stalled | Use Check, verify codec/MP4 compatibility and Storage. A process alone does not mean it is writing. |
| Storage blocked | Check write permissions, mount identity and space above reserve. |
| Check works, live fails | Check browser codec and UDP. Bridge: verify `WEBRTC_HOST`; NAT/VPN/proxy: reachable ICE candidates or TURN. |

Checks classify structured errors locally into fixed messages; a camera masking an authentication error may appear as unreadable media. Never submit credential-bearing RTSP URLs or raw camera output.

## Compatibility and reports

There are synthetic H.264 RTSP integration tests, but no verified brand/model list or hosted demo. A working RTSP source is required; automatic ONVIF discovery is unavailable. Native ARM and long-running real-camera/NAS testing remain unverified in the documented release preparation; ARM CI is configured, but target hardware must be checked.

In [Issues](https://github.com/vvbelousov/TupoNVR/issues), include model, firmware, codec, resolution, main/substream behavior, browser, and live/recording/archive results. Remove credentials, private URLs and footage. See [development validation](../CONTRIBUTING.md#validation) for synthetic sources.
