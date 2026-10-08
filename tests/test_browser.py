"""Opt-in Chromium workflows against built assets and deterministic API fixtures."""
import asyncio
import mimetypes
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest


async def fullscreen_roundtrip(page, container, language):
    """Native fullscreen must preserve the video instance and playback state."""
    from playwright.async_api import expect
    enter='Enter fullscreen' if language=='en' else 'На весь экран'
    leave='Exit fullscreen' if language=='en' else 'Выйти из полноэкранного режима'
    before=await container.locator('video').evaluate('(video)=>{window.fullscreenVideo=video;return {time:video.currentTime,paused:video.paused}}')
    await container.get_by_role('button',name=enter,exact=True).click()
    await expect(container.get_by_role('button',name=leave,exact=True)).to_have_attribute('aria-pressed','true')
    assert await container.evaluate('(element)=>document.fullscreenElement===element&&element.querySelectorAll("video").length===1')
    assert await container.locator('video').evaluate('(video)=>video===window.fullscreenVideo&&getComputedStyle(video).objectFit==="contain"')
    dimensions=await container.evaluate('(element)=>({width:element.clientWidth,height:element.clientHeight,viewportWidth:innerWidth,viewportHeight:innerHeight})')
    assert abs(dimensions['width']-dimensions['viewportWidth'])<=1 and abs(dimensions['height']-dimensions['viewportHeight'])<=1,dimensions
    await container.get_by_role('button',name=leave,exact=True).click()
    await expect(container.get_by_role('button',name=enter,exact=True)).to_have_attribute('aria-pressed','false')
    after=await container.locator('video').evaluate('(video)=>({same:video===window.fullscreenVideo,time:video.currentTime,paused:video.paused})')
    assert after['same'] and after['paused']==before['paused'],(before,after)
    assert abs(after['time']-before['time'])<2,(before,after)


def archive_day(day, zone='UTC'):
    from zoneinfo import ZoneInfo
    local = datetime.fromisoformat(day).replace(tzinfo=ZoneInfo(zone))
    start = local.astimezone(timezone.utc)
    end = (local + timedelta(days=1)).astimezone(timezone.utc)
    ticks = []
    stamp = start
    while stamp <= end:
        ticks.append({'time':stamp.isoformat(),'local':stamp.astimezone(ZoneInfo(zone)).isoformat()})
        stamp += timedelta(hours=1)
    return {'timezone':zone,'start':start.isoformat(),'end':end.isoformat(),'ticks':ticks}


