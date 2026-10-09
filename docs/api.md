# Interface and API

[Documentation](README.md) · [Русский](api_RU.md)

- **Account:** language, installation timezone, configured username and logout. `POST /api/logout` revokes the server session and deletes its active WebRTC resources, then clears the browser session cookie; cached HTTP Basic credentials do not authenticate application requests. Account passwords are configured through the environment, with no UI password-change flow.
- **Overview:** camera counts, connectivity, recording progress, free space and errors. Watch opens `/cameras/<id>/live` for that camera’s main stream. Refresh and browser navigation preserve the camera; saved Multiview selections stay independent.
- **Cameras:** add, edit, disable, check, and delete cameras. Blank passwords or RTSP URLs preserve saved values during editing; a separate switch removes the substream. List URLs hide paths and query parameters that may contain secrets.
- **Multiview:** add live cameras, drag tile headers, resize using the corner, and choose `contain`/`cover`. Individual viewing uses the main stream.
- **Archive:** investigate one, several, or all cameras at a shared local date/time. Aligned availability tracks expose gaps; shared controls provide play/pause, seek, and speed. Each camera can independently enter fullscreen. Segment transitions happen independently. Single-camera previous/next, automatic-next, pagination, and downloads remain available.
- **Storage:** destination availability, write access, free-space reserves, missing-mount protection, and webhook delivery status.

OpenAPI is available at `/docs`. Main routes include:

| Area | Routes |
|---|---|
| Cameras | `GET/POST /api/cameras`, `GET/PUT/PATCH/DELETE /api/cameras/{id}`, `GET /api/cameras/{id}/edit`, `POST /api/cameras/{id}/start`, `/stop`, `/check`, `GET /api/cameras/{id}/status` |
| Archive files | `GET /api/recordings?camera_id=&date=YYYY-MM-DD&limit=&offset=`, `GET/DELETE /api/recordings/{id}`, `GET /api/recordings/{id}/download` |
| Archive lookup | `GET /api/recordings/timeline?camera_id=&start=&end=`, `GET /api/recordings/at?camera_id=&time=`, `GET /api/recordings/{id}/adjacent?direction=next\|previous`, `POST /api/recordings/timelines`, `POST /api/recordings/resolve` |
| Time and language | `GET /api/config`, `GET/PUT /api/time`, `GET /api/time/day?date=YYYY-MM-DD`, `POST /api/time/resolve`, `GET /api/language` |
| Layout and dashboard | `GET/PUT /api/layout`, `GET /api/dashboard` |
| Storage and notifications | `GET /api/storage/status`, `GET /api/storage/destinations`, `PUT /api/storage/destinations/{name}`, `POST /api/storage/destinations/{name}/protection`, `GET /api/notifications/status` |
| Service health | `GET /health`, `GET /ready`, `GET /metrics` |

When authentication is enabled, `POST /api/login` creates an HttpOnly session cookie. CLI clients use the same login endpoint and retain its session cookie. Unauthenticated protected APIs return 401 without an HTTP Basic challenge.

`/health` and `/ready` are public; `/metrics` requires login when configured. OpenAPI describes current schemas; `{id}` names here are illustrative.

[Export and time APIs](time-and-archive.md#api-additions) and [manual cleanup APIs](recording-cleanup.md#architecture-and-api) are documented in their guides.

Camera YAML settings: [schema, bootstrap and import/export API](camera-configuration.md).
