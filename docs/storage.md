# Storage, retention, and backup

[Documentation](README.md) · [Русский](storage_RU.md)

## Storage and retention

`DEFAULT_RECORDING_PATH` in `.env` is a **host path** mounted at `/recordings` in the application container. It can be a local disk or an already mounted NFS/SMB directory. For example:

```sh
sudo mount -t nfs nas:/export/nvr /srv/nvr
# Then set DEFAULT_RECORDING_PATH=/srv/nvr in .env.
```

A camera's `recording_destination` is a subdirectory name, such as `default` or `garage`; absolute paths and `..` are rejected. A separate filesystem can be mounted inside that directory on the host. Configure mounts, write permissions, and mounting after reboot on the host. Check them before enabling recording, including behavior when NFS/SMB becomes unavailable.

Retention defaults to seven days and accepts 1–3650 days; an empty value means no age limit. Recordings from deleted cameras use a seven-day age limit measured from each segment’s start, not from camera deletion. Every five minutes, the worker indexes recordings and removes expired segments. When space is low, it deletes the oldest indexed files toward the `MIN_FREE_SPACE_GB` reserve on each affected filesystem. The current hour of an active camera is protected from deletion. If active segments occupy all available space, the reserve cannot be guaranteed: size storage appropriately, configure retention, and monitor `/metrics`.

No age limit still permits low-space deletion. Individual segments can be deleted through the API or [manual cleanup](recording-cleanup.md). SQLite uses WAL, and the worker removes metadata for missing files. Camera IDs are not reused after deletion. Existing databases receive the application's established schema upgrades at startup while preserving rows and accounting for existing recordings and layouts.


## Per-destination storage protection

Each destination is checked independently for availability, free space on its filesystem, a real temporary write/fsync, and the `MIN_FREE_SPACE_GB` reserve. If unavailable, unwritable, or below reserve, its cameras stop recording and resume after recovery. Other destinations and live viewing continue. Checks run roughly every five seconds in bounded subprocesses with a three-second timeout. Stale status is not considered ready. Archive metadata for an unavailable destination is not removed as if its files were missing.

Enable identity protection for external mounts **before enabling camera recording**:

1. Mount the resource under `DEFAULT_RECORDING_PATH` on the host, for example `/srv/nvr/nas`. Configure write permissions and mounting after reboot.
2. In **Storage**, choose **Create protection ID**. The application creates a random 256-bit ID, atomically publishes `.nvr-storage-id` without overwriting, and verifies readback. The destination must already exist; this operation does not create a directory in place of a missing mount. If it is not visible as a separate mount point, creation requires explicit confirmation of intentionally local storage. Do not confirm this for a missing external resource.
3. If an ID already exists, choose **Use existing ID**. A mismatch with the expected ID requires explicit confirmation before adopting the connected resource's ID. Creation never replaces an existing file or configured expected ID. Repair an invalid marker manually on the intended resource. If the filesystem lacks hard-link/fsync support, automatic creation fails and manual setup remains available.
4. **Manual configuration:** create `.nvr-storage-id` on the resource with 1–128 characters (ASCII letters, digits, `_`, `-`) and save the expected ID. Changing or disabling protection requires UI confirmation. `PUT /api/storage/destinations/nas` accepts `{"expected_marker":"nas-primary-01"}`. `POST /api/storage/destinations/{name}/protection` accepts `action` (`create`/`use_existing`), `previous_expected_marker` (including null), `replace_existing`, and `allow_local`. Operations apply only to configured destinations and use bounded subprocesses, a three-second timeout, safe errors, and protection against symlinks/directory replacement. If creation fails after writing the marker, it may remain; verify the resource and adopt its existing ID.
5. Confirm that the destination is ready, then enable recording. If it is not listed yet, register it through the manual configuration API or add a camera with recording disabled and that destination selected.

If a mount disappears or its ID is absent/mismatched, the NVR does not create the protected directory/ID or start recording into the underlying local directory. `{"expected_marker":null}` disables protection. Ordinary local destinations, including `default`, retain automatic directory creation.

The ID checks identity; it is not a secret or a replacement for an OS mount. Already open file descriptors remain under OS control after resource loss. The NVR cannot guarantee the current segment survives or resolve hung kernel I/O on NFS/SMB. For nested host mounts, configure suitable Compose bind mounts and verify visibility inside the container; the NVR does not manage mount propagation.


## Updating, backup, and recovery

Keep `DATA_DIR`, `DEFAULT_RECORDING_PATH`, `.env`, and any host/NAS mount configuration across updates. The data directory includes SQLite, its WAL/SHM files when present, and timezone `settings.json`; storage markers live on their respective recording destinations.

Before updating, stop the application cleanly with `docker compose stop nvr-app` (include the same `-f` arguments as your deployment). Back up the complete data directory, configuration, recording directories, and `.nvr-storage-id` files while recording is stopped. Protect backups like camera credentials. Do not copy only an actively written SQLite file or regenerate storage IDs during restoration. There is no automated backup/recovery feature.

For a published-image deployment, read the release notes, set `NVR_IMAGE` to the desired fixed version, and run:

```sh
docker compose -f docker-compose.yml -f compose.image.yml pull
docker compose -f docker-compose.yml -f compose.image.yml up -d --no-build
```

For LAN, add `-f compose.lan.yml` after the base file in **both** commands. For a source build, update the reviewed source and run `docker compose up -d --build` with the configured local `NVR_IMAGE` and deployment files. Check `/health`, `/ready`, storage readiness, and recording progress afterward. The application performs its existing database upgrades at startup. An older image may not support an upgraded database; rollback can require restoring the matching pre-upgrade data backup. Do not delete persistent directories when recreating containers.

To recover, keep the application stopped, restore the complete matching backup with the expected ownership and storage mounts, select the matching application version, and start it. Check destination IDs and readiness before recording. Verify your backup procedure on disposable data; recovery has no dedicated UI or automatic migration rollback.