@pytest.mark.browser
@pytest.mark.skipif(os.getenv('NVR_RUN_BROWSER') != '1', reason='Build frontend and set NVR_RUN_BROWSER=1')
@pytest.mark.parametrize('language', ['en', 'ru'])
def test_ui_schedules_storage_diagnostics_and_sequential_archive(tmp_path, language):
    from playwright.async_api import async_playwright, expect

    labels = {
        'Ручная настройка':'Manual configuration','Создать ID защиты':'Create protection ID','Использовать существующий ID':'Use existing ID','Имя':'Name','Логин':'Username','Пароль':'Password','Описание':'Description','Закрыть ×':'Close ×','Подключение':'Connectivity','Запись':'Recording','В сети':'Online','Запись приостановлена':'Recording paused','Не в сети':'Offline','Запись не продвигается':'Recording stalled','Войти':'Sign in','Пишут':'Writing','Камеры':'Cameras','Проверить':'Check','Править':'Edit',
        'Ограничить время записи':'Limit recording hours','Часовой пояс IANA':'IANA timezone','Начало':'Start',
        'Конец (24:00 — конец суток)':'End (24:00 = end of day)','Сохранить':'Save','Добавить':'Add',
        'Хранилище':'Storage','ID хранилища nas':'Storage ID nas','Сохранить защиту':'Save protection',
        'Готово к записи':'Ready to record','Архив':'Archive','Дата архива UTC':'Archive date UTC',
        'Камера архива':'Archive camera','Шкала записи':'Recording timeline','Смотреть':'Watch',
        'Загрузить ещё':'Load more','Время архива UTC':'Archive time UTC','Перейти · UTC':'Go to time · UTC',
        'Пробел в записи: 10 с.':'Recording gap: 10 s.','Архив этой камеры закончился.':'End of this camera’s archive.',
        'В выбранное время запись отсутствует.':'No recording at the selected time.', 'Мультиэкран':'Multiview',
        'ID хранилища отсутствует или не совпадает':'Storage ID missing or incorrect',
        'Видеопоток доступен h264 128 × 96':'Video stream readable h264 128 × 96',
    }
    def ui(text):
        return text if language == 'ru' else labels.get(text, text)

    root = Path(__file__).resolve().parents[1] / 'frontend' / 'dist'
    assert (root / 'index.html').exists(), 'Run npm run build --prefix frontend first'
    encoders = subprocess.run(['ffmpeg', '-hide_banner', '-encoders'], capture_output=True, check=True).stdout
    encoder = 'libx264' if b'libx264' in encoders else 'libopenh264'
    clip = tmp_path / 'clip.mp4'
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=64x64:rate=5', '-t', '30', '-c:v', encoder, '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(clip)], check=True)

    async def exercise():
        async def capture(name):
            directory = os.getenv('NVR_SCREENSHOT_DIR')
            if directory:
                destination = Path(directory) / language
                destination.mkdir(parents=True,exist_ok=True)
                await page.screenshot(path=str(destination / f'{name}.png'),full_page=True)
        logged_in = False
        saved_camera = None
        saved_marker = None
        layout_gets = 0
        layout_saves = []
        archive_offsets = []
        adjacent_requests = []
        cameras = [{'id': i, 'name': f'Camera {i}', 'rtsp_url': 'rtsp://camera/…', 'substream_url': None, 'enabled': True, 'recording_enabled': i != 2, 'recording_destination': 'nas', 'retention_days': 7, 'description': '', 'has_credentials': False, 'recording_schedule': None} for i in (1, 2, 3, 4)]
        first = {'id': 1, 'camera_id': 1, 'started_at': '2026-10-05T23:59:00+00:00', 'ended_at': '2026-10-06T00:00:00+00:00', 'size_bytes': 2048}
        second = {'id': 2, 'camera_id': 1, 'started_at': '2026-10-06T00:00:10+00:00', 'ended_at': '2026-10-06T00:01:00+00:00', 'size_bytes': 2048}
        cameras[0].update(description='Saved description',retention_days=30,recording_schedule={'timezone':'UTC','windows':[{'days':list(range(7)),'start':'09:00','end':'17:00'}]})
        async def route(handler):
            nonlocal logged_in, saved_camera, saved_marker, layout_gets
            request = handler.request
            url = urlsplit(request.url)
            path = url.path
            query = parse_qs(url.query)
            if path == '/reader.js':
                await handler.fulfill(content_type='application/javascript', body='window.MediaMTXWebRTCReader=class {close(){}};')
                return
            if path == '/api/language':
                await handler.fulfill(json={'default_language':'en'})
                return
            if path.startswith('/api/'):
                if path == '/api/login':
                    logged_in = request.post_data_json['password'] == 'correct'
                    await handler.fulfill(status=200 if logged_in else 401, json={'ok': logged_in})
                    return
                if not logged_in:
                    await handler.fulfill(status=401, json={'detail': 'Authentication required'})
                    return
                if path == '/api/cameras':
                    result = cameras
                elif path == '/api/cameras/1/edit':
                    result = {**cameras[0],'rtsp_url':'rtsp://camera/main','substream_url':'rtsp://camera/small','username':'saved-user','has_credentials':True}
                elif path == '/api/cameras/1' and request.method == 'PATCH':
                    saved_camera = request.post_data_json
                    cameras[0].update(saved_camera)
                    result = cameras[0]
                elif path.endswith('/check'):
                    result = {'ok': True, 'message': 'Video stream readable', 'video': {'codec_name': 'h264', 'width': 128, 'height': 96}, 'browser_compatibility': 'likely'}
                elif path == '/api/config':
                    result = {'webrtc_port': 8889}
                elif path == '/api/dashboard':
                    result = {'cameras': 4, 'online': 3, 'recording': 2, 'writing': 1, 'errors': [], 'storage': {'free_bytes': 1024**3, 'destinations': [{'name':'nas','ready':bool(saved_marker),'reason':'ok' if saved_marker else 'identity_missing'}]}}
                elif path == '/api/notifications/status':
                    result = {'enabled': True, 'pending': 1, 'failed': 0, 'last_delivery': None}
                elif path.endswith('/status'):
                    cid = int(path.split('/')[3])
                    connectivity = 'OFFLINE' if cid == 3 else 'ONLINE'
                    result = {'online':cid != 3,'state':connectivity,'connectivity_state':connectivity,'recording_expected':cid != 2,'recording_health':{1:'WRITING',2:'PAUSED',3:'RECONNECTING',4:'STALLED'}[cid],'last_progress_at':first['started_at'] if cid==1 else None,'latest_segment':first}
                elif path == '/api/storage/destinations':
                    result = [{'name': 'nas', 'expected_marker': saved_marker, 'available': bool(saved_marker), 'writable': bool(saved_marker), 'ready': bool(saved_marker), 'free_bytes': 1024**3, 'total_bytes': 2*1024**3, 'reason': 'ok' if saved_marker else 'identity_missing'}]
                elif path == '/api/storage/destinations/nas/protection':
                    assert request.post_data_json['action'] in ('create','use_existing')
                    if request.post_data_json['action']=='create' and saved_marker:
                        await handler.fulfill(status=409,json={'detail':'Protection ID already exists; use existing ID'})
                        return
                    saved_marker = 'generated-id'
                    result = {'name':'nas','expected_marker':saved_marker,'ready':True}
                elif path == '/api/storage/destinations/nas':
                    saved_marker = request.post_data_json['expected_marker']
                    result = {'ready': True}
                elif path == '/api/layout':
                    if request.method == 'PUT':
                        result = request.post_data_json
                        if not layout_saves:
                            await asyncio.sleep(.2)
                        layout_saves.append(result)
                    else:
                        layout_gets += 1
                        result = {'columns': 2, 'tiles': [{'camera_id': 1, 'x': 0, 'y': 0, 'w': 1, 'h': 1}]}
                elif path == '/api/time/day':
                    result = archive_day(query['date'][0])
                elif path == '/api/time/resolve':
                    body=request.post_data_json
                    stamp=f"{body['date']}T{body['time']}Z"
                    result={'timezone':'UTC','instants':[{'time':stamp,'local':stamp}]}
                elif path == '/api/recordings/timelines':
                    body=request.post_data_json
                    result={'cameras':{str(cid):{'intervals':[{'start':first['started_at'],'end':first['ended_at']}],'gaps':[]} for cid in body['camera_ids']}}
                elif path == '/api/recordings/resolve':
                    body=request.post_data_json
                    target=datetime.fromisoformat(body['time'])
                    values={}
                    for cid in body['camera_ids']:
                        chosen=next((record for record in (first,second) if datetime.fromisoformat(record['started_at'])<=target<datetime.fromisoformat(record['ended_at'])),None)
                        upcoming=next((record for record in (first,second) if datetime.fromisoformat(record['started_at'])>target),None)
                        values[str(cid)]={'segment':chosen,'next_segment':upcoming,'seek_seconds':(target-datetime.fromisoformat(chosen['started_at'])).total_seconds() if chosen else 0}
                    result={'time':body['time'],'cameras':values}
                elif path == '/api/recordings/cleanup/query':
                    result = {'total':0,'recordings':[]}
                elif path == '/api/recordings':
                    offset = int(query.get('offset', ['0'])[0])
                    archive_offsets.append(offset)
                    assert query['limit'] == ['200']
                    result = [dict(first, id=i+1) for i in range(offset, min(offset+200, 201))]
                elif path == '/api/recordings/timeline':
                    result = {'intervals': [{'start': first['started_at'], 'end': first['ended_at']}], 'gaps': []}
                elif path == '/api/recordings/at':
                    assert query['camera_id'] == ['1']
                    stamp = datetime.fromisoformat(query['time'][0])
                    if stamp.hour != 23:
                        await handler.fulfill(status=404, json={'detail': 'No recording at this time'})
                        return
                    assert stamp.utcoffset().total_seconds() == 0
                    result = {'segment': first, 'seek_seconds': 10}
                elif path.endswith('/adjacent'):
                    adjacent_requests.append((path, query['direction'][0]))
                    result = {'segment': second, 'seek_seconds': 0, 'gap_seconds': 10} if path == '/api/recordings/1/adjacent' else {'segment': None, 'seek_seconds': 0, 'gap_seconds': 0}
                elif path in ('/api/recordings/1', '/api/recordings/2'):
                    await handler.fulfill(body=clip.read_bytes(), content_type='video/mp4', headers={'Accept-Ranges':'bytes'})
                    return
                else:
                    raise AssertionError(f'Unexpected request {request.method} {path}')
                await handler.fulfill(json=result)
                return
            asset = root / path.lstrip('/') if path != '/' else root / 'index.html'
            await handler.fulfill(body=asset.read_bytes(), content_type=mimetypes.guess_type(asset.name)[0] or 'application/octet-stream')

        async with async_playwright() as playwright:
            executable = os.getenv('NVR_CHROME_EXECUTABLE')
            browser = await playwright.chromium.launch(executable_path=executable or None, headless=True, args=['--no-sandbox'])
            try:
                context = await browser.new_context(timezone_id='America/New_York')
                page = await context.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                await page.route('http://nvr.test/**', route)
                await page.goto('http://nvr.test/')
                await expect(page.locator('html')).to_have_attribute('lang','en')
                await expect(page.get_by_role('button',name='Sign in',exact=True)).to_be_visible()
                await expect(page.locator('aside')).to_have_count(0)
                await expect(page.locator('.time-settings')).to_have_count(0)
                await page.locator('input[name=user]').fill('admin')
                await page.locator('input[name=pass]').fill('wrong')
                await page.get_by_role('button', name='Sign in', exact=True).click()
                await expect(page.locator('input[name=pass]')).to_be_visible()
                await page.locator('input[name=pass]').fill('correct')
                await page.get_by_role('button', name='Sign in', exact=True).click()
                await expect(page.locator('input[name=pass]')).to_have_count(0)
                await expect(page.locator('.time-settings')).to_have_count(0)
                await page.get_by_role('link',name='Account').click()
                if language=='ru':
                    await page.get_by_label('Language',exact=True).select_option('ru')
                await expect(page.locator('html')).to_have_attribute('lang',language)
                await page.get_by_role('button',name='Overview' if language=='en' else 'Обзор',exact=True).click()
                await expect(page.locator('.stats article').filter(has_text=ui('Пишут')).locator('strong')).to_have_text('1')
                paused_row = page.locator('section .row').filter(has_text='Camera 2')
                await expect(paused_row.get_by_label(ui('Подключение'))).to_have_text(ui('В сети'))
                await expect(paused_row.get_by_label(ui('Подключение'))).to_have_class('state good')
                await expect(paused_row.get_by_label(ui('Запись'),exact=True)).to_have_text(ui('Запись приостановлена'))
                await expect(paused_row.get_by_label(ui('Запись'),exact=True)).to_have_class('state neutral')
                offline_row = page.locator('section .row').filter(has_text='Camera 3')
                await expect(offline_row.get_by_label(ui('Подключение'))).to_have_text(ui('Не в сети'))
                await expect(offline_row.get_by_label(ui('Подключение'))).to_have_class('state bad')
                stalled_row = page.locator('section .row').filter(has_text='Camera 4')
                await expect(stalled_row.get_by_label(ui('Подключение'))).to_have_class('state good')
                await expect(stalled_row.get_by_label(ui('Запись'),exact=True)).to_have_class('state bad')
                rows = page.locator('.overview-row')
                assert await rows.count() == 4
                positions = await rows.evaluate_all('(rows) => rows.map(row=>Array.from(row.children).map(cell=>cell.getBoundingClientRect().x))')
                assert all(len(row)==5 for row in positions)
                assert all(abs(positions[0][column]-row[column])<1 for row in positions[1:] for column in (1,2,3,4))
                await expect(paused_row).to_contain_text('No active recording' if language=='en' else 'Нет активной записи')
                await expect(page.get_by_role('alert').filter(has_text='nas')).to_contain_text(ui('ID хранилища отсутствует или не совпадает'))
                await capture('overview-desktop')
                await page.set_viewport_size({'width':390,'height':844})
                assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                await expect(paused_row.get_by_role('button',name='Watch' if language=='en' else 'Смотреть',exact=True)).to_be_visible()
                await capture('overview-mobile')
                await page.set_viewport_size({'width':900,'height':768})
                assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                await capture('overview-tablet')
                await page.set_viewport_size({'width':1280,'height':720})
                await page.get_by_role('button', name=ui('Камеры'), exact=True).click()
                await page.get_by_role('button', name=ui('Проверить'), exact=True).first.click()
                await expect(page.locator('.camera').first).to_contain_text(ui('Видеопоток доступен h264 128 × 96'))
                await page.get_by_role('button', name=ui('Править'), exact=True).first.click()
                await expect(page.get_by_label('RTSP URL',exact=True)).to_have_value('rtsp://camera/main')
                await expect(page.get_by_label('Substream URL',exact=True)).to_have_value('rtsp://camera/small')
                await expect(page.get_by_label(ui('Логин'),exact=True)).to_have_value('saved-user')
                await expect(page.get_by_label(ui('Пароль'),exact=True)).to_have_value('')
                await expect(page.get_by_label(ui('Описание'),exact=True)).to_have_value('Saved description')
                await expect(page.get_by_text('Schedule timezone' if language=='en' else 'Часовой пояс расписания')).to_contain_text('UTC')
                await expect(page.get_by_label(ui('Начало'),exact=True)).to_have_value('09:00')
                await capture('camera-edit')
                await page.set_viewport_size({'width':390,'height':844})
                assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                await capture('camera-edit-mobile')
                await page.set_viewport_size({'width':1280,'height':720})
                await page.get_by_label('RTSP URL',exact=True).fill('rtsp://camera/edited')
                await page.get_by_label(ui('Ограничить время записи')).check()
                await page.get_by_label(ui('Начало'), exact=True).fill('22:00')
                await page.get_by_label(ui('Конец (24:00 — конец суток)')).fill('06:00')
                await page.get_by_role('button', name=ui('Сохранить'), exact=True).click()
                await expect(page.get_by_role('button', name=ui('Добавить'), exact=True)).to_be_visible()
                dialogs=[]
                async def reject_delete(dialog):
                    dialogs.append(dialog.message)
                    await dialog.dismiss()
                page.once('dialog',reject_delete)
                await page.get_by_role('button',name='Delete' if language=='en' else 'Удалить',exact=True).first.click()
                assert 'Camera 1' in dialogs[0] and ('Recordings' in dialogs[0] or 'Архив' in dialogs[0])
                assert saved_camera['rtsp_url']=='rtsp://camera/edited'
                assert 'password' not in saved_camera and 'username' not in saved_camera and 'description' not in saved_camera
                assert saved_camera['recording_schedule']['timezone'] == 'UTC'
                assert saved_camera['recording_schedule']['windows'] == [{'days': list(range(7)), 'start': '22:00', 'end': '06:00'}]
                await page.get_by_role('button', name=ui('Хранилище'), exact=True).click()
                await expect(page.locator('.destination')).to_contain_text(ui('ID хранилища отсутствует или не совпадает'))
                page.on('dialog',lambda dialog:dialog.accept())
                await page.get_by_role('button',name=ui('Создать ID защиты'),exact=True).click()
                await expect(page.locator('.destination')).to_contain_text(ui('Готово к записи'))
                assert saved_marker=='generated-id'
                await page.get_by_role('button',name=ui('Использовать существующий ID'),exact=True).click()
                await page.get_by_text(ui('Ручная настройка'),exact=True).click()
                await page.get_by_label(ui('ID хранилища nas')).fill('nas-disk')
                await page.get_by_role('button', name=ui('Сохранить защиту'), exact=True).click()
                await expect(page.locator('.destination')).to_contain_text(ui('Готово к записи'))
                assert saved_marker == 'nas-disk'
                await capture('storage-desktop')
                await page.set_viewport_size({'width':390,'height':844})
                assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                await capture('storage-desktop-mobile')
                await page.set_viewport_size({'width':1280,'height':720})
                await page.get_by_role('button', name=ui('Архив'), exact=True).click()
                await expect(page.get_by_label(ui('Камера архива'))).to_have_value('')
                await page.get_by_label('Archive date' if language=='en' else 'Дата архива',exact=True).fill('2026-10-05')
                await page.get_by_label(ui('Камера архива')).select_option('1')
                await expect(page.get_by_label(ui('Шкала записи'))).to_be_visible()
                await expect(page.get_by_role('button', name=ui('Смотреть'), exact=True)).to_have_count(200)
                await page.get_by_role('button', name=ui('Загрузить ещё'), exact=True).click()
                await expect(page.get_by_role('button', name=ui('Смотреть'), exact=True)).to_have_count(201)
                assert 200 in archive_offsets
                await page.get_by_label('Archive time' if language=='en' else 'Время архива',exact=True).fill('23:59:10')
                await page.get_by_role('button', name='Play selected cameras' if language=='en' else 'Смотреть выбранные камеры', exact=True).click()
                await capture('archive-desktop')
                await page.set_viewport_size({'width':390,'height':844})
                assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                await capture('archive-desktop-mobile')
                await page.set_viewport_size({'width':1280,'height':720})
                await page.wait_for_function('document.querySelector("section video")?.currentTime >= 10')
                timeline = page.get_by_role('slider',name=ui('Шкала записи'))
                await timeline.focus()
                await page.get_by_role('button',name='Pause' if language=='en' else 'Пауза',exact=True).click()
                await expect(page.locator('.archive-camera-head .snapshot-control')).to_have_count(1)
                await expect(page.locator('.archive-camera-head .fullscreen-control')).to_have_count(1)
                await expect(page.locator('.archive-camera').get_by_role('button',name='Focus' if language=='en' else 'Увеличить',exact=True)).to_have_count(0)
                await fullscreen_roundtrip(page,page.locator('.recorded-video'),language)
                assert int(await timeline.get_attribute('aria-valuenow'))>=86350
                await timeline.press('Home')
                await timeline.press('ArrowRight')
                await expect(timeline).to_have_attribute('aria-valuenow','300')
                player = page.locator('section video')
                await page.get_by_role('button',name='Next →' if language=='en' else 'Следующий →',exact=True).click()
                await expect(player).to_have_attribute('src', '/api/recordings/2')
                await expect(page.locator('.feedback[role=status]').filter(has_text=ui('Пробел в записи: 10 с.'))).to_contain_text(ui('Пробел в записи: 10 с.'))
                assert adjacent_requests[-1] == ('/api/recordings/1/adjacent', 'next')
                await page.get_by_role('button',name='Next →' if language=='en' else 'Следующий →',exact=True).click()
                await expect(page.get_by_role('status').filter(has_text=ui('Архив этой камеры закончился.'))).to_contain_text(ui('Архив этой камеры закончился.'))
                await page.get_by_label('Archive date' if language=='en' else 'Дата архива',exact=True).fill('2026-10-05')
                await page.get_by_label('Archive time' if language=='en' else 'Время архива',exact=True).fill('12:00:00')
                await page.get_by_role('button', name='Play selected cameras' if language=='en' else 'Смотреть выбранные камеры', exact=True).click()
                await expect(player).to_have_count(0)
                await expect(page.get_by_role('status').filter(has_text=ui('В выбранное время запись отсутствует.'))).to_contain_text(ui('В выбранное время запись отсутствует.'))
                await page.get_by_role('button', name=ui('Мультиэкран'), exact=True).click()
                assert await page.locator('.modal').count() == 0
                async with page.expect_response(lambda response:urlsplit(response.url).path=='/api/layout' and response.request.method=='PUT'):
                    await page.locator('.toolbar select').nth(1).select_option('2')
                await expect(page.locator('.tile')).to_have_count(2)
                saves_before=len(layout_saves)
                await fullscreen_roundtrip(page,page.locator('.tile').filter(has_text='Camera 1').locator('.stream'),language)
                await fullscreen_roundtrip(page,page.locator('.tile').filter(has_text='Camera 2').locator('.stream'),language)
                assert len(layout_saves)==saves_before
                await expect(page.locator('.tile')).to_have_count(2)
                await page.locator('.tile').filter(has_text='Camera 1').get_by_role('button', name='Remove Camera 1 from view' if language=='en' else 'Убрать Camera 1 с экрана', exact=True).click()
                await page.wait_for_timeout(500)
                assert [tile['camera_id'] for tile in layout_saves[-1]['tiles']] == [2]
                previous_gets = layout_gets
                await page.wait_for_timeout(10500)
                assert layout_gets == previous_gets, 'Polling must not overwrite layout'
                await expect(page.locator('.tile')).to_have_count(1)
                await capture('multiview-desktop')
                await page.locator('.tile').get_by_role('button',name='Main stream ↗' if language=='en' else 'Основной поток ↗',exact=True).click()
                await expect(page.get_by_role('dialog',name='Camera 2',exact=True)).to_be_visible()
                await fullscreen_roundtrip(page,page.locator('dialog .stream'),language)
                await capture('live-dialog')
                await page.keyboard.press('Escape')
                await expect(page.get_by_role('dialog')).to_have_count(0)
                await expect(page.locator('.tile').get_by_role('button',name='Main stream ↗' if language=='en' else 'Основной поток ↗',exact=True)).to_be_focused()
                await page.set_viewport_size({'width':390,'height':844})
                await page.wait_for_function('document.documentElement.scrollWidth<=innerWidth')
                await expect(page.locator('.toolbar .help')).to_contain_text('On small screens, cameras stack vertically.' if language=='en' else 'На небольших экранах камеры расположены вертикально.')
                await capture('multiview-mobile')
                await page.set_viewport_size({'width':1280,'height':720})
                assert not errors, errors
                await page.reload()
                await expect(page.locator('html')).to_have_attribute('lang',language)
                await expect(page.get_by_role('button',name=ui('Камеры'),exact=True)).to_be_visible()
            finally:
                await browser.close()
    asyncio.run(exercise())


