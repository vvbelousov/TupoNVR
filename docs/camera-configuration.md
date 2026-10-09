# Camera YAML configuration

[Русский](camera-configuration_RU.md) · [Documentation](README.md)

Available in **0.2.1**. SQLite remains authoritative. YAML is a one-shot bootstrap, explicit import/export, or portable settings backup; it does not synchronize on restart. Global settings remain environment variables. Footage, recording metadata, layouts, destination enrollment/identity markers and installation timezone are not included.

## Schema version 1

See the [complete example](examples/cameras.yaml). Top-level fields are `version: 1` and `cameras` (a list, up to 500). Unknown fields, unsupported versions, duplicate keys, custom YAML tags, aliases, non-UTF-8 files and documents over 1 MiB are rejected. Empty camera lists are valid; empty files are invalid. Nesting and parser complexity are bounded too; split unusually large schedule-heavy imports into smaller files.

| Camera field | Meaning / new-camera default |
|---|---|
| `key` | Required stable portable identifier: 1–64 ASCII letters, digits, `_` or `-` |
| `name` | Required display name, 1–100 characters |
| `rtsp_url` | Required `rtsp://` or `rtsps://` camera URL |
| `username`, `password` | Optional credentials; omitted/null/empty values keep existing credentials |
| `enabled` | Boolean, `true` |
| `recording_enabled` | Boolean, `true` |
| `recording_destination` | Directory name on recording volume, `default`; 1–64 letters/digits/`_`/`-` |
| `retention_days` | Integer 1–3650, default 7; explicit `null` means unlimited |
| `substream_url` | Optional RTSP URL, `null` |
| `description` | Text, up to 1000 characters, empty by default |
| `recording_schedule` | Optional schedule, `null` means continuous |

Schedules contain `windows` (1–14), each with unique integer `days` (Monday=0 through Sunday=6), `start` and `end` in `HH:MM`. Only `end` permits `24:00`. Overnight windows continue into the following day. The compatibility `timezone` field is retained in export/import; **execution uses the installation timezone in Account**, which is not changed by camera import. If omitted, schedule timezone defaults to the installation timezone. Credentials/URLs accept no control characters and at most 4096 characters.

## Identity and migration

Existing cameras receive generated stable keys through a migration on startup. SQLite IDs and recording associations stay intact. Exported keys remain stable across exports and renames. Imports match **only by key**; a changed key creates a new camera even if its name or URL matches another. Use an export as the starting point when editing an existing installation. Do not change keys during a rename.

For existing cameras, omitted fields preserve saved values. Explicit null retention/schedule values remove those limits. Null substream removes a substream; empty URL strings preserve saved URLs. Credential removal is not supported by YAML. Imports create/update/leave unchanged and never delete absent cameras. Repeated imports with the same configuration are idempotent.

## Optional bootstrap with Docker Compose

Prepare `cameras.yaml`, download `compose.bootstrap.yml` alongside the other deployment files, and add it to your normal Compose command. For example, using the versioned prebuilt `vvbelousov/tuponvr:0.2.1` image after publication:

```sh
curl -fsSLO https://raw.githubusercontent.com/vvbelousov/TupoNVR/dev/compose.bootstrap.yml
docker compose -f docker-compose.yml -f compose.bootstrap.yml up -d
```

Preserve all existing overlays (for example `compose.lan.yml`) on subsequent commands. Set `CAMERA_BOOTSTRAP_FILE=/absolute/path/cameras.yaml` in `.env` if needed. The optional overlay mounts the existing file read-only at `/config/bootstrap/cameras.yaml` and refuses to create a missing host path. Deployments without bootstrap use their normal Compose command and need no extra directories. Native deployments can place the file at that same fixed application path.

On startup: no file means normal startup; any existing camera means bootstrap is skipped. An empty camera database with a valid file imports all cameras transactionally before runtime activation. Invalid YAML, unreadable files or database failures produce a credential-free warning and **startup continues**, allowing repair through the UI. Validation failures leave the database unchanged. Restart never overwrites UI edits. If all cameras are deliberately deleted while the bootstrap file remains, the next restart imports it again; remove the overlay/file to avoid that behavior.

## Export, backup and import

On **Cameras → Import / Export**, choose **Export YAML**. By default passwords, usernames, embedded URL credentials and URL queries (which can contain vendor tokens) are excluded. URLs retain hosts and paths. To migrate an authenticated installation, explicitly select **Include credentials**; the UI warns that the download contains secrets. Export responses use `Cache-Control: no-store`. Protect backups with restrictive filesystem permissions/encryption and avoid committing them to source control.

For migration: export with credentials, prepare the target recording destinations and installation timezone separately, select the exported file on the target Cameras page, then **Preview changes → Confirm import**. A credentials-free export is useful for editing or migration of anonymous cameras; supply credentials locally if required. A new camera specifying a username without a password is rejected. Anonymous new cameras produce a reminder that credentials may need to be added after import. Missing/empty passwords never clear an existing password. Existing embedded credentials and URL query options are preserved when redacted URLs are imported.

Preview lists create/update/unchanged operations, field changes, validation errors, duplicate-key conflicts and warnings. Connection and credential changes are indicated without their values. Selecting a file does not apply it. Confirmation commits all changes in one SQLite transaction; changed configuration or a changed document invalidates the preview (409), requiring a new preview. A server restart invalidates previews too. The camera list refreshes on success.

Camera activation and MediaMTX updates happen after the database commit. If activation fails, the UI reports that settings were saved and activation is pending; existing periodic reconciliation retries. External processes cannot be rolled back with SQLite. YAML backs up camera settings only; retain the normal database and recording backups for full recovery. Redacted exports cannot reconstruct missing secrets or tokens, and destination protection must be configured separately.

## API

All routes use the existing authentication policy (public only when application authentication is disabled).

- `GET /api/camera-config/export`: downloadable `cameras.yaml`; `include_credentials=true` explicitly enables secrets.
- `POST /api/camera-config/preview`: raw UTF-8 YAML body (`Content-Type: application/yaml`), limited to 1 MiB. Returns `create`, `update`, `unchanged`, `errors`, `conflicts`, `warnings`, and a `fingerprint` only when valid. Validation errors return a structured preview with HTTP 200; oversized requests return 413.
- `POST /api/camera-config/apply`: same raw YAML body; headers `X-Confirm-Import: true` and `X-Import-Fingerprint: <preview fingerprint>`. Returns counts `created`, `updated`, `unchanged` and `runtime_pending`. Missing confirmation/invalid configuration: 422; stale preview: 409; database failure with rollback: 503.

Responses are not cached. Previews store no secrets server-side; fingerprints use an ephemeral keyed digest and detect changes in the document, camera configuration and installation timezone. Internal database IDs are never portable YAML identifiers.
