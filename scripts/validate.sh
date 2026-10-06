#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python_bin="${NVR_PYTHON:-.venv/bin/python}"
if ! command -v "$python_bin" >/dev/null; then python_bin=python3; fi
"$python_bin" -m pip check
# Correctness lint; the existing repository has no formatting convention/check.
"$python_bin" -m ruff check --select F backend tests scripts
npm test --prefix frontend
npm run build --prefix frontend
"$python_bin" -m pytest -q