@pytest.mark.browser
@pytest.mark.skipif(os.getenv('NVR_RUN_BROWSER') != '1', reason='Build frontend and set NVR_RUN_BROWSER=1')
def test_real_session_login_without_browser_basic_challenge(tmp_path, monkeypatch):
    """Chromium uses the actual HTTP server, auth dependency and session cookie."""
    import socket
    import threading
    import time
    import uvicorn
    from playwright.async_api import async_playwright, expect
    from test_nvr import setup, FakeGateway

    main, _ = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(main, 'MediaGateway', FakeGateway)
    monkeypatch.setattr(main, 'DEFAULT_LANGUAGE', 'en')
    monkeypatch.setenv('COOKIE_SECURE', 'false')
    main.mount_ui(main.app, Path(__file__).resolve().parents[1] / 'frontend' / 'dist')
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        server = uvicorn.Server(uvicorn.Config(main.app, log_level='error'))
        worker = threading.Thread(target=server.run, kwargs={'sockets':[listener]}, daemon=True)
        worker.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started and worker.is_alive() and time.monotonic() < deadline:
                time.sleep(.01)
            assert server.started, 'Isolated backend did not start'
            base = f'http://127.0.0.1:{listener.getsockname()[1]}'

            async def exercise():
                async with async_playwright() as playwright:
                    browser = await playwright.chromium.launch(executable_path=os.getenv('NVR_CHROME_EXECUTABLE') or None, headless=True, args=['--no-sandbox'])
                    try:
                        context = await browser.new_context()
                        page = await context.new_page()
                        responses = []
                        page.on('response', lambda response: responses.append(response))
                        login = page.get_by_role('heading', name='Sign in to your NVR')
                        for path in ('/', '/overview', '/multiview', '/archive', '/settings', '/account'):
                            await page.goto(base + path)
                            await expect(login).to_be_visible()
                            await expect(page.locator('aside')).to_have_count(0)
                        result = await context.request.get(base + '/api/config')
                        assert result.status == 401
                        assert 'www-authenticate' not in result.headers
                        await page.get_by_label('Username', exact=True).fill('admin')
                        await page.get_by_label('Password', exact=True).fill('secret')
                        await page.get_by_role('button', name='Sign in', exact=True).click()
                        await expect(page.get_by_role('button', name='Log out', exact=True)).to_be_visible()
                        assert (await context.request.get(base + '/api/config')).status == 200
                        await page.reload()
                        await expect(page.get_by_role('button', name='Log out', exact=True)).to_be_visible()
                        await page.get_by_role('button', name='Log out', exact=True).click()
                        await expect(login).to_be_visible()
                        assert not any(cookie['name'] == 'nvr_session' for cookie in await context.cookies())
                        for path in ('/api/config', '/api/cameras'):
                            result = await context.request.get(base + path)
                            assert result.status == 401
                            assert 'www-authenticate' not in result.headers
                        await page.reload()
                        await expect(login).to_be_visible()
                        await page.goto(base + '/archive')
                        await expect(login).to_be_visible()
                        # A separate browser context also starts unauthenticated.
                        fresh = await browser.new_context()
                        fresh_page = await fresh.new_page()
                        await fresh_page.goto(base)
                        await expect(fresh_page.get_by_role('heading', name='Sign in to your NVR')).to_be_visible()
                        assert any(response.status == 401 for response in responses)
                        for response in responses:
                            assert 'www-authenticate' not in await response.all_headers(), response.url
                    finally:
                        await browser.close()
            asyncio.run(exercise())
        finally:
            server.should_exit = True
            worker.join(timeout=10)
            assert not worker.is_alive(), 'Isolated backend did not stop'


