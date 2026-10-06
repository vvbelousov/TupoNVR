# Contributing to TupoNVR

Keep changes focused on practical home/small-installation workflows. Preserve stream-copy recording and the two-service deployment. Discuss substantial features before implementing them; avoid adding infrastructure merely to match a larger VMS.

## Development

Install Python 3.12+, Node 22+, FFmpeg/ffprobe, Docker with Compose v2, and system `tzdata`.

```sh
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements-dev.txt
npm ci --prefix frontend
cp .env.example .env
docker compose up -d --build
```

Use disposable data and synthetic cameras while developing. Never submit `.env`, databases, recordings, unredacted screenshots, tokens, or private camera URLs.

For backend iteration, MediaMTX must be reachable on both its API and RTSP ports. One local option is:

```sh
docker run --rm --name tuponvr-dev-mediamtx \
  -p 127.0.0.1:9997:9997 -p 127.0.0.1:8554:8554 \
  -p 8889:8889 -p 8189:8189/udp \
  -e MTX_WEBRTCADDITIONALHOSTS=127.0.0.1 \
  -v "$PWD/mediamtx.yml:/mediamtx.yml:ro" bluenviron/mediamtx:1.21.1
```

In another terminal:

```sh
DATABASE_PATH="$PWD/data-dev/nvr.sqlite3" DEFAULT_RECORDING_PATH="$PWD/recordings-dev" \
  MEDIAMTX_API=http://127.0.0.1:9997 MEDIAMTX_RTSP_HOST=127.0.0.1 \
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

## Pull requests

Explain the problem, resulting behavior, and validation. Add meaningful tests for bugs and features, update both interface languages when changing UI text, and update documentation/configuration when needed. Separate unrelated changes. Persistent-data, recording, storage, and authentication changes need explicit design review.

Report vulnerabilities as described in [SECURITY.md](SECURITY.md), not in public issues. Contributions are made under the project’s [Apache-2.0 license](LICENSE). See [release preparation](docs/releasing.md) for maintainer steps.
