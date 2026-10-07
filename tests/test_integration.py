"""Opt-in test of the complete recording path, with no physical camera needed."""
import os
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from test_nvr import setup


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv('NVR_MEDIAMTX_BIN'), reason='Set NVR_MEDIAMTX_BIN to a MediaMTX 1.21.1 executable')
def test_real_recording_diagnostics_storage_and_schedules(tmp_path, monkeypatch):
    # The application uses MediaMTX's standard RTSP port. Never disturb another server.
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 8554))
    def port():
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            return sock.getsockname()[1]
    api_port, web_port, udp_port = port(), port(), port()
    config = (Path(__file__).resolve().parents[1] / 'mediamtx.yml').read_text()
    config = config.replace('apiAddress: :9997', f'apiAddress: 127.0.0.1:{api_port}').replace('rtspAddress: :8554', 'rtspAddress: 127.0.0.1:8554')
    config = config.replace('webrtcAddress: :8889', f'webrtcAddress: 127.0.0.1:{web_port}').replace('webrtcLocalUDPAddress: :8189', f'webrtcLocalUDPAddress: 127.0.0.1:{udp_port}')
    config = config.replace('paths: {}', 'paths:\n  source: {}').replace('      - action: read', '      - action: publish\n      - action: read')
    config_path = tmp_path / 'mediamtx.yml'
    config_path.write_text(config)
    encoders = subprocess.run(['ffmpeg', '-hide_banner', '-encoders'], capture_output=True, check=True).stdout
    encoder = 'libx264' if b'libx264' in encoders else 'libopenh264'
    monkeypatch.setenv('MEDIAMTX_API', f'http://127.0.0.1:{api_port}')
    monkeypatch.setenv('MEDIAMTX_RTSP_HOST', '127.0.0.1')
    monkeypatch.setenv('SEGMENT_SECONDS', '2')
    monkeypatch.setenv('MIN_FREE_SPACE_GB', '0')
    monkeypatch.delenv('WEBHOOK_URL', raising=False)
    main, _ = setup(tmp_path, monkeypatch)
    processes = []
    with (tmp_path / 'gateway.log').open('w') as gateway_log, (tmp_path / 'source.log').open('w') as source_log:
        try:
            gateway = subprocess.Popen([os.environ['NVR_MEDIAMTX_BIN'], str(config_path)], stdout=gateway_log, stderr=subprocess.STDOUT)
            processes.append(gateway)
            time.sleep(1)
            assert gateway.poll() is None, (tmp_path / 'gateway.log').read_text()
            source = subprocess.Popen(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-re', '-f', 'lavfi', '-i', 'testsrc2=size=128x96:rate=5', '-c:v', encoder, '-g', '10', '-pix_fmt', 'yuv420p', '-f', 'rtsp', '-rtsp_transport', 'tcp', 'rtsp://127.0.0.1:8554/source'], stdout=subprocess.DEVNULL, stderr=source_log)
            processes.append(source)
            time.sleep(1)
            assert source.poll() is None, (tmp_path / 'source.log').read_text()
            with TestClient(main.app) as client:
                assert client.post('/api/login', json={'username':'admin','password':'secret'}).status_code == 200
                assert client.get('/ready').status_code == 200
                destination = main.ROOT / 'nas'
                destination.mkdir()
                marker = destination / '.nvr-storage-id'
                marker.write_text('test-disk')
                assert client.put('/api/storage/destinations/nas', json={'expected_marker': 'test-disk'}).json()['ready']
                payload = {'name': 'Synthetic', 'rtsp_url': 'rtsp://127.0.0.1:8554/source', 'recording_destination': 'nas'}
                response = client.post('/api/cameras', json=payload)
                assert response.status_code == 201, response.text
                cid = response.json()['id']
                def state():
                    return client.get(f'/api/cameras/{cid}/status').json()
                def wait_for_health(value):
                    deadline = time.monotonic() + 20
                    while time.monotonic() < deadline:
                        current = state()
                        if current['recording_health'] == value:
                            return current
                        time.sleep(.2)
                    pytest.fail(f'Expected {value}, got {current}')
                assert wait_for_health('WRITING')['last_progress_at']
                diagnostic = client.post(f'/api/cameras/{cid}/check').json()
                assert diagnostic['ok'] and diagnostic['video']['codec_name'] == 'h264', diagnostic
                assert diagnostic['video']['width'] == 128
                time.sleep(4)
                marker.unlink()
                # Saving protection triggers the same reconciliation as the background loop.
                assert not client.put('/api/storage/destinations/nas', json={'expected_marker': 'test-disk'}).json()['ready']
                blocked = wait_for_health('STORAGE_UNAVAILABLE')
                assert blocked['recording_expected'] and not blocked['recorder_running']
                assert client.post(f'/api/cameras/{cid}/check').json()['ok'], 'Storage must not disable readable live source'
                marker.write_text('test-disk')
                assert client.put('/api/storage/destinations/nas', json={'expected_marker': 'test-disk'}).json()['ready']
                wait_for_health('WRITING')
                now = datetime.now(timezone.utc)
                minute = (now.hour * 60 + now.minute + 60) % 1440
                start = f'{minute // 60:02}:{minute % 60:02}'
                end = f'{(minute + 1) // 60:02}:{(minute + 1) % 60:02}'
                scheduled = {'timezone': 'UTC', 'windows': [{'days': list(range(7)), 'start': start, 'end': end}]}
                assert client.put(f'/api/cameras/{cid}', json={**payload, 'recording_schedule': scheduled}).status_code == 200
                paused = wait_for_health('SCHEDULED_PAUSE')
                assert not paused['recording_expected'] and not paused['recorder_running']
                assert client.post(f'/api/cameras/{cid}/check').json()['ok']
                archived = client.get(f'/api/recordings?camera_id={cid}').json()
                assert archived, 'Graceful schedule stop must finalize and index MP4s'
                first = archived[-1]
                assert client.get('/api/recordings/at', params={'camera_id': cid, 'time': first['started_at']}).json()['segment']['id'] == first['id']
                downloaded = client.get(f"/api/recordings/{first['id']}/download")
                assert downloaded.status_code == 200
                mp4 = tmp_path / 'download.mp4'
                mp4.write_bytes(downloaded.content)
                duration = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(mp4)], capture_output=True, check=True)
                assert float(duration.stdout) > 0
                assert client.get(f"/api/recordings/{first['id']}", headers={'Range': 'bytes=0-99'}).status_code == 206
                assert client.put(f'/api/cameras/{cid}', json={**payload, 'recording_schedule': None}).status_code == 200
                wait_for_health('WRITING')
                assert client.put(f'/api/cameras/{cid}',json={**payload,'recording_enabled':False}).status_code == 200
                assert state()['state']=='ONLINE' and state()['recording_health']=='PAUSED'
                # Let the idle on-demand relay close. Health must remain online.
                time.sleep(12)
                assert state()['state']=='ONLINE'
                inactive=client.portal.call(client.app.state.gateway.call,'GET','/v3/paths/list')
                assert not any(p['name']==f'cam_{cid}' and p.get('ready') for p in inactive.get('items',[]))
                viewer=subprocess.Popen(['ffprobe','-v','quiet','-rtsp_transport','tcp','-timeout','8000000','-show_streams',f'rtsp://127.0.0.1:8554/cam_{cid}'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                processes.append(viewer)
                assert state()['state']=='ONLINE'
                viewer.wait(timeout=15)
                assert viewer.returncode==0
                assert state()['state']=='ONLINE' and state()['recording_health']=='PAUSED'
                # Real source loss/recovery must follow direct RTSP probes,
                # even with recording paused and no viewer attached.
                source.terminate()
                source.wait(timeout=10)
                client.app.state.gateway.connectivity[cid]['_tick'] -= 11
                assert not client.post(f'/api/cameras/{cid}/check').json()['ok']
                assert state()['state']=='ONLINE'  # One failure is debounced.
                client.app.state.gateway.connectivity[cid]['_tick'] -= 11
                assert not client.post(f'/api/cameras/{cid}/check').json()['ok']
                assert state()['state']=='OFFLINE' and state()['recording_health']=='PAUSED'
                source = subprocess.Popen(source.args,stdout=subprocess.DEVNULL,stderr=source_log)
                processes.append(source)
                time.sleep(1)
                assert source.poll() is None
                client.app.state.gateway.connectivity[cid]['_tick'] -= 11
                assert client.post(f'/api/cameras/{cid}/check').json()['ok']
                assert state()['state']=='ONLINE' and state()['recording_health']=='PAUSED'
                assert client.post(f'/api/cameras/{cid}/stop').status_code == 200
                assert not state()['recorder_running']
                # Also check an intentionally disabled camera with the same direct source probe.
                assert client.post(f'/api/cameras/{cid}/check').json()['ok']
                assert not client.app.state.gateway.check_tasks
        finally:
            for process in reversed(processes):
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