@pytest.mark.browser
@pytest.mark.skipif(os.getenv('NVR_RUN_BROWSER') != '1', reason='Build frontend and set NVR_RUN_BROWSER=1')
@pytest.mark.parametrize('configured,saved,blocked,expected', [
    ('ru', None, False, 'ru'), ('ru', 'en', False, 'en'), ('en', 'ru', False, 'ru'),
    ('ru', 'invalid', False, 'ru'), ('fr', None, False, 'en'),
    ('unavailable', None, False, 'en'), ('ru', None, True, 'ru'),
])
def test_initial_language_precedence_and_fallback(configured, saved, blocked, expected):
    from playwright.async_api import async_playwright, expect
    root = Path(__file__).resolve().parents[1] / 'frontend' / 'dist'
    async def exercise():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(executable_path=os.getenv('NVR_CHROME_EXECUTABLE') or None, headless=True, args=['--no-sandbox'])
            try:
                context = await browser.new_context(locale='ru-RU')
                page = await context.new_page()
                if blocked:
                    await page.add_init_script("Object.defineProperty(window,'localStorage',{get(){throw new Error('Storage disabled')}})")
                elif saved:
                    import json
                    await page.add_init_script(f"localStorage.setItem('nvr-language',{json.dumps(saved)})")
                async def route(handler):
                    path = urlsplit(handler.request.url).path
                    if path == '/api/language':
                        await handler.fulfill(status=503 if configured == 'unavailable' else 200, json={'default_language':configured})
                    elif path == '/api/cameras':
                        await handler.fulfill(json=[])
                    elif path == '/api/config':
                        await handler.fulfill(json={'webrtc_port':8889})
                    elif path == '/api/dashboard':
                        await handler.fulfill(json={'cameras':0,'online':0,'writing':0,'errors':[],'storage':{'free_bytes':None}})
                    elif path == '/api/layout':
                        await handler.fulfill(json={'columns':2,'tiles':[]})
                    else:
                        asset = root/path.lstrip('/') if path != '/' else root/'index.html'
                        await handler.fulfill(body=asset.read_bytes(), content_type=mimetypes.guess_type(asset.name)[0] or 'application/octet-stream')
                await page.route('http://nvr.test/**', route)
                await page.goto('http://nvr.test/')
                await expect(page.locator('html')).to_have_attribute('lang',expected)
                await expect(page.get_by_role('heading',name='Обзор' if expected=='ru' else 'Overview',exact=True)).to_be_visible()
                await page.get_by_role('link',name='Аккаунт' if expected=='ru' else 'Account').click()
                await page.locator('.language-switch select').select_option('en' if expected=='ru' else 'ru')
                await expect(page.locator('html')).to_have_attribute('lang','en' if expected=='ru' else 'ru')
            finally:
                await browser.close()
    asyncio.run(exercise())


@pytest.mark.browser
@pytest.mark.skipif(os.getenv('NVR_RUN_BROWSER') != '1', reason='Build frontend and set NVR_RUN_BROWSER=1')
@pytest.mark.parametrize('language',['en','ru'])
def test_empty_states_camera_creation_and_pending_storage(language):
    from playwright.async_api import async_playwright, expect
    root = Path(__file__).resolve().parents[1]/'frontend'/'dist'
    async def exercise():
        cameras=[]
        saved=[]
        async with async_playwright() as playwright:
            browser=await playwright.chromium.launch(executable_path=os.getenv('NVR_CHROME_EXECUTABLE') or None,headless=True,args=['--no-sandbox'])
            try:
                page=await browser.new_page(viewport={'width':390,'height':844})
                async def route(handler):
                    request=handler.request
                    path=urlsplit(request.url).path
                    if path=='/api/language':
                        result={'default_language':language}
                    elif path=='/api/cameras':
                        if request.method=='POST':
                            fields=request.post_data_json
                            saved.append(fields)
                            cameras.append({**fields,'id':1,'has_credentials':False})
                            result=cameras[0]
                        else:
                            result=cameras
                    elif path=='/api/config':
                        result={'webrtc_port':8889}
                    elif path=='/api/dashboard':
                        result={'cameras':len(cameras),'online':0,'writing':0,'errors':[],'storage':{'free_bytes':None}}
                    elif path=='/api/layout':
                        result={'columns':2,'tiles':[]}
                    elif path=='/api/cameras/1/status':
                        result={'connectivity_state':'UNKNOWN','recording_health':'PAUSED','recording_expected':False}
                    elif path=='/api/recordings/cleanup/query':
                        result={'total':0,'recordings':[]}
                    elif path=='/api/storage/destinations':
                        result=[{'name':'default','expected_marker':None,'mounted':False,'available':False,'writable':False,'ready':False,'free_bytes':None,'total_bytes':None,'reason':'check_pending'}]
                    elif path=='/api/notifications/status':
                        result={'enabled':False,'pending':0,'failed':0,'last_delivery':None}
                    elif path=='/api/storage/destinations/default/protection':
                        await asyncio.sleep(.6)
                        await handler.fulfill(status=400,json={'detail':'Storage unavailable or protection operation failed'})
                        return
                    elif path=='/api/time/day':
                        result=archive_day(parse_qs(urlsplit(request.url).query)['date'][0])
                    elif path=='/api/recordings/timelines':
                        result={'cameras':{'1':{'intervals':[],'gaps':[]}}}
                    elif path.startswith('/api/recordings'):
                        result=[]
                    else:
                        asset=root/path.lstrip('/') if path!='/' else root/'index.html'
                        await handler.fulfill(body=asset.read_bytes(),content_type=mimetypes.guess_type(asset.name)[0] or 'application/octet-stream')
                        return
                    await handler.fulfill(json=result)
                await page.route('http://nvr.test/**',route)
                await page.goto('http://nvr.test/')
                await expect(page.get_by_text('No cameras configured' if language=='en' else 'Камеры не настроены',exact=True)).to_be_visible()
                await page.get_by_role('button',name='Multiview' if language=='en' else 'Мультиэкран',exact=True).click()
                await expect(page.get_by_text('No cameras in this view' if language=='en' else 'На экране нет камер',exact=True)).to_be_visible()
                await page.get_by_role('button',name='Cameras' if language=='en' else 'Камеры',exact=True).click()
                await expect(page.locator('.advanced-settings')).not_to_have_attribute('open','')
                await page.get_by_label('Name' if language=='en' else 'Название',exact=True).fill('Front door')
                await page.get_by_label('RTSP URL',exact=True).fill('rtsp://camera/live')
                await page.locator('.form').get_by_label('Recording' if language=='en' else 'Запись',exact=True).uncheck()
                await page.get_by_role('button',name='Add' if language=='en' else 'Добавить',exact=True).click()
                await expect(page.get_by_text('Camera saved.' if language=='en' else 'Камера сохранена.',exact=True)).to_be_visible()
                assert saved[0]['name']=='Front door' and saved[0]['recording_enabled'] is False
                await expect(page.locator('.camera')).to_contain_text('Recording paused' if language=='en' else 'Запись приостановлена')
                await page.get_by_role('button',name='Storage' if language=='en' else 'Хранилище',exact=True).click()
                await expect(page.locator('.destination>.state').first).to_have_class('state neutral')
                await expect(page.get_by_text('Not protected' if language=='en' else 'Без защиты',exact=True)).to_have_class('state neutral')
                page.on('dialog',lambda dialog:dialog.accept())
                await page.get_by_role('button',name='Create protection ID' if language=='en' else 'Создать ID защиты',exact=True).click()
                await expect(page.get_by_text('Updating protection…' if language=='en' else 'Обновление защиты…',exact=True)).to_be_visible()
                await expect(page.locator('.destination').get_by_role('alert')).to_contain_text('Storage unavailable' if language=='en' else 'Хранилище недоступно')
                await expect(page.get_by_role('button',name='Create protection ID' if language=='en' else 'Создать ID защиты',exact=True)).to_be_enabled()
                await page.get_by_role('button',name='Archive' if language=='en' else 'Архив',exact=True).click()
                await expect(page.get_by_text('No recordings for this date' if language=='en' else 'За эту дату записей нет',exact=True)).to_be_visible()
                await page.get_by_label('Archive date' if language=='en' else 'Дата архива',exact=True).fill('')
                await expect(page.get_by_text('Choose an archive date' if language=='en' else 'Выберите дату архива',exact=True)).to_be_visible()
                await expect(page.get_by_role('alert')).to_have_count(0)
                assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            finally:
                await browser.close()
    asyncio.run(exercise())


