"""Clean Compose recording test; touches only a unique temporary deployment."""
import argparse
import http.cookiejar
import json
import os
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def free_port(kind=socket.SOCK_STREAM):
    with socket.socket(type=kind) as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def wait_for(check, description, timeout=40):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = check()
            if value:
                return value
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            pass
        time.sleep(.5)
    raise RuntimeError(f'Timed out: {description}')


def smoke(image, uid, gid):
    project = 'nvr-smoke-' + secrets.token_hex(5)
    temporary = Path(tempfile.mkdtemp(prefix='nvr-smoke-'))
    source_name = project + '-source'
    environment = {**os.environ, 'COMPOSE_PROJECT_NAME': project}
    environment.pop('COMPOSE_FILE', None)
    # All configuration is explicit; never load the caller's .env or Compose overrides.
    compose = ['docker', 'compose', '--project-name', project, '--project-directory', str(temporary),
               '--env-file', str(temporary / '.env'), '-f', str(temporary / 'docker-compose.yml')]
    def run(arguments, **kwargs):
        return subprocess.run(arguments, env=environment, check=True, **kwargs)
    def compose_run(*arguments, **kwargs):
        return run([*compose, *arguments], **kwargs)
    username, password = 'smoke', secrets.token_hex(24)
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    port, web_port, udp_port = free_port(), free_port(), free_port(socket.SOCK_DGRAM)
    base = f'http://127.0.0.1:{port}'
    def request(path, method='GET', body=None, headers=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(base + path, data=data, method=method,
                                     headers={'Content-Type': 'application/json', **(headers or {})})
        with opener.open(req, timeout=20) as response:
            content = response.read()
            return response.status, json.loads(content) if response.headers.get_content_type() == 'application/json' else content
    try:
        for name in ('data', 'recordings'):
            (temporary / name).mkdir()
        (temporary / 'recordings/default').mkdir()
        shutil.copyfile(ROOT / 'docker-compose.yml', temporary / 'docker-compose.yml')
        config = (ROOT / 'mediamtx.yml').read_text().replace('paths: {}', 'paths:\n  source: {}')
        config = config.replace('      - action: read', '      - action: publish\n      - action: read')
        (temporary / 'mediamtx.yml').write_text(config)
        values = {'NVR_IMAGE': image, 'NVR_BIND': '127.0.0.1', 'NVR_PORT': port, 'WEBRTC_PORT': web_port, 'WEBRTC_UDP_PORT': udp_port,
                  'WEBRTC_HOST': '127.0.0.1', 'DATA_DIR': './data', 'DEFAULT_RECORDING_PATH': './recordings',
                  'MIN_FREE_SPACE_GB': 0, 'SEGMENT_SECONDS': 2, 'AUTH_USERNAME': username, 'AUTH_PASSWORD': password,
                  'APP_TIMEZONE': 'UTC', 'DEFAULT_LANGUAGE': 'en', 'COOKIE_SECURE': 'false', 'LOG_LEVEL': 'WARNING',
                  'WEBHOOK_URL': '', 'WEBHOOK_TOKEN': '', 'NVR_UID': uid, 'NVR_GID': gid}
        # Compose gives shell variables precedence over .env; override relevant names too.
        environment.update({key: str(value) for key, value in values.items()})
        (temporary / '.env').write_text(''.join(f'{key}={value}\n' for key, value in values.items()))
        os.chmod(temporary / '.env', 0o600)
        compose_run('config', '--quiet')
        compose_run('up', '-d', '--no-build', '--pull', 'missing')
        wait_for(lambda: request('/ready')[0] == 200, 'application and MediaMTX ready')
        container = compose_run('ps', '-q', 'nvr-app', capture_output=True, text=True).stdout.strip()
        wait_for(lambda: run(['docker', 'inspect', '--format', '{{.State.Health.Status}}', container],
                            capture_output=True, text=True).stdout.strip() == 'healthy', 'container healthcheck')
        assert request('/')[0] == 200
        try:
            urllib.request.urlopen(base + '/api/cameras', timeout=5)
        except urllib.error.HTTPError as error:
            assert error.code == 401
            assert 'www-authenticate' not in error.headers
        else:
            raise AssertionError('Configured authentication must reject unauthenticated API access')
        assert request('/api/login', 'POST', {'username': username, 'password': password})[0] == 200
        assert request('/api/cameras')[1] == []
        run(['docker', 'run', '-d', '--rm', '--name', source_name, '--network', project + '_media',
             '--entrypoint', 'ffmpeg', image, '-hide_banner', '-loglevel', 'error', '-re', '-f', 'lavfi',
             '-i', 'testsrc2=size=128x96:rate=5', '-c:v', 'libx264', '-threads', '1', '-g', '10',
             '-pix_fmt', 'yuv420p', '-f', 'rtsp', '-rtsp_transport', 'tcp', 'rtsp://mediamtx:8554/source'])
        time.sleep(2)
        assert request('/api/storage/destinations/default', 'PUT', {'expected_marker': None})[0] == 200
        protected = request('/api/storage/destinations/default/protection', 'POST',
                            {'action': 'create', 'previous_expected_marker': None, 'allow_local': True})[1]
        marker_id = protected['expected_marker']
        assert protected['ready'] and marker_id
        _, camera = request('/api/cameras', 'POST', {'name': 'Synthetic smoke camera',
                            'rtsp_url': 'rtsp://mediamtx:8554/source', 'recording_enabled': False})
        cid = camera['id']
        probe = request(f'/api/cameras/{cid}/check', 'POST')[1]
        assert probe['ok'] and probe['video']['codec_name'] == 'h264', 'Synthetic source must be readable'
        assert request(f'/api/cameras/{cid}', 'PATCH', {'recording_enabled': True})[0] == 200
        def health():
            return request(f'/api/cameras/{cid}/status')[1]
        wait_for(lambda: health()['recording_health'] == 'WRITING', 'FFmpeg recording progress')
        time.sleep(5)
        marker = temporary / 'recordings/default/.nvr-storage-id'
        marker.unlink()
        assert not request('/api/storage/destinations/default', 'PUT', {'expected_marker': marker_id})[1]['ready']
        wait_for(lambda: health()['recording_health'] == 'STORAGE_UNAVAILABLE', 'missing storage ID protection')
        marker.write_text(marker_id + '\n')
        assert request('/api/storage/destinations/default', 'PUT', {'expected_marker': marker_id})[1]['ready']
        wait_for(lambda: health()['recording_health'] == 'WRITING', 'recording recovery')
        time.sleep(3)
        assert request(f'/api/cameras/{cid}', 'PATCH', {'recording_enabled': False})[0] == 200
        records = wait_for(lambda: request(f'/api/recordings?camera_id={cid}')[1], 'finalized archive metadata')
        sid = records[0]['id']
        status, fragment = request(f'/api/recordings/{sid}', headers={'Range': 'bytes=0-99'})
        assert status == 206 and len(fragment) == 100
        assert request(f'/api/recordings/{sid}/download')[0] == 200
        assert request('/api/time', 'PUT', {'timezone': 'Europe/Moscow'})[0] == 200
        compose_run('restart', 'nvr-app')
        wait_for(lambda: request('/ready')[0] == 200, 'restart readiness')
        # In-memory sessions deliberately do not survive application restarts.
        # Keep the old cookie to verify revocation before obtaining a new one.
        try:
            request('/api/config')
        except urllib.error.HTTPError as error:
            assert error.code == 401
            assert 'www-authenticate' not in error.headers
        else:
            raise AssertionError('Restart must invalidate the previous session')
        assert request('/api/login', 'POST', {'username': username, 'password': password})[0] == 200
        assert request('/api/config')[1]['timezone'] == 'Europe/Moscow'
        assert request('/api/cameras')[1][0]['id'] == cid
        assert request('/api/storage/destinations')[1][0]['expected_marker'] == marker_id
        assert any(row['id'] == sid for row in request(f'/api/recordings?camera_id={cid}')[1])
        print('PASS: clean Compose install, health, auth, synthetic RTSP, diagnostics, recording, storage protection/recovery, archive/range/download, restart session revocation/relogin, and persistence')
    finally:
        subprocess.run(['docker', 'rm', '-f', source_name], env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # Cleanup is confined to this newly created directory/project, never user data.
        subprocess.run([*compose, 'down', '--volumes', '--remove-orphans'], env=environment, check=False)
        if temporary.exists():
            subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--user', '0:0',
                            '--mount', f'type=bind,src={temporary},dst=/cleanup', '--entrypoint', 'python', image,
                            '-c', "import shutil; shutil.rmtree('/cleanup/data'); shutil.rmtree('/cleanup/recordings')"],
                           env=environment, check=False)
            shutil.rmtree(temporary)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True, help='An already built local application image (never pushed)')
    parser.add_argument('--uid', type=int, default=0)
    parser.add_argument('--gid', type=int, default=0)
    args = parser.parse_args()
    smoke(args.image, args.uid, args.gid)
