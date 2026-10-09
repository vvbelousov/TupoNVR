"""Camera YAML file selection, preview, confirmation and download in real DOM."""
import asyncio
import mimetypes
import os
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

import pytest


@pytest.mark.browser
@pytest.mark.skipif(os.getenv('NVR_RUN_BROWSER') != '1', reason='Build UI and enable browser checks')
@pytest.mark.parametrize('language', ['en', 'ru'])
def test_camera_configuration_workflow(language):
    from playwright.async_api import async_playwright, expect
    root = Path(__file__).resolve().parents[1] / 'frontend/dist'
    def label(en, ru):
        return ru if language == 'ru' else en

    async def exercise():
        applications, exports = [], []
        camera_reads = 0
        fail_apply = False
        async def route(handler):
            nonlocal camera_reads
            req = handler.request
            path = urlsplit(req.url).path
            if path == '/api/language':
                await handler.fulfill(json={'default_language':language})
            elif path == '/api/camera-config/export':
                exports.append(parse_qs(urlsplit(req.url).query)['include_credentials'][0])
                await handler.fulfill(body='version: 1\ncameras: []\n', content_type='application/yaml',headers={'Content-Disposition':'attachment; filename="cameras.yaml"'})
            elif path == '/api/camera-config/preview':
                good = 'version: 1' in req.post_data
                entry = {'key':'door','name':'Door','changes':{'name':{'before':None,'after':'Door'},'password':{'changed':True}}}
                value={'create':[entry] if good else [],'update':[],'unchanged':[], 'conflicts':[], 'warnings':[], 'errors':[] if good else [{'field':'document','message':'Expected version and cameras fields'}]}
                if good:
                    value['fingerprint']='preview-proof'
                await handler.fulfill(json=value)
            elif path == '/api/camera-config/apply':
                assert req.headers['x-confirm-import'] == 'true'
                assert req.headers['x-import-fingerprint'] == 'preview-proof'
                applications.append(req.post_data)
                await handler.fulfill(status=409 if fail_apply else 200,json={'detail':'Camera configuration changed; preview again'} if fail_apply else {'created':1,'updated':0,'unchanged':0,'runtime_pending':False})
            elif path.startswith('/api/'):
                if path == '/api/cameras':
                    camera_reads += 1
                values={'/api/config':{'timezone':'UTC','username':'admin'}, '/api/cameras':[], '/api/layout':{'columns':2,'tiles':[]},'/api/dashboard':{'storage':{},'destinations':[]}}
                await handler.fulfill(json=values.get(path,{}))
            else:
                file = root/path.lstrip('/')
                if not file.is_file():
                    file = root/'index.html'
                await handler.fulfill(body=file.read_bytes(),content_type=mimetypes.guess_type(file.name)[0] or 'application/octet-stream')
        async with async_playwright() as playwright:
            browser=await playwright.chromium.launch(executable_path=os.getenv('NVR_CHROME_EXECUTABLE'),args=['--no-sandbox'])
            page=await browser.new_page()
            await page.route('**/*',route)
            await page.goto('http://nvr.test/?page=Cameras')
            section=page.locator('.camera-config')
            checkbox=section.get_by_role('checkbox',name=label('Include credentials','Включить учётные данные'))
            await expect(checkbox).not_to_be_checked()
            async with page.expect_download() as download:
                await section.get_by_role('button',name=label('Export YAML','Экспорт YAML')).click()
            assert (await download.value).suggested_filename == 'cameras.yaml'
            assert exports == ['false']
            await checkbox.check()
            await expect(section.get_by_text(label('This file includes passwords and URL tokens. Store it securely and share it only with trusted people.','Файл содержит пароли и токены URL. Храните его безопасно и передавайте только доверенным людям.'))).to_be_visible()
            async with page.expect_download():
                await section.get_by_role('button',name=label('Export YAML','Экспорт YAML')).click()
            assert exports == ['false','true']
            upload=section.locator('input[type=file]')
            preview=section.get_by_role('button',name=label('Preview changes','Предпросмотр изменений'))
            confirm=section.get_by_role('button',name=label('Confirm import','Подтвердить импорт'))
            await upload.set_input_files({'name':'empty.yaml','mimeType':'application/yaml','buffer':b''})
            await expect(preview).to_be_disabled()
            await upload.set_input_files({'name':'bad.yaml','mimeType':'application/yaml','buffer':b'bad'})
            await expect(preview).to_be_enabled()
            await preview.click()
            await expect(section.get_by_text(label('document: Expected version and cameras fields','document: Ожидаются поля version и cameras'))).to_be_visible()
            await expect(confirm).to_have_count(0)
            content=b'version: 1\ncameras:\n - key: door\n   name: Door\n   password: secret-test\n'
            await upload.set_input_files({'name':'cameras.yaml','mimeType':'application/yaml','buffer':content})
            await expect(preview).to_be_enabled()
            assert not applications
            await preview.click()
            await expect(confirm).to_be_visible()
            assert 'secret-test' not in await section.inner_text()
            await section.locator('summary').click()
            await expect(section.get_by_text(label('Changed (hidden)','Изменено (скрыто)'),exact=False)).to_be_visible()
            before=camera_reads
            await confirm.click()
            await expect(section.get_by_text(label('Imported: 1 created · 0 updated · 0 unchanged','Импортировано: создано 1 · обновлено 0 · без изменений 0'))).to_be_visible()
            assert camera_reads > before and len(applications) == 1
            assert applications[0] == content.decode()
            await preview.click()
            await expect(confirm).to_be_visible()
            fail_apply=True
            await confirm.click()
            await expect(section.get_by_text(label('409: Camera configuration changed; preview again','409: Настройки камер изменились; выполните предпросмотр снова'))).to_be_visible()
            await expect(confirm).to_have_count(0)
            await browser.close()
    asyncio.run(exercise())