@pytest.mark.browser
@pytest.mark.skipif(os.getenv('NVR_RUN_BROWSER') != '1', reason='Build frontend and set NVR_RUN_BROWSER=1')
@pytest.mark.parametrize('language',['en','ru'])
def test_installation_timezone_and_synchronized_archive(tmp_path,monkeypatch,language):
    """Real API/SQLite and native media; the browser timezone deliberately differs."""
    import shutil
    from fastapi.testclient import TestClient
    from playwright.async_api import async_playwright, expect
    from test_nvr import setup, FakeGateway
    main,video=setup(tmp_path,monkeypatch)
    monkeypatch.setenv('APP_TIMEZONE','UTC')
    monkeypatch.setenv('DEFAULT_LANGUAGE',language)
    monkeypatch.setattr(main,'DEFAULT_LANGUAGE',language)
    monkeypatch.setattr(main,'MIN_FREE',0)
    monkeypatch.setattr(video,'MIN_FREE',0)
    monkeypatch.setattr(main,'MediaGateway',FakeGateway)
    root=Path(__file__).resolve().parents[1]/'frontend'/'dist'
    encoders=subprocess.run(['ffmpeg','-hide_banner','-encoders'],capture_output=True,check=True).stdout
    encoder='libx264' if b'libx264' in encoders else 'libopenh264'
    clips={}
    for duration in (8,60,70):
        path=tmp_path/f'clip-{duration}.mp4'
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i','testsrc2=size=64x64:rate=5','-t',str(duration),'-c:v',encoder,'-pix_fmt','yuv420p','-movflags','+faststart',str(path)],check=True)
        clips[duration]=path
    def ui(en,ru):
        return ru if language=='ru' else en
    with TestClient(main.app) as client:
        assert client.post('/api/login', json={'username':'admin','password':'secret'}).status_code == 200
        cameras=[]
        for index in range(4):
            response=client.post('/api/cameras',json={'name':f'Camera {index+1}','rtsp_url':'rtsp://camera/live','recording_enabled':False})
            assert response.status_code==201,response.text
            cameras.append(response.json()['id'])
        def add(camera,stamp,duration):
            folder=video.ROOT/'default'/str(camera)
            folder.mkdir(parents=True,exist_ok=True)
            path=folder/f'{stamp}.mp4'
            shutil.copyfile(clips[duration],path)
            start=datetime.fromisoformat(stamp)
            with main.db() as connection:
                return connection.execute('INSERT INTO segments(camera_id,path,started_at,ended_at,size_bytes) VALUES(?,?,?,?,?)',
                    (camera,str(path),start.isoformat(),(start+timedelta(seconds=duration)).isoformat(),path.stat().st_size)).lastrowid
        first=add(cameras[0],'2026-10-06T11:32:00+00:00',8)
        second=add(cameras[0],'2026-10-06T11:32:08+00:00',60)
        other=add(cameras[1],'2026-10-06T11:31:55+00:00',70)
        resumes=add(cameras[2],'2026-10-06T11:32:04+00:00',60)
        with main.db() as connection:
            original=[tuple(row) for row in connection.execute('SELECT * FROM segments ORDER BY id')]
        resolutions=[]
        recovery_failure=False
        async def exercise():
            async with async_playwright() as playwright:
                browser=await playwright.chromium.launch(executable_path=os.getenv('NVR_CHROME_EXECUTABLE') or None,headless=True,args=['--no-sandbox'])
                try:
                    context=await browser.new_context(timezone_id='America/Los_Angeles',viewport={'width':1280,'height':900})
                    await context.add_init_script('const realNow=Date.now;Date.now=()=>realNow()+4*86400000')
                    page=await context.new_page()
                    errors=[]
                    page.on('pageerror',lambda error:(errors.append(str(error)),print(f'Browser error: {error}')))
                    async def route(handler):
                        nonlocal recovery_failure
                        request=handler.request
                        url=urlsplit(request.url)
                        if url.path.startswith('/api/'):
                            body=request.post_data_json if request.method in ('POST','PUT','PATCH') else None
                            if url.path=='/api/recordings/resolve':
                                resolutions.append(body)
                                if body['camera_ids']==[cameras[2]] and not recovery_failure:
                                    recovery_failure=True
                                    await handler.fulfill(status=503,json={'detail':'Temporary lookup outage'})
                                    return
                            headers={'range':request.headers['range']} if 'range' in request.headers else {}
                            response=await asyncio.to_thread(client.request,request.method,url.path+('?' + url.query if url.query else ''),json=body,headers=headers)
                            await handler.fulfill(status=response.status_code,headers=dict(response.headers),body=response.content)
                        else:
                            asset=root/(url.path.lstrip('/') if url.path!='/' else 'index.html')
                            await handler.fulfill(body=asset.read_bytes(),content_type=mimetypes.guess_type(asset.name)[0] or 'application/octet-stream')
                    await page.route('http://nvr.test/**',route)
                    await page.goto('http://nvr.test/')
                    await page.get_by_role('link',name=ui('Account','Аккаунт')).click()
                    settings=page.locator('.time-settings')
                    await expect(page).to_have_title('TupoNVR')
                    await expect(page.locator('aside h1')).to_have_text('▣ TupoNVR')
                    await settings.get_by_text(ui('Change timezone','Изменить часовой пояс'),exact=True).click()
                    await settings.get_by_label(ui('Timezone','Часовой пояс'),exact=True).fill('Europe/Moscow')
                    await settings.get_by_role('button',name=ui('Save timezone','Сохранить часовой пояс'),exact=True).click()
                    await expect(settings).to_contain_text(ui('Timezone saved.','Часовой пояс сохранён.'))
                    await page.get_by_role('button',name=ui('Overview','Обзор'),exact=True).click()
                    await expect(page.locator('.overview-row').first).to_contain_text('14:33:08')
                    await page.get_by_role('button',name=ui('Archive','Архив'),exact=True).click()
                    from zoneinfo import ZoneInfo
                    today=datetime.fromisoformat(client.get('/api/time').json()['now']).astimezone(ZoneInfo('Europe/Moscow')).date().isoformat()
                    await expect(page.get_by_label(ui('Archive date','Дата архива'),exact=True)).to_have_value(today)
                    await page.get_by_label(ui('Archive date','Дата архива'),exact=True).fill('2026-10-06')
                    await page.get_by_label(ui('Archive camera','Камера архива'),exact=True).select_option('all')
                    await expect(page.locator('.archive-track>strong')).to_have_count(4)
                    await page.get_by_label(ui('Archive time','Время архива'),exact=True).fill('14:32:02')
                    await page.get_by_role('button',name=ui('Play selected cameras','Смотреть выбранные камеры'),exact=True).click()
                    tile=lambda cid:page.locator(f'.archive-camera[data-camera-id="{cid}"]')
                    await expect(tile(cameras[0]).locator('video')).to_have_attribute('src',f'/api/recordings/{first}')
                    await expect(tile(cameras[1]).locator('video')).to_have_attribute('src',f'/api/recordings/{other}')
                    await expect(tile(cameras[2])).to_contain_text(ui('Footage resumes at','Запись возобновляется в'))
                    await page.get_by_role('button',name=ui('Pause','Пауза'),exact=True).click()
                    assert datetime.fromisoformat(resolutions[0]['time'])==datetime.fromisoformat('2026-10-06T11:32:02Z')
                    offsets=await page.locator('.archive-camera video').evaluate_all('(videos)=>videos.map(v=>v.currentTime)')
                    assert abs((offsets[1]-offsets[0])-5)<.8,offsets
                    assert 1.5<=offsets[0]<3.5,offsets
                    await expect(page.get_by_test_id('master-time')).to_contain_text('14:32:02')
                    assert await page.locator('.archive-camera video').evaluate_all('(videos)=>videos.every(v=>v.paused)')
                    await expect(page.locator('.archive-camera-head .snapshot-control')).to_have_count(2)
                    await expect(page.locator('.archive-camera-head .fullscreen-control')).to_have_count(2)
                    await expect(page.locator('.archive-camera').get_by_role('button',name=ui('Focus','Увеличить'),exact=True)).to_have_count(0)
                    await expect(page.locator('.archive-camera').get_by_role('button',name=ui('Return to grid','Вернуться к сетке'),exact=True)).to_have_count(0)
                    before_fullscreen=len(resolutions)
                    master_time=await page.get_by_test_id('master-time').inner_text()
                    timeline_position=await page.get_by_role('slider',name=ui('Recording timeline','Шкала записи')).get_attribute('aria-valuenow')
                    await page.get_by_label(ui('Speed','Скорость'),exact=True).select_option('2')
                    await page.locator('.archive-camera video').evaluate_all('(videos)=>{window.archiveVideos=videos}')
                    for camera in cameras[:2]:
                        await fullscreen_roundtrip(page,tile(camera).locator('.recorded-video'),language)
                    assert len(resolutions)==before_fullscreen
                    assert await page.get_by_test_id('master-time').inner_text()==master_time
                    await expect(page.get_by_role('slider',name=ui('Recording timeline','Шкала записи'))).to_have_attribute('aria-valuenow',timeline_position)
                    await expect(page.get_by_label(ui('Archive camera','Камера архива'),exact=True)).to_have_value('all')
                    await expect(page.get_by_label(ui('Speed','Скорость'),exact=True)).to_have_value('2')
                    await expect(page.get_by_role('button',name=ui('Play','Воспроизвести'),exact=True)).to_be_visible()
                    assert await page.locator('.archive-camera video').evaluate_all('(videos)=>videos.every((v,i)=>v===window.archiveVideos[i]&&v.paused&&v.playbackRate===2)')
                    await page.get_by_label(ui('Speed','Скорость'),exact=True).select_option('1')
                    await expect(page.locator('.archive-camera')).to_have_count(4)
                    assert await tile(cameras[1]).locator('video').evaluate('v=>v.paused')
                    player=tile(cameras[0]).locator('video')
                    await player.evaluate('v=>v.currentTime=0')
                    await page.wait_for_function('document.querySelector(".archive-camera video").currentTime>1.5')
                    for rate in ('0.5','2','4','1'):
                        await page.get_by_label(ui('Speed','Скорость'),exact=True).select_option(rate)
                        assert await player.evaluate('v=>v.playbackRate')==float(rate)
                    await page.get_by_role('button',name=ui('Play','Воспроизвести'),exact=True).click()
                    await fullscreen_roundtrip(page,tile(cameras[1]).locator('.recorded-video'),language)
                    await expect(tile(cameras[2])).to_contain_text(ui('Recording lookup failed','Не удалось найти запись'),timeout=10000)
                    assert await tile(cameras[1]).locator('video').evaluate('v=>!v.paused')
                    await expect(tile(cameras[2]).locator('video')).to_have_attribute('src',f'/api/recordings/{resumes}',timeout=15000)
                    await expect(tile(cameras[2]).get_by_role('alert')).to_have_count(0)
                    await expect(tile(cameras[0]).locator('video')).to_have_attribute('src',f'/api/recordings/{second}',timeout=15000)
                    await expect(tile(cameras[3])).to_contain_text(ui('No recording at this time','В указанное время записи нет'))
                    await page.get_by_role('button',name=ui('Pause','Пауза'),exact=True).click()
                    assert any(value['camera_ids']==[cameras[2]] for value in resolutions)
                    assert any(value['camera_ids']==[cameras[0]] for value in resolutions)
                    assert not any(value['camera_ids']==[cameras[3]] for value in resolutions)
                    await page.get_by_label(ui('Archive time','Время архива'),exact=True).fill('14:32:12')
                    await page.get_by_role('button',name=ui('Play selected cameras','Смотреть выбранные камеры'),exact=True).click()
                    await expect(tile(cameras[0]).locator('video')).to_have_attribute('src',f'/api/recordings/{second}')
                    await page.get_by_role('button',name=ui('Pause','Пауза'),exact=True).click()
                    values=await page.locator('.archive-camera video').evaluate_all('(videos)=>videos.map(v=>v.currentTime)')
                    assert len(values)==3 and abs(values[0]-4)<.9 and abs(values[1]-17)<.9 and abs(values[2]-8)<.9,values
                    await tile(cameras[0]).locator('video').evaluate('v=>{v.play=()=>new Promise((_,reject)=>window.cancelOldPlay=reject)}')
                    await page.get_by_role('button',name=ui('Play','Воспроизвести'),exact=True).click()
                    await page.wait_for_function('typeof window.cancelOldPlay === "function"')
                    await page.get_by_label(ui('Archive time','Время архива'),exact=True).fill('14:32:20')
                    await page.get_by_role('button',name=ui('Play selected cameras','Смотреть выбранные камеры'),exact=True).click()
                    await expect(page.get_by_role('button',name=ui('Pause','Пауза'),exact=True)).to_be_visible()
                    await page.evaluate('window.cancelOldPlay(new DOMException("Cancelled", "AbortError"))')
                    await expect(page.locator('.archive-camera').get_by_role('alert')).to_have_count(0)
                    await page.get_by_role('button',name=ui('Pause','Пауза'),exact=True).click()
                    before_refresh=len(resolutions)
                    await page.get_by_role('button',name=ui('Refresh','Обновить'),exact=True).click()
                    await page.wait_for_timeout(350)
                    assert len(resolutions)==before_refresh+1
                    assert resolutions[-1]['camera_ids']==cameras
                    await expect(page.get_by_role('button',name=ui('Play','Воспроизвести'),exact=True)).to_be_visible()
                    directory=os.getenv('NVR_SCREENSHOT_DIR')
                    if directory:
                        destination=Path(directory)/language
                        destination.mkdir(parents=True,exist_ok=True)
                        await page.screenshot(path=str(destination/'archive-synchronized.png'),full_page=True)
                    await page.set_viewport_size({'width':390,'height':844})
                    await page.wait_for_function('document.documentElement.scrollWidth<=innerWidth')
                    if directory:await page.screenshot(path=str(destination/'archive-synchronized-mobile.png'),full_page=True)
                    axis=page.get_by_role('slider',name=ui('Recording timeline','Шкала записи'))
                    box=await axis.bounding_box()
                    before=len(resolutions)
                    await page.mouse.move(box['x']+box['width']*.4,box['y']+box['height']/2)
                    await page.mouse.down()
                    await page.mouse.move(box['x']+box['width']*.5,box['y']+box['height']/2,steps=5)
                    assert len(resolutions)==before,'Dragging must not send one lookup per pixel'
                    await page.mouse.up()
                    await expect(page.get_by_role('button',name=ui('Pause','Пауза'),exact=True)).to_be_visible()
                    assert len(resolutions)==before+1
                    await page.get_by_role('button',name=ui('Pause','Пауза'),exact=True).click()
                    await page.get_by_text(ui('Selected cameras: 4','Выбрано камер: 4'),exact=True).click()
                    await page.get_by_role('button',name=ui('Clear','Очистить'),exact=True).click()
                    await expect(page.locator('.archive-camera')).to_have_count(0)
                    await page.get_by_label('Camera 1',exact=True).check()
                    await page.get_by_label('Camera 2',exact=True).check()
                    await expect(page.locator('.archive-camera')).to_have_count(2)
                    await page.get_by_role('button',name=ui('Clear','Очистить'),exact=True).click()
                    await page.get_by_role('button',name=ui('Select all','Выбрать все'),exact=True).click()
                    await expect(page.locator('.archive-camera')).to_have_count(4)
                    await page.evaluate("window.dispatchEvent(new StorageEvent('storage',{key:'nvr-recordings-changed',newValue:'1'}))")
                    await expect(page.get_by_text(ui('Recordings changed. Archive refreshed.','Записи изменились. Архив обновлён.'),exact=True)).to_be_visible()
                    await expect(page.locator('.synchronized-playback video')).to_have_count(0)
                    await page.get_by_role('button',name=ui('Overview','Обзор'),exact=True).click()
                    await page.get_by_role('link',name=ui('Account','Аккаунт')).click()
                    settings=page.locator('.time-settings')
                    await settings.get_by_text(ui('Change timezone','Изменить часовой пояс'),exact=True).click()
                    await settings.get_by_label(ui('Timezone','Часовой пояс'),exact=True).fill('America/New_York')
                    await settings.get_by_role('button',name=ui('Save timezone','Сохранить часовой пояс'),exact=True).click()
                    await expect(settings).to_contain_text(ui('Timezone saved.','Часовой пояс сохранён.'))
                    await page.get_by_role('button',name=ui('Archive','Архив'),exact=True).click()
                    await expect(page.get_by_label(ui('Archive camera','Камера архива'),exact=True)).to_have_value('')
                    await page.get_by_label(ui('Archive camera','Камера архива'),exact=True).select_option(str(cameras[0]))
                    await page.get_by_label(ui('Archive date','Дата архива'),exact=True).fill('2026-11-01')
                    await page.get_by_label(ui('Archive time','Время архива'),exact=True).fill('01:30:00')
                    await page.get_by_role('button',name=ui('Play selected cameras','Смотреть выбранные камеры'),exact=True).click()
                    await expect(page.get_by_label(ui('Repeated local time','Повторяющееся местное время'))).to_be_visible()
                    await page.get_by_label(ui('Repeated local time','Повторяющееся местное время')).select_option('2026-11-01T06:30:00+00:00')
                    await page.get_by_role('button',name=ui('Play selected cameras','Смотреть выбранные камеры'),exact=True).click()
                    await expect(page.locator('.synchronized-playback')).to_contain_text(ui('No recording at this time','В указанное время записи нет'))
                    assert datetime.fromisoformat(resolutions[-1]['time'])==datetime.fromisoformat('2026-11-01T06:30:00Z')
                    await page.get_by_label(ui('Archive date','Дата архива'),exact=True).fill('2026-03-08')
                    await page.get_by_label(ui('Archive time','Время архива'),exact=True).fill('02:30:00')
                    await page.get_by_role('button',name=ui('Play selected cameras','Смотреть выбранные камеры'),exact=True).click()
                    await expect(page.get_by_role('alert')).to_contain_text(ui('This local time does not exist','Это местное время отсутствует'))
                    assert not errors,errors
                finally:
                    await browser.close()
        asyncio.run(exercise())
        with main.db() as connection:
            assert [tuple(row) for row in connection.execute('SELECT * FROM segments ORDER BY id')]==original


