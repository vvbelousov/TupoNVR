# Recording cleanup

[Documentation](README.md) · [Русский](recording-cleanup_RU.md)

Storage settings include **Manage recordings / Storage cleanup**. Choose all
cameras or check one or more cameras, then select all time or a custom local
date/time range. Apply filters to update the recording list. Local inputs use
the installation timezone from Account, regardless of the browser timezone.
Nonexistent DST times are rejected; repeated times require choosing the earlier
or later occurrence.

Preview either all filtered recordings or selected recording IDs. The list shows
100 rows per page. Select all selects the current page only; selections persist
across pages and reset when filters change. Preview displays the count, indexed
file sizes, affected cameras, period and active exclusions. It makes no file or
index changes. Sizes come from SQLite metadata; the actual reclaimed byte count
uses the file's size at deletion, and can differ when a file is missing or changed.

Deletion requires the application confirmation dialog. Clearing all cameras for
all time additionally requires typing `DELETE`. Whole segments overlapping
`[start, end)` match: `segment.start < end && segment.end > start`. Footage outside
the interval can therefore be removed with an overlapping segment. Files are
never trimmed or re-encoded. Camera, interval and individual ID filters intersect.

## Architecture and API

Recorders write UTC-named MP4 segments under
`recording-root/destination/camera-id/YYYY/MM/DD/HH/YYYYMMDDTHHMMSS.mp4`.
SQLite's existing `segments` table remains the only index, populated using
FFprobe once segments are closed. The Archive list, timeline and synchronized
playback all query this table; deleting its rows removes footage from future
lookups. Cleanup completion also refreshes Archive tabs in the same browser
through a lightweight storage event, stopping stale playback selections. Existing
playback reports unavailable files without crashing.

All endpoints use the existing application authentication dependency (including
its explicitly configured no-auth installation mode):

- `POST /api/recordings/cleanup/query?offset=0&limit=100`: paginated metadata and
  total count. Accepts `camera_ids`, `recording_ids`, `start`, `end`.
- `POST /api/recordings/cleanup/preview`: accepts the same intersecting criteria,
  returns a preview and opaque token valid for 15 minutes.
- `POST /api/recordings/cleanup/delete`: `{token, confirmation}`; confirmation is
  `DELETE` for clear all, otherwise `confirm`. Returns 202 and the job token.
- `GET /api/recordings/cleanup/progress/{token}`: processed/total, deleted,
  actual reclaimed bytes, active exclusions, missing files/IDs and failures.
- Existing `DELETE /api/recordings/{id}` uses the same guarded routine; an active
  file returns 409 and unsafe/unavailable storage or unlink failure returns 503.

No client paths, sizes or deletion counts are accepted. UTC offsets are mandatory
for API time boundaries. Cleanup intervals are not limited to the Archive's
31-day playback query window.

## Protection and consistency

Execution operates on a fixed preview snapshot and checks each row again under
the existing archive lock. Newly indexed or replaced rows cannot enlarge the
confirmed operation. Preview exclusions remain excluded even if they close.
Repeated execution of a token returns the same job without deleting again.
Manual cleanup and retention share the lock and deletion helper.

FFmpeg's output directory is registered before process creation and remains
protected through process shutdown. The entire directory is conservatively
excluded, including closed segments beside the active one; cleanup never stops
cameras. Registration waits in a worker thread so archive maintenance cannot
block the ASGI event loop.

Each deletion opens the recording root and every directory component with
`O_NOFOLLOW`, verifies the configured `.nvr-storage-id` through the opened
destination, checks destination identity, and unlinks only an indexed regular
MP4 with the recognized recorder path and camera ID. Protected missing or
mismatched mounts fail safely. Unprotected local destinations retain the
installation's existing behavior; configure a storage ID to protect an external
mount. No directories, unknown files, marker files, databases or configuration
files are deleted. Unsafe rows and unindexed damaged files remain for inspection.

Successful unlinks are followed by individual SQLite commits. Unlink failures
retain their rows. Files already missing under a verified destination remove
stale rows, reclaim zero bytes, and are reported separately. Missing destinations
never justify metadata removal. Filesystem and SQLite changes are not atomic:
a crash or database failure after unlink leaves a stale row that subsequent
manual cleanup or retention reconciles. A failure is reported, never success for
an undeleted file. If the index update fails after a successful unlink, its actual
reclaimed bytes are still counted and the row remains available for reconciliation. Retention settings are unchanged.

## Limits

Progress and preview snapshots live in process memory (at most 32 retained
previews/jobs). Completed jobs and unused previews expire after 15 minutes;
running jobs continue beyond that interval. A lightweight daemon thread performs
the deletion and the UI polls progress. Restarting the process loses progress
and stops an in-flight job; submit a fresh preview to reconcile missing rows.
This follows the application's single-process deployment; multiple ASGI workers
are not supported for cleanup coordination. Navigating away stops UI polling
but does not cancel the operation. No new service, queue or database is required.

Preview uses indexed metadata, so footage not yet indexed is unavailable for
selection. Active directories are conservative exclusions. There is no additional
selection behavior on the synchronized playback timeline. Optional age presets
and an Archive inline delete button are not included.
