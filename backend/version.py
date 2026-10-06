"""Use the frontend package version for local runs and production images."""
import json
from pathlib import Path

bundled = Path(__file__).with_name('version.json')
manifest = bundled if bundled.exists() else Path(__file__).resolve().parents[1] / 'frontend' / 'package.json'
VERSION = json.loads(manifest.read_text())['version']