@pytest.mark.browser
@pytest.mark.skipif(os.getenv('NVR_RUN_BROWSER') != '1', reason='Build frontend and set NVR_RUN_BROWSER=1')
@pytest.mark.parametrize('language,width',[('en',1280),('ru',390)])
def test_single_camera_live_navigation_and_multiview_independence(language, width):
    from playwright.async_api import async_playwright, expect
    root=Path(__file__).resolve().parents[1]/'frontend'/'dist'
    async def exercise():
        cameras=[{'id':i,'name':f'Camera {i}','enabled':True,'substream_url':'rtsp://synthetic/sub'} for i in [1,2]]
        layout={'columns':2,'tiles':[{'camera_id':2,'x':0,'y':0,'w':1,'h':1}]}
        writes=[]
        status='ONLINE'
        camera_gate=None
        camera_failure=False
        async with async_playwright() as playwright:
            browser=await playwright.chromium.launch(executable_path=os.getenv('NVR_CHROME_EXECUTABLE') or None,headless=True,args=['--no-sandbox'])
            try:
                page=await browser.new_page(viewport={'width':width,'height':844})
                async def route(handler):
                    path=urlsplit(handler.request.url).path
                    if path=='/api/language':
                        result={'default_language':language}
                    elif path=='/api/cameras':
                        if camera_gate is not None:
                            await camera_gate.wait()
                        if camera_failure:
                            await handler.fulfill(status=503,json={'detail':'Request failed'})
                            return
                        result=cameras
                    elif path=='/api/config':
                        result={'webrtc_port':8889,'timezone':'UTC'}
                    elif path=='/api/dashboard':
                        result={'cameras':len(cameras),'storage':{'free_bytes':None},'errors':[]}
                    elif path=='/api/layout':
                        if handler.request.method=='PUT':
                            writes.append(handler.request.post_data_json)
                        result=layout
                    elif path.endswith('/status'):
                        result={'connectivity_state':status}
                    elif path=='/reader.js':
                        await handler.fulfill(content_type='text/javascript',body='''window.liveReaders=[];window.closedReaders=[];window.liveConfigs=[];
window.MediaMTXWebRTCReader=class{constructor(config){window.liveReaders.push(config.url);window.liveConfigs.push(config);config.onError('unavailable')}close(){window.closedReaders.push(true)}};''')
                        return
                    else:
                        asset=root/'index.html' if path=='/' or path.startswith('/cameras/') else root/path.lstrip('/')
                        await handler.fulfill(body=asset.read_bytes(),content_type=mimetypes.guess_type(asset.name)[0] or 'application/octet-stream')
                        return
                    await handler.fulfill(json=result)
                await page.route('http://nvr.test/**',route)
                overview='Overview' if language=='en' else 'Обзор'
                multiview='Multiview' if language=='en' else 'Мультиэкран'
                watch='Watch' if language=='en' else 'Смотреть'
                back='Back to Overview' if language=='en' else 'Назад к обзору'
                missing='Camera not found' if language=='en' else 'Камера не найдена'
                async def single(id):
                    await expect(page.locator('header h2')).to_have_text(f'Camera {id}')
                    await expect(page.locator('video')).to_have_count(1)
                    await expect(page.locator('.tile')).to_have_count(0)
                    await expect(page.locator('main')).not_to_contain_text(f'Camera {3-id}')
                    assert (await page.evaluate('window.liveReaders.at(-1)')).endswith(f'/cam_{id}/whep')
                    assert await page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
                await page.goto('http://nvr.test/')
                await page.locator('.overview-row').filter(has_text='Camera 1').get_by_role('button',name=watch,exact=True).click()
                await expect(page).to_have_url('http://nvr.test/cameras/1/live')
                await single(1)
                readers=await page.evaluate('({opened:liveReaders.length,closed:closedReaders.length})')
                await fullscreen_roundtrip(page,page.locator('.single-camera-video .stream'),language)
                assert await page.evaluate('({opened:liveReaders.length,closed:closedReaders.length})')==readers
                enter='Enter fullscreen' if language=='en' else 'На весь экран'
                leave='Exit fullscreen' if language=='en' else 'Выйти из полноэкранного режима'
                stream=page.locator('.single-camera-video .stream')
                await stream.evaluate('element=>{element.requestFullscreen=()=>Promise.reject(new Error("denied"))}')
                await stream.get_by_role('button',name=enter,exact=True).click()
                await expect(stream.locator('.fullscreen-error')).to_contain_text('Fullscreen unavailable' if language=='en' else 'Полноэкранный режим недоступен')
                await expect(stream.get_by_role('button',name=enter,exact=True)).to_be_enabled()
                assert await page.evaluate('({opened:liveReaders.length,closed:closedReaders.length})')==readers
                await stream.evaluate('element=>{delete element.requestFullscreen}')
                await stream.get_by_role('button',name=enter,exact=True).click()
                await expect(stream.get_by_role('button',name=leave,exact=True)).to_have_attribute('aria-pressed','true')
                await page.evaluate('document.exitFullscreen()')
                await expect(stream.get_by_role('button',name=enter,exact=True)).to_have_attribute('aria-pressed','false')
                await expect(stream.locator('.fullscreen-error')).to_have_count(0)
                await stream.evaluate('element=>{element.requestFullscreen=undefined;document.dispatchEvent(new Event("fullscreenchange"))}')
                await expect(stream.locator('.fullscreen-control')).to_have_count(0)
                await expect(stream.locator('video')).to_have_count(1)
                await stream.evaluate('element=>{delete element.requestFullscreen;document.dispatchEvent(new Event("fullscreenchange"))}')
                await expect(stream.get_by_role('button',name=enter,exact=True)).to_be_visible()
                await expect(page.locator('.stream-error')).to_be_visible()
                await page.evaluate('window.liveConfigs.at(-1).onTrack({streams:[new MediaStream()]})')
                await expect(page.locator('.stream-error')).to_have_count(0)
                assert await page.locator('video').evaluate('(video)=>video.srcObject instanceof MediaStream')
                await page.reload()
                await single(1)
                await page.get_by_role('button',name=back,exact=True).click()
                await page.locator('.overview-row').filter(has_text='Camera 2').get_by_role('button',name=watch,exact=True).click()
                await single(2)
                await page.go_back()
                await expect(page.locator('.overview-row')).to_have_count(2)
                await page.go_forward()
                await single(2)
                await page.get_by_role('button',name=multiview,exact=True).click()
                await expect(page.locator('.tile')).to_have_count(1)
                await expect(page.locator('.tile strong')).to_have_text('Camera 2')
                assert (await page.evaluate('window.liveReaders.at(-1)')).endswith('/cam_2_sub/whep')
                readers=await page.evaluate('({opened:liveReaders.length,closed:closedReaders.length})')
                await page.locator('.tile .fit').click()
                await fullscreen_roundtrip(page,page.locator('.tile .stream'),language)
                assert await page.locator('.tile video').evaluate('video=>getComputedStyle(video).objectFit')=='cover'
                assert await page.evaluate('({opened:liveReaders.length,closed:closedReaders.length})')==readers
                await page.get_by_role('button',name=overview,exact=True).click()
                await page.go_back()
                await expect(page.locator('.tile strong')).to_have_text('Camera 2')
                await page.reload()
                await expect(page.locator('.tile strong')).to_have_text('Camera 2')
                assert writes==[]
                for id in ['999','invalid']:
                    await page.goto(f'http://nvr.test/cameras/{id}/live')
                    await expect(page.get_by_text(missing,exact=True)).to_be_visible()
                    await expect(page.locator('video')).to_have_count(0)
                camera_gate=asyncio.Event()
                await page.goto('http://nvr.test/cameras/1/live')
                await expect(page.get_by_text('Loading appliance status…' if language=='en' else 'Загрузка состояния устройства…',exact=True)).to_be_visible()
                camera_gate.set()
                camera_gate=None
                await single(1)
                camera_failure=True
                await page.reload()
                await expect(page.get_by_text('Camera could not be loaded' if language=='en' else 'Не удалось загрузить камеру',exact=True)).to_be_visible()
                await expect(page.locator('video')).to_have_count(0)
                camera_failure=False
                status='OFFLINE'
                await page.goto('http://nvr.test/cameras/1/live')
                await single(1)
                await expect(page.locator('.single-camera-live .feedback')).to_contain_text('offline' if language=='en' else 'не в сети')
                await page.locator('video').dispatch_event('error')
                await expect(page.locator('.stream-error')).to_contain_text('playback failed' if language=='en' else 'Ошибка просмотра')
                cameras[0]['enabled']=False
                await page.reload()
                await expect(page.locator('.single-camera-live .feedback')).to_contain_text('disabled' if language=='en' else 'отключена')
                cameras.pop(0)
                await expect(page.get_by_text(missing,exact=True)).to_be_visible(timeout=15000)
                await expect(page.locator('video')).to_have_count(0)
                assert await page.evaluate('window.closedReaders.length')>0
                assert writes==[]
            finally:
                await browser.close()
    asyncio.run(exercise())


