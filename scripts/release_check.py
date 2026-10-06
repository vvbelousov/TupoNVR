"""Validate release metadata without publishing or contacting an external account."""
import json
import os
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r'v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?')


def validate(tag, prerelease, version, username, image, platforms, license_id, has_license):
    match = SEMVER.fullmatch(tag)
    if not match or tag[1:] != version:
        raise ValueError('Release tag must match the version in frontend/package.json')
    identifiers = (match[4] or '').split('.')
    if any(value.isdigit() and len(value) > 1 and value.startswith('0') for value in identifiers):
        raise ValueError('Numeric prerelease identifiers cannot have leading zeros')
    if bool(match[4]) != prerelease:
        raise ValueError('GitHub prerelease flag must match the version suffix')
    if not has_license or not re.fullmatch(r'[A-Za-z0-9.+-]+', license_id):
        raise ValueError('A LICENSE and SPDX license identifier in frontend/package.json are required')
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', username):
        raise ValueError('Set DOCKERHUB_USERNAME to the Docker Hub login name')
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*/[a-z0-9]+(?:[._-][a-z0-9]+)*', image):
        raise ValueError('Set DOCKERHUB_IMAGE to a lowercase namespace/repository without a tag')
    if platforms not in ('linux/amd64', 'linux/amd64,linux/arm64'):
        raise ValueError('DOCKER_PLATFORMS must be linux/amd64 or linux/amd64,linux/arm64')
    return {'version': version, 'stable': str(not prerelease).lower(), 'platforms': platforms, 'license': license_id}


def eligible_aliases(prerelease, release_id, latest_id):
    return not prerelease and str(release_id) == str(latest_id)


if __name__ == '__main__':
    manifest = json.loads((ROOT / 'frontend/package.json').read_text())
    version = manifest['version']
    values = validate(os.getenv('RELEASE_TAG', ''), os.getenv('RELEASE_PRERELEASE') == 'true', version,
                      os.getenv('DOCKERHUB_USERNAME', ''), os.getenv('DOCKERHUB_IMAGE', ''),
                      os.getenv('DOCKER_PLATFORMS', 'linux/amd64'), manifest.get('license', ''),
                      (ROOT / 'LICENSE').is_file())
    aliases = False
    if values['stable'] == 'true':
        url = f"{os.environ['GITHUB_API_URL']}/repos/{os.environ['GITHUB_REPOSITORY']}/releases/latest"
        request = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + os.environ['GH_TOKEN'],
                                                       'Accept': 'application/vnd.github+json'})
        with urllib.request.urlopen(request, timeout=15) as response:
            latest = json.load(response)
        aliases = eligible_aliases(False, os.environ['RELEASE_ID'], latest['id'])
    values['aliases'] = str(aliases).lower()
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        for key, value in values.items():
            output.write(f'{key}={value}\n')
