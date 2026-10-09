# Development and validation

[Documentation](README.md) · [Contributing](../CONTRIBUTING.md)

These instructions are for contributors and developers. Regular users should follow [installation](installation.md) with prebuilt images. Run all commands from the repository root.

```sh
git clone --branch dev https://github.com/vvbelousov/TupoNVR.git
cd TupoNVR
```

## Development

Install Python 3.12+, Node 22+, FFmpeg/ffprobe, Docker with Compose v2, and system `tzdata`.

```sh
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements-dev.txt
npm ci --prefix frontend
cp .env.example .env
docker compose -f docker-compose.yml -f compose.dev.yml up -d --build
```

The development override sets `tuponvr:local` and `pull_policy: build`, so it never tags a local build as the official image. For host networking add `-f compose.lan.yml` before `-f compose.dev.yml`. Use the same files for subsequent commands.

Use disposable data and synthetic cameras while developing. Never submit `.env`, databases, recordings, unredacted screenshots, tokens, or private camera URLs.

For backend iteration, MediaMTX must be reachable on both its API and RTSP ports. One local option is:

```sh
docker run --rm --name tuponvr-dev-mediamtx \
  -p 127.0.0.1:9997:9997 -p 127.0.0.1:8554:8554 \
  -p 127.0.0.1:8889:8889 -p 8189:8189/udp \
  -e MTX_WEBRTCADDITIONALHOSTS=127.0.0.1 \
  -v "$PWD/mediamtx.yml:/mediamtx.yml:ro" bluenviron/mediamtx:1.21.1
```

In another terminal:

```sh
DATABASE_PATH="$PWD/data-dev/nvr.sqlite3" DEFAULT_RECORDING_PATH="$PWD/recordings-dev" \
  MEDIAMTX_API=http://127.0.0.1:9997 MEDIAMTX_RTSP_HOST=127.0.0.1 MEDIAMTX_WEBRTC=http://127.0.0.1:8889 \
  .venv/bin/uvicorn main:app --app-dir backend --host 127.0.0.1 --port 8000 --workers 1
```

Set authentication explicitly if this development server is reachable by other people. Do not use multiple workers or hot reload while recording: process ownership belongs to one application instance. These development directories are local artifacts; remove them from any proposed change.

`npm run dev --prefix frontend` starts Vite on port 5173. There is no API proxy configured: Vite alone is useful for frontend work, while the local Uvicorn command above serves the API on port 8000. Build with `npm run build --prefix frontend`. For integrated UI/API/media testing, use the Docker build, which places the compiled UI at `/app/static` for FastAPI to serve.

## Validation

```sh
scripts/validate.sh
```

This runs dependency checks, Ruff correctness rules (F), frontend tests, strict TypeScript checking, Vite production build, and pytest. No additional formatter is mandated.

For full integration/browser coverage, download the official MediaMTX 1.21.1 binary, install Chromium with `.venv/bin/python -m playwright install chromium`, and run:

```sh
NVR_MEDIAMTX_BIN=/absolute/path/to/mediamtx NVR_RUN_BROWSER=1 scripts/validate.sh
```

The integration test needs a free localhost RTSP port 8554. `NVR_CHROME_EXECUTABLE` can select an installed Chrome. Production deployment can also be exercised without any real camera:

```sh
docker build -f backend/Dockerfile -t tuponvr:test .
python3 scripts/smoke.py --image tuponvr:test
```

The smoke test creates its own configuration, authentication, directories, project, and synthetic source, verifies recording/storage/archive/persistence, and removes only its own deployment. It needs Docker socket access and permission to pull the public MediaMTX image if not already cached. Never run tests against production data.


## Debugging

Use `docker compose -f docker-compose.yml -f compose.dev.yml logs --tail=100 nvr-app` and inspect `/health`, `/ready`, camera diagnostics, Storage, and browser developer tools. Set `LOG_LEVEL=DEBUG` only on disposable installations and redact private camera URLs and credentials before sharing logs. Rebuild the development image after source changes.

Tests isolate SQLite and recordings in temporary directories. CI runs Python 3.12 / Node 22, checks the downloaded MediaMTX checksum, builds the Docker image, and checks static assets and SQLite upgrades. Browser tests exercise both UI languages; timezone tests cover DST, schedules and unchanged recording timestamps. Synthetic tests do not replace long-running real-camera/NAS testing.