@pytest.mark.browser
@pytest.mark.skipif(os.getenv('NVR_RUN_BROWSER') != '1', reason='Build frontend and set NVR_RUN_BROWSER=1')
@pytest.mark.parametrize('width', [1280, 390])
def test_login_account_preferences_and_logout(width):
    from playwright.async_api import async_playwright, expect
    root = Path(__file__).resolve().parents[1] / 'frontend' / 'dist'

    async def exercise():
        logged_in = False
        zone = 'UTC'
        submissions = []
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(executable_path=os.getenv('NVR_CHROME_EXECUTABLE') or None, headless=True, args=['--no-sandbox'])
            try:
                page = await browser.new_page(viewport={'width':width,'height':900})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))

                async def route(handler):
                    nonlocal logged_in, zone
                    request = handler.request
                    url = urlsplit(request.url)
                    path = url.path
                    if path == '/api/language':
                        await handler.fulfill(json={'default_language':'en'})
                        return
                    if path == '/api/login':
                        submissions.append(request.post_data_json)
                        await asyncio.sleep(.5)
                        logged_in = request.post_data_json == {'username':'admin','password':'correct'}
                        await handler.fulfill(status=200 if logged_in else 401, json={'detail':'private backend error','ok':logged_in})
                        return
                    if path == '/api/logout':
                        logged_in = False
                        await handler.fulfill(status=204)
                        return
                    if path.startswith('/api/'):
                        if not logged_in:
                            await handler.fulfill(status=401,json={'detail':'Authentication required'})
                            return
                        if path == '/api/config':
                            result = {'webrtc_port':8889,'timezone':zone,'username':'admin'}
                        elif path == '/api/cameras':
                            result = []
                        elif path == '/api/layout':
                            result = {'columns':2,'tiles':[]}
                        elif path == '/api/dashboard':
                            result = {'cameras':0,'online':0,'writing':0,'errors':[],'storage':{'free_bytes':None}}
                        elif path == '/api/time':
                            if request.method == 'PUT':
                                zone = request.post_data_json['timezone']
                            result = {'timezone':zone,'now':'2026-10-06T12:00:00Z','timezones':['UTC','Europe/Moscow']}
                        else:
                            raise AssertionError(path)
                        await handler.fulfill(json=result)
                        return
                    asset = root / path.lstrip('/') if path not in ('/','/account') else root / 'index.html'
                    await handler.fulfill(body=asset.read_bytes(),content_type=mimetypes.guess_type(asset.name)[0] or 'application/octet-stream')

                await page.route('http://nvr.test/**',route)
                await page.goto('http://nvr.test/account')
                await expect(page.get_by_role('heading',name='Sign in to your NVR')).to_be_visible()
                await expect(page.locator('aside')).to_have_count(0)
                assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                await page.get_by_label('Username',exact=True).fill('admin')
                await page.get_by_label('Password',exact=True).fill('wrong')
                await page.get_by_label('Password',exact=True).press('Enter')
                await expect(page.get_by_role('button',name='Signing in…')).to_be_disabled()
                # Even synthetic repeated submissions cannot bypass the pending guard.
                await page.locator('form').evaluate('(form)=>{form.dispatchEvent(new Event("submit",{bubbles:true,cancelable:true}));form.dispatchEvent(new Event("submit",{bubbles:true,cancelable:true}))}')
                await expect(page.get_by_role('alert')).to_have_text('Invalid credentials')
                assert len(submissions) == 1
                await page.get_by_label('Password',exact=True).fill('correct')
                await page.get_by_label('Password',exact=True).press('Enter')
                await expect(page.get_by_role('heading',name='Account',exact=True).first).to_be_visible()
                assert urlsplit(page.url).path == '/account'
                await expect(page.locator('.account-control')).to_contain_text('admin')
                await expect(page.get_by_label('Language',exact=True)).to_have_value('en')
                await expect(page.locator('.time-settings')).to_contain_text('UTC')
                await page.get_by_label('Language',exact=True).select_option('ru')
                await expect(page.locator('html')).to_have_attribute('lang','ru')
                await expect(page.get_by_role('heading',name='Предпочтения')).to_be_visible()
                await page.reload()
                await expect(page.get_by_label('Язык',exact=True)).to_have_value('ru')
                await page.get_by_text('Изменить часовой пояс',exact=True).click()
                await page.get_by_label('Часовой пояс',exact=True).fill('Europe/Moscow')
                await page.get_by_role('button',name='Сохранить часовой пояс',exact=True).click()
                await expect(page.get_by_role('status')).to_contain_text('Часовой пояс сохранён.')
                await page.reload()
                await expect(page.locator('.time-settings')).to_contain_text('Europe/Moscow')
                await page.get_by_label('Язык',exact=True).select_option('en')
                await page.get_by_role('button',name='Overview',exact=True).click()
                await expect(page.locator('.language-switch,.time-settings')).to_have_count(0)
                await expect(page.locator('aside select')).to_have_count(0)
                account = page.get_by_role('link',name='Account admin')
                if width == 1280:
                    bounds = await account.bounding_box()
                    nav = await page.locator('nav').bounding_box()
                    assert bounds['y'] > nav['y'] + nav['height'] + 200
                await account.click()
                await expect(page.get_by_role('heading',name='Preferences')).to_be_visible()
                assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                await page.get_by_role('button',name='Log out',exact=True).click()
                await expect(page.get_by_role('heading',name='Sign in to your NVR')).to_be_visible()
                await expect(page.locator('aside')).to_have_count(0)
                await page.reload()
                await expect(page.get_by_label('Password',exact=True)).to_be_visible()
                assert not errors, errors
            finally:
                await browser.close()
    asyncio.run(exercise())


