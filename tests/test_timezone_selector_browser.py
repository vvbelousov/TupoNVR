"""Real DOM keyboard, pointer and save workflows against built UI assets."""
import asyncio
import mimetypes
import os
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import available_timezones

import pytest


@pytest.mark.browser
@pytest.mark.skipif(os.getenv('NVR_RUN_BROWSER') != '1', reason='Build frontend and set NVR_RUN_BROWSER=1')
@pytest.mark.parametrize('language', ['en', 'ru'])
@pytest.mark.parametrize('mobile', [False, True])
def test_timezone_selector_and_branding(language, mobile):
    from playwright.async_api import async_playwright, expect

    root = Path(__file__).resolve().parents[1] / 'frontend' / 'dist'
    zones = sorted(available_timezones() - {'localtime', 'posixrules', 'Factory'})
    labels = {
        'change': ('Change timezone', 'Изменить часовой пояс'),
        'save': ('Save timezone', 'Сохранить часовой пояс'),
        'empty': ('No matching timezones', 'Часовые пояса не найдены'),
        'saved': ('Timezone saved. Recording schedules now use this timezone.', 'Часовой пояс сохранён. Расписания записи используют этот часовой пояс.'),
    }
    def text(key):
        return labels[key][language == 'ru']

    async def exercise():
        logged_in = False
        current = 'Europe/Moscow'
        saves = []

        async def route(handler):
            nonlocal logged_in, current
            request = handler.request
            path = urlsplit(request.url).path
            if path == '/api/language':
                await handler.fulfill(json={'default_language': language})
            elif path == '/api/login':
                logged_in = True
                await handler.fulfill(json={'ok': True})
            elif path.startswith('/api/'):
                if not logged_in:
                    await handler.fulfill(status=401, json={'detail': 'Authentication required'})
                elif path == '/api/time':
                    if request.method == 'PUT':
                        zone = request.post_data_json['timezone']
                        assert zone in zones
                        saves.append(zone)
                        current = zone
                        await handler.fulfill(json={'timezone': current})
                    else:
                        await handler.fulfill(json={'timezone': current, 'timezones': zones, 'now': '2026-10-09T10:00:00Z'})
                else:
                    replies = {
                        '/api/config': {'timezone': current, 'username': 'admin'},
                        '/api/cameras': [], '/api/layout': {'columns': 2, 'tiles': []},
                        '/api/dashboard': {'storage': {}, 'destinations': []},
                    }
                    await handler.fulfill(json=replies.get(path, {}))
            else:
                file = root / path.lstrip('/')
                if not file.is_file():
                    file = root / 'index.html'
                await handler.fulfill(body=file.read_bytes(), content_type=mimetypes.guess_type(file.name)[0] or 'application/octet-stream')

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(executable_path=os.getenv('NVR_CHROME_EXECUTABLE'), args=['--no-sandbox'])
            context = await browser.new_context(viewport={'width': 390, 'height': 844} if mobile else {'width': 1280, 'height': 900}, has_touch=mobile, is_mobile=mobile)
            page = await context.new_page()
            await page.route('**/*', route)
            await page.goto('http://nvr.test/account')
            await expect(page.locator('.login-brand .brand')).to_have_text('TupoNVR')
            await expect(page.locator('.brand img')).to_have_js_property('complete', True)
            assert await page.locator('.brand img').evaluate('(image)=>image.naturalWidth>0')
            assert await page.locator('link[rel=icon]').get_attribute('href') == '/favicon.svg'
            await page.locator('input[name=user]').fill('admin')
            await page.locator('input[name=pass]').fill('password')
            await page.locator('form button').click()
            await expect(page.locator('aside .brand')).to_have_text('TupoNVR')
            await expect(page.locator('aside .brand img')).to_have_js_property('complete', True)
            assert await page.locator('aside .brand img').evaluate('(image)=>image.naturalWidth>0')
            await page.get_by_text(text('change'), exact=True).click()
            field = page.get_by_role('combobox', name='Часовой пояс' if language == 'ru' else 'Timezone')
            await expect(field).to_have_value(current)
            await page.get_by_role('button', name=text('save'), exact=True).wait_for()
            await field.click()
            options = page.get_by_role('listbox').get_by_role('option')
            await expect(options).to_have_count(len(zones))
            await expect(page.get_by_role('option', name='Europe/Moscow', exact=False)).to_have_attribute('aria-selected', 'true')
            assert await page.get_by_role('listbox').evaluate('(list)=>list.parentElement===document.body')
            await field.fill('bErLiN')
            await expect(options).to_have_count(1)
            await field.press('Escape')
            await expect(field).to_have_value('Europe/Moscow')
            assert saves == []
            # Clicking a field that still has focus must reopen the full list.
            await field.click()
            await expect(options).to_have_count(len(zones))
            await field.fill('Invalid/Zone')
            await expect(page.get_by_role('status')).to_have_text(text('empty'))
            await field.press('Enter')
            assert saves == []
            await field.press('Tab')
            await expect(field).to_have_value('Europe/Moscow')
            await expect(page.get_by_role('listbox')).to_have_count(0)
            await field.click()
            await field.fill('europe/')
            first = await options.first.inner_text()
            await field.press('ArrowDown')
            active = await field.get_attribute('aria-activedescendant')
            assert await page.locator(f'[id="{active}"]').inner_text() != first
            await field.press('ArrowUp')
            active = await field.get_attribute('aria-activedescendant')
            assert (await page.locator(f'[id="{active}"]').inner_text()).replace(' ✓', '') == first.replace(' ✓', '')
            await field.fill('BERLIN')
            await field.press('Enter')
            await expect(field).to_have_value('Europe/Berlin')
            assert saves == []  # Selecting is still a draft until the existing Save action.
            await page.get_by_role('button', name=text('save'), exact=True).click()
            await expect(page.get_by_text(text('saved'), exact=True)).to_be_visible()
            assert saves == ['Europe/Berlin']
            await field.click()
            await expect(options).to_have_count(len(zones))
            await field.fill('Tokyo')
            option = page.get_by_role('option', name='Asia/Tokyo', exact=True)
            if mobile:
                await option.tap()
            else:
                await option.click()
            await expect(field).to_have_value('Asia/Tokyo')
            await expect(page.get_by_role('listbox')).to_have_count(0)
            await field.click()
            await field.fill('UTC')
            await page.locator('header h2').click()
            await expect(field).to_have_value('Asia/Tokyo')
            # Reopening restores the full list; popup stays inside the mobile viewport.
            await field.click()
            await expect(options).to_have_count(len(zones))
            bounds = await page.get_by_role('listbox').bounding_box()
            assert bounds['x'] >= 0 and bounds['x'] + bounds['width'] <= page.viewport_size['width']
            assert bounds['y'] >= 0 and bounds['y'] + bounds['height'] <= page.viewport_size['height']
            assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            await context.close()
            await browser.close()

    asyncio.run(exercise())
