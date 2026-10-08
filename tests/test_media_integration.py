"""Real RTSP ingestion and authenticated Chromium WebRTC through FastAPI."""
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

from test_nvr import setup


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv('NVR_MEDIAMTX_BIN') or os.getenv('NVR_RUN_BROWSER') != '1',
                    reason='Set NVR_MEDIAMTX_BIN and NVR_RUN_BROWSER=1 for real WebRTC playback')
def test_authenticated_webrtc_and_logout(tmp_path, monkeypatch):
    from playwright.sync_api import sync_playwright

    def port():
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            return sock.getsockname()[1]

    api_port, web_port, udp_port, app_port, rtsp_port = port(), port(), port(), port(), port()
    root = Path(__file__).resolve().parents[1]
    config = (root / 'mediamtx.yml').read_text()
    config = config.replace('apiAddress: :9997', f'apiAddress: 127.0.0.1:{api_port}')
    config = config.replace('rtspAddress: :8554', f'rtspAddress: 127.0.0.1:{rtsp_port}')
    config = config.replace('webrtcAddress: :8889', f'webrtcAddress: 127.0.0.1:{web_port}')
    config = config.replace('webrtcLocalUDPAddress: :8189', f'webrtcLocalUDPAddress: 127.0.0.1:{udp_port}')
    # A synthetic publisher replaces a real camera in this disposable fixture only.
    config = config.replace('paths: {}', 'paths:\n  source: {}')
    config = config.replace('      - action: read', '      - action: publish\n      - action: read')
    config += '\nwebrtcAdditionalHosts: [127.0.0.1]\n'
    config_path = tmp_path / 'mediamtx.yml'
    config_path.write_text(config)
    monkeypatch.setenv('MEDIAMTX_API', f'http://127.0.0.1:{api_port}')
    monkeypatch.setenv('MEDIAMTX_RTSP_HOST', '127.0.0.1')
    monkeypatch.setenv('MEDIAMTX_WEBRTC', f'http://127.0.0.1:{web_port}')
    monkeypatch.setenv('MIN_FREE_SPACE_GB', '0')
    monkeypatch.setenv('COOKIE_SECURE', 'false')
    monkeypatch.delenv('WEBHOOK_URL', raising=False)
    main, _ = setup(tmp_path, monkeypatch)
    main.mount_ui(main.app, root / 'frontend/dist')
    server = uvicorn.Server(uvicorn.Config(main.app, host='127.0.0.1', port=app_port, log_level='error'))
    thread = threading.Thread(target=server.run, daemon=True)
    processes = []
    base = f'http://127.0.0.1:{app_port}'
    with (tmp_path / 'media.log').open('w') as media_log, (tmp_path / 'source.log').open('w') as source_log:
        try:
            gateway = subprocess.Popen([os.environ['NVR_MEDIAMTX_BIN'], str(config_path)], stdout=media_log, stderr=subprocess.STDOUT)
            processes.append(gateway)
            time.sleep(1)
            assert gateway.poll() is None, (tmp_path / 'media.log').read_text()
            encoders = subprocess.run(['ffmpeg', '-hide_banner', '-encoders'], capture_output=True, check=True).stdout
            encoder = 'libx264' if b'libx264' in encoders else 'libopenh264'
            source = subprocess.Popen(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-re', '-f', 'lavfi',
                                       '-i', 'testsrc2=size=128x96:rate=5', '-c:v', encoder, '-threads', '1', '-g', '10',
                                       '-pix_fmt', 'yuv420p', '-f', 'rtsp', '-rtsp_transport', 'tcp',
                                       f'rtsp://127.0.0.1:{rtsp_port}/source'], stdout=subprocess.DEVNULL, stderr=source_log)
            processes.append(source)
            time.sleep(1)
            assert source.poll() is None, (tmp_path / 'source.log').read_text()
            thread.start()
            deadline = time.monotonic() + 15
            while not server.started and time.monotonic() < deadline:
                time.sleep(.1)
            assert server.started
            with httpx.Client(base_url=base, trust_env=False, timeout=30) as client:
                assert client.options('/api/media/cam_1/whep').status_code == 401
                assert client.post('/api/media/cam_1/whep', content='offer').status_code == 401
                assert client.post('/api/login', json={'username': 'admin', 'password': 'secret'}).status_code == 200
                response = client.post('/api/cameras', json={'name': 'Synthetic', 'rtsp_url': f'rtsp://127.0.0.1:{rtsp_port}/source',
                                                             'recording_enabled': False})
                assert response.status_code == 201, response.text
                cid = response.json()['id']
                assert client.post(f'/api/cameras/{cid}/check').json()['ok']
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=os.getenv('NVR_CHROME_EXECUTABLE'), args=['--no-sandbox'])
                page = browser.new_page()
                page.goto(base + '/health')
                page.add_script_tag(path=str(root / 'frontend/public/reader.js'))
                assert page.evaluate("async () => (await fetch('/api/login', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:'admin',password:'secret'})})).status") == 200
                page.evaluate("""cid => {
                    const video = document.createElement('video'); video.autoplay = true; video.muted = true;
                    document.body.append(video); window.liveVideo = video;
                    window.reader = new MediaMTXWebRTCReader({url:location.origin+'/api/media/cam_'+cid+'/whep',
                      onTrack:event=>{video.srcObject=event.streams[0];video.play()},onError:error=>{window.liveError=error}});
                }""", cid)
                page.wait_for_function('window.liveVideo.videoWidth > 0 && window.liveVideo.currentTime > 0', timeout=30000)
                assert main.app.state.media.resources
                stolen = page.context.cookies()[0]['value']
                assert page.evaluate("async () => (await fetch('/api/logout',{method:'POST'})).status") == 204
                assert not main.app.state.media.resources
                with httpx.Client(base_url=base, trust_env=False) as replay:
                    replay.cookies.set('nvr_session', stolen)
                    assert replay.options(f'/api/media/cam_{cid}/whep').status_code == 401
                    assert replay.get('/api/config').status_code == 401
                sessions = httpx.get(f'http://127.0.0.1:{api_port}/v3/webrtcsessions/list', trust_env=False).json()
                assert not sessions['items'], sessions
                browser.close()
        finally:
            server.should_exit = True
            if thread.is_alive():
                thread.join(timeout=20)
            for process in reversed(processes):
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