@pytest.mark.browser
@pytest.mark.skipif(os.getenv('NVR_RUN_BROWSER') != '1', reason='Build frontend and set NVR_RUN_BROWSER=1')
@pytest.mark.parametrize('failed', [0, 1])
def test_storage_cleanup_confirmation_selection_and_results(failed):
    """Exercise the rendered UI, including cancellation and partial results."""
    from playwright.async_api import async_playwright, expect
    root = Path(__file__).resolve().parents[1] / 'frontend' / 'dist'

    async def exercise():
        entries = [{'id': i, 'camera_id': i, 'started_at': '2020-01-01T10:00:00Z', 'ended_at': '2020-01-01T10:05:00Z', 'size_bytes': 1024**3} for i in (1, 2)]
        deletes, previews, queries = [], [], []
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(executable_path=os.getenv('NVR_CHROME_EXECUTABLE') or None, headless=True, args=['--no-sandbox'])
            try:
                page = await browser.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                async def route(handler):
                    request = handler.request
                    path = urlsplit(request.url).path
                    if path.startswith('/api/'):
                        if path == '/api/language':
                            result = {'default_language': 'en'}
                        elif path == '/api/config':
                            result = {'webrtc_port': 8889, 'timezone': 'Europe/Moscow', 'username': 'admin'}
                        elif path == '/api/cameras':
                            result = [{'id': 1, 'name': 'Door', 'enabled': False}, {'id': 2, 'name': 'Yard', 'enabled': False}]
                        elif path == '/api/dashboard':
                            result = {'cameras': 2, 'online': 0, 'writing': 0, 'errors': [], 'storage': {'free_bytes': None}}
                        elif path == '/api/layout':
                            result = {'columns': 2, 'tiles': []}
                        elif path == '/api/storage/destinations':
                            result = []
                        elif path == '/api/notifications/status':
                            result = {'enabled': False, 'pending': 0, 'failed': 0, 'last_delivery': None}
                        elif path.endswith('/status'):
                            result = {'online': False}
                        elif path == '/api/time/resolve':
                            body = request.post_data_json
                            assert body['time'] in ('13:00', '14:00')
                            result = {'instants': [{'time': f"{body['date']}T{'10:00' if body['time']=='13:00' else '11:00'}:00Z"}]}
                        elif path == '/api/recordings/cleanup/query':
                            await asyncio.sleep(.15)
                            body = request.post_data_json
                            queries.append(body)
                            chosen = [row for row in entries if not body.get('camera_ids') or row['camera_id'] in body['camera_ids']]
                            result = {'total': len(chosen), 'recordings': chosen}
                        elif path == '/api/recordings/cleanup/preview':
                            body = request.post_data_json
                            previews.append(body)
                            chosen = [row for row in entries if (not body.get('camera_ids') or row['camera_id'] in body['camera_ids']) and (not body.get('recording_ids') or row['id'] in body['recording_ids'])]
                            result = {'token': str(len(previews)), 'count': len(chosen), 'size_bytes': len(chosen)*1024**3, 'criteria': body, 'cameras': [row['camera_id'] for row in chosen], 'clear_all': not body, 'active_excluded': 1}
                        elif path == '/api/recordings/cleanup/delete':
                            deletes.append(request.post_data_json)
                            result = {'token': request.post_data_json['token']}
                        elif path.startswith('/api/recordings/cleanup/progress/'):
                            entries[:] = entries[-1:] if failed else []
                            result = {'state': 'done', 'processed': 2, 'total': 2, 'deleted': 2-failed, 'reclaimed_bytes': (2-failed)*1024**3, 'active': 0, 'missing': 0, 'failed': failed}
                        else:
                            raise AssertionError(path)
                        await handler.fulfill(json=result)
                    else:
                        asset = root / 'index.html' if path in ('/', '/storage') else root / path.lstrip('/')
                        await handler.fulfill(body=asset.read_bytes(), content_type=mimetypes.guess_type(asset.name)[0] or 'application/octet-stream')
                await page.route('http://nvr.test/**', route)
                await page.goto('http://nvr.test/')
                await page.get_by_role('button', name='Storage', exact=True).click()
                section = page.locator('.recording-cleanup')
                await expect(section.get_by_text('Loading…', exact=True)).to_be_visible()
                await expect(section.get_by_label('Select recording 1')).to_be_visible()
                await section.get_by_role('button', name='Preview cleanup of all filtered recordings').click()
                dialog = page.get_by_role('dialog')
                await expect(dialog).to_contain_text('Recordings found: 2')
                await expect(dialog).to_contain_text('Active recording files are excluded: 1')
                await expect(dialog.get_by_role('button', name='Delete recordings')).to_be_disabled()
                await dialog.get_by_role('button', name='Cancel', exact=True).last.click()
                assert not deletes
                await section.get_by_label('All cameras', exact=True).uncheck()
                await section.get_by_label('Door', exact=True).check()
                await section.get_by_label('Custom date/time range').check()
                await section.locator('input[type=datetime-local]').nth(0).fill('2020-01-01T13:00')
                await section.locator('input[type=datetime-local]').nth(1).fill('2020-01-01T14:00')
                await section.get_by_role('button', name='Apply filters').click()
                await expect(section.get_by_label('Select recording 2')).to_have_count(0)
                assert queries[-1] == {'camera_ids': [1], 'start': '2020-01-01T10:00:00Z', 'end': '2020-01-01T11:00:00Z'}
                await section.get_by_label('Select recording 1').check()
                await section.get_by_role('button', name='Deselect all', exact=True).click()
                await expect(section.get_by_label('Select recording 1')).not_to_be_checked()
                await section.get_by_role('button', name='Select all on this page').click()
                await section.get_by_role('button', name='Preview selected recordings').click()
                await expect(dialog).to_contain_text('Recordings found: 1')
                assert previews[-1]['recording_ids'] == [1]
                await dialog.get_by_role('button', name='Cancel', exact=True).last.click()
                assert not deletes
                await section.get_by_label('All cameras', exact=True).check()
                await section.get_by_label('Custom date/time range').uncheck()
                await section.get_by_role('button', name='Apply filters').click()
                await expect(section.get_by_label('Select recording 2')).to_be_visible()
                await section.get_by_role('button', name='Preview cleanup of all filtered recordings').click()
                await dialog.get_by_label('Type DELETE', exact=True).fill('DELETE')
                await dialog.get_by_role('button', name='Delete recordings').click()
                await expect(section.get_by_text('Cleanup complete', exact=False)).to_be_visible()
                await expect(section).to_contain_text(f'Deleted: {2-failed}')
                await expect(section).to_contain_text(f'Failed: {failed}')
                if failed:
                    await expect(section.get_by_label('Select recording 2')).to_be_visible()
                else:
                    await expect(section.get_by_text('No recordings match these filters.')).to_be_visible()
                assert deletes == [{'token': '3', 'confirmation': 'DELETE'}]
                assert not errors, errors
            finally:
                await browser.close()
    asyncio.run(exercise())
