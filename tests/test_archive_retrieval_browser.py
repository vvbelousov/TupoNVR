"""Retrieval controls with actual archive/export APIs and decoded browser media."""
import asyncio
import mimetypes
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from test_nvr import setup
from test_exports import clip


@pytest.mark.browser
@pytest.mark.skipif(os.getenv('NVR_RUN_BROWSER') != '1', reason='Build frontend and set NVR_RUN_BROWSER=1')
@pytest.mark.parametrize('language,width', [('en', 1280), ('ru', 1280), ('en', 390)])
def test_snapshots_recent_archive_ranges_and_export(tmp_path, monkeypatch, language, width):
    from playwright.async_api import async_playwright, expect
    main, video = setup(tmp_path, monkeypatch)
    main.init()
    import exports
    jobs = exports.ExportJobs()
    monkeypatch.setattr(exports, 'jobs', jobs)
    monkeypatch.setenv('APP_TIMEZONE', 'Europe/Moscow')
    monkeypatch.setenv('SETTINGS_PATH', str(tmp_path / 'settings.json'))
    _, path = clip(main, video)
    clip(main, video, second=6)
    if language == 'en' and width == 1280:
        clip(main, video, stamp=datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc))
        clip(main, video, stamp=datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc))
    client = TestClient(main.app)
    client.post('/api/login', json={'username': 'admin', 'password': 'secret'})
    root = Path(__file__).resolve().parents[1] / 'frontend' / 'dist'
    now = (datetime(2020, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=301)).isoformat()
    export_requests = []
    camera = {'id': 1, 'name': 'Door', 'enabled': True, 'recording_enabled': True}
    def ui(en, ru):
        return en if language == 'en' else ru

    async def exercise():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(executable_path=os.getenv('NVR_CHROME_EXECUTABLE') or None,
                                                       headless=True, args=['--no-sandbox'])
            try:
                context = await browser.new_context(timezone_id='Asia/Tokyo', viewport={'width': width, 'height': 900})
                page = await context.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                async def route(handler):
                    request = handler.request
                    url = urlsplit(request.url)
                    if url.path == '/reader.js':
                        # Decode real media and deliver a browser MediaStream to the live component.
                        await handler.fulfill(content_type='text/javascript', body='''window.MediaMTXWebRTCReader=class {
constructor(config){this.video=document.createElement('video');this.video.muted=true;this.video.loop=true;this.video.src='/clip.mp4';this.video.oncanplay=async()=>{await this.video.play();config.onTrack({streams:[this.video.captureStream()]})}}
close(){this.video.pause();this.video.src=''}};''')
                    elif url.path == '/clip.mp4':
                        await handler.fulfill(body=path.read_bytes(), content_type='video/mp4')
                    elif url.path == '/api/config':
                        await handler.fulfill(json={'timezone': main.get_timezone(), 'now': now, 'webrtc_port': 8889})
                    elif url.path == '/api/language':
                        await handler.fulfill(json={'default_language': language})
                    elif url.path == '/api/cameras':
                        await handler.fulfill(json=[camera])
                    elif url.path == '/api/dashboard':
                        await handler.fulfill(json={'cameras': 1, 'storage': {'free_bytes': None}, 'errors': []})
                    elif url.path.endswith('/status'):
                        await handler.fulfill(json={'connectivity_state': 'ONLINE'})
                    elif url.path.startswith('/api/'):
                        body = request.post_data_json if request.method in ('POST', 'PUT') else None
                        if url.path == '/api/recordings/exports' and request.method == 'POST':
                            export_requests.append(body)
                        headers = {'range': request.headers['range']} if 'range' in request.headers else {}
                        response = await asyncio.to_thread(client.request, request.method, url.path + ('?' + url.query if url.query else ''), json=body, headers=headers)
                        await handler.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)
                    else:
                        asset = root / url.path.lstrip('/') if url.path.startswith('/assets/') else root / 'index.html'
                        await handler.fulfill(body=asset.read_bytes(), content_type=mimetypes.guess_type(asset.name)[0] or 'application/octet-stream')
                await page.route('http://nvr.test/**', route)
                await page.goto('http://nvr.test/cameras/1/live')
                await expect(page.locator('video')).to_have_js_property('readyState', 4)
                async with page.expect_download() as live_download:
                    await page.get_by_role('button', name=ui('Save frame', 'Сохранить кадр'), exact=True).click()
                image = await live_download.value
                destination = await image.path()
                assert Path(destination).read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
                await page.get_by_role('link', name=ui('Recent recordings', 'Недавние записи'), exact=True).click()
                await expect(page.get_by_label(ui('Archive camera', 'Камера архива'), exact=True)).to_have_value('1')
                await expect(page.locator('.archive-camera video')).to_have_js_property('readyState', 4)
                await page.get_by_role('button', name=ui('Pause', 'Пауза'), exact=True).click()
                video_before = await page.locator('.archive-camera video').evaluate('(video)=>{window.archiveVideo=video;return video.currentTime}')
                for minutes in [5, 15, 30, 60]:
                    await page.get_by_role('button', name=f'{minutes} '+ui('min', 'мин'), exact=True).click()
                    await expect(page.get_by_label(ui('Archive camera', 'Камера архива'), exact=True)).to_have_value('1')
                    await expect(page.get_by_role('slider')).to_have_attribute('aria-valuemax', str(minutes*60-1))
                assert await page.locator('.archive-camera video').evaluate('(video)=>video===window.archiveVideo')
                assert abs(await page.locator('.archive-camera video').evaluate('(video)=>video.currentTime') - video_before) < .2
                async with page.expect_download() as archive_download:
                    await page.get_by_role('button', name=ui('Save frame', 'Сохранить кадр'), exact=True).click()
                assert Path(await (await archive_download.value).path()).read_bytes().startswith(b'\x89PNG')
                export = page.locator('.archive-export')
                await export.get_by_role('button', name=ui('Set start at cursor', 'Начало по курсору'), exact=True).click()
                start = await export.get_by_label(ui('Export start', 'Начало экспорта'), exact=True).input_value()
                assert start.startswith('2020-01-01T03:00:')  # configured timezone, not browser timezone
                await export.get_by_label(ui('Export start', 'Начало экспорта'), exact=True).fill('2020-01-01T03:00:01')
                await export.get_by_label(ui('Export end', 'Конец экспорта'), exact=True).fill('2020-01-01T03:00:09')
                await export.get_by_role('button', name=ui('Create export', 'Создать экспорт'), exact=True).click()
                await expect(export).to_contain_text(ui('Export ready', 'Экспорт готов'), timeout=20000)
                await expect(export).to_contain_text(ui('Recording gaps omitted', 'Пропуски записи исключены'))
                async with page.expect_download() as video_download:
                    await export.get_by_role('link', name=ui('Download MP4', 'Скачать MP4'), exact=True).click()
                assert (await video_download.value).suggested_filename.startswith('camera-1-')
                assert not jobs.jobs
                # Missing decoded media is reported in place, without replacing the player.
                await page.locator('.archive-camera video').evaluate('(video)=>{video.removeAttribute("src");video.load()}')
                await page.get_by_role('button', name=ui('Save frame', 'Сохранить кадр'), exact=True).click()
                await expect(page.locator('.snapshot-error')).to_contain_text(ui('No decoded frame', 'Нет декодированного кадра'))
                assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                if language == 'en' and width == 1280:
                    main.save_timezone('America/New_York')
                    await page.reload()
                    export = page.locator('.archive-export')
                    await export.get_by_label('Export start', exact=True).fill('2026-03-08T02:30')
                    await export.get_by_label('Export end', exact=True).fill('2026-03-08T03:30')
                    await export.get_by_role('button', name='Create export', exact=True).click()
                    await expect(export).to_contain_text('does not exist')
                    await export.get_by_label('Export start', exact=True).fill('2026-11-01T01:30')
                    await export.get_by_label('Export end', exact=True).fill('2026-11-01T01:30:02')
                    await export.get_by_role('button', name='Create export', exact=True).click()
                    await expect(export).to_contain_text('Repeated local time')
                    await export.get_by_label('Start occurrence', exact=True).select_option('earlier')
                    await export.get_by_label('End occurrence', exact=True).select_option('earlier')
                    await export.get_by_role('button', name='Create export', exact=True).click()
                    await expect(export).to_contain_text('Export ready', timeout=20000)
                    assert export_requests[-1]['start'] == '2026-11-01T05:30:00+00:00'
                    await export.get_by_label('Start occurrence', exact=True).select_option('later')
                    await export.get_by_label('End occurrence', exact=True).select_option('later')
                    await export.get_by_role('button', name='Create export', exact=True).click()
                    await expect(export).to_contain_text('Export ready', timeout=20000)
                    assert export_requests[-1]['start'] == '2026-11-01T06:30:00+00:00'
                assert not errors, errors
            finally:
                await browser.close()
    try:
        asyncio.run(exercise())
    finally:
        jobs.close()
