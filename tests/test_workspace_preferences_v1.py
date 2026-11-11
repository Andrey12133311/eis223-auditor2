"""Local Chromium regressions for display preferences over real DOM controls."""
import json
import os
import unittest
from urllib.parse import urlsplit

from scripts.workspace_preferences_v1 import SCRIPT, workspace_preferences


def registry_fixture():
    return '''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{margin:0;padding:16px;font:14px system-ui}*{box-sizing:border-box}table{width:100%;border-collapse:collapse}th,td{padding:12px;border:1px solid #ddd}label{display:block}input,select,button{font:inherit}</style>
</head><body><main><form class="filters">
<label>Поиск<input id="query" type="search"></label><label>Состояние<select id="status"><option value="">Все</option><option value="pending">В работе</option></select></label>
<label>Дата<input id="from" type="date"></label><label><input id="violations" type="checkbox">Есть нарушения</label>
<button id="apply" type="button">Применить</button></form>
<table><thead><tr><th>Номер закупки</th><th>Заказчик</th><th>Действия</th></tr></thead><tbody><tr><td><a href="/check-new/32612345678">32612345678</a></td><td>Заказчик</td><td><button type="button">Открыть</button></td></tr></tbody></table></main>
<script>window.applies=0;document.getElementById('apply').onclick=()=>{window.applies++};</script>''' + workspace_preferences() + '</body></html>'


class PreferencesMarkupTests(unittest.TestCase):
    def test_enhancement_has_only_an_explicit_owner_read_and_no_new_submission(self):
        self.assertEqual(SCRIPT.count('fetch('), 1)
        self.assertIn("fetch('/me/profile'", SCRIPT)
        self.assertNotIn('innerHTML', SCRIPT)
        self.assertNotIn('method:', SCRIPT)
        self.assertIn("node.type='button'", SCRIPT)
        self.assertIn('sessionStorage', SCRIPT)
        self.assertIn('localStorage', SCRIPT)


@unittest.skipUnless(os.environ.get('EIS_PREFERENCES_BROWSER_TESTS') == '1', 'Optional local Chromium checks')
class PreferencesBrowserTests(unittest.TestCase):
    def browser(self):
        from playwright.sync_api import sync_playwright
        self.playwright = sync_playwright().start()
        self.addCleanup(self.playwright.stop)
        self.chromium = self.playwright.chromium.launch(executable_path='/usr/bin/chromium', chromium_sandbox=True)
        self.addCleanup(self.chromium.close)
        return self.chromium

    def page(self, browser, mobile=False, blocked_storage=False):
        context = browser.new_context(viewport={'width': 390 if mobile else 1200, 'height': 900}, has_touch=mobile, is_mobile=mobile)
        self.addCleanup(context.close)
        if blocked_storage:
            context.add_init_script("for(const key of ['localStorage','sessionStorage'])Object.defineProperty(window,key,{get(){throw new DOMException('Blocked','SecurityError')}})")
        requests, errors = [], []
        def handler(route):
            requests.append((route.request.method, route.request.url))
            if route.request.url.endswith('/me/profile'):
                route.fulfill(status=200, content_type='application/json', body=json.dumps({'email': 'preview@example.test'}))
            else:
                route.fulfill(status=200, content_type='text/html', body=registry_fixture())
        context.route('**/*', handler)
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto('https://preferences.eis.test/')
        page.locator('.eis-view-preferences summary').click()
        return page, requests, errors

    def identity(self, page, email):
        page.evaluate("email=>document.dispatchEvent(new CustomEvent('eis:workspace-data',{detail:{path:'/me/profile',data:{email}}}))", email)
        page.wait_for_function("document.querySelector('.eis-preference-note').textContent.includes('учётной записи')")

    def test_columns_density_saved_filters_and_owner_partition_keep_existing_handlers(self):
        from playwright.sync_api import expect
        page, requests, errors = self.page(self.browser())
        self.identity(page, 'first@example.test')
        page.get_by_role('button', name='Компактная', exact=True).click()
        expect(page.locator('table')).to_have_class('eis-display-table eis-density-compact')
        customer = page.locator('.eis-preference-column').filter(has_text='Заказчик').locator('input')
        customer.uncheck()
        expect(page.locator('tbody td').nth(1)).to_be_hidden()
        expect(page.locator('tbody td').first).to_be_visible()
        expect(page.locator('tbody td').last).to_be_visible()
        expect(page.locator('.eis-preference-column').filter(has_text='Номер закупки').locator('input')).to_be_disabled()
        expect(page.locator('.eis-preference-column').filter(has_text='Действия').locator('input')).to_be_disabled()
        page.locator('#query').fill('ИНН 7700000000')
        page.locator('#status').select_option('pending')
        page.locator('#from').fill('2026-11-11')
        page.locator('#violations').check()
        page.get_by_label('Название набора').fill('Мои условия')
        page.get_by_role('button', name='Сохранить фильтры', exact=True).click()
        display = page.evaluate("JSON.parse(localStorage.getItem('eis223.display.v1.registry'))")
        self.assertEqual(display, {'version': 1, 'density': 'compact', 'hidden': ['Заказчик#1']})
        self.assertNotIn('7700000000', page.evaluate('JSON.stringify(localStorage)'))
        page.locator('#query').fill('изменено')
        page.locator('#status').select_option('')
        page.locator('#from').fill('')
        page.locator('#violations').uncheck()
        page.locator('.eis-preference-use').get_by_role('button', name='Применить', exact=True).click()
        expect(page.locator('#query')).to_have_value('ИНН 7700000000')
        expect(page.locator('#status')).to_have_value('pending')
        expect(page.locator('#from')).to_have_value('2026-11-11')
        expect(page.locator('#violations')).to_be_checked()
        self.assertEqual(page.evaluate('window.applies'), 1)
        # A new data row inherits the chosen columns without hiding its action.
        page.evaluate("document.querySelector('tbody').append(document.querySelector('tbody tr').cloneNode(true))")
        expect(page.locator('tbody tr').last.locator('td').nth(1)).to_be_hidden()
        expect(page.locator('tbody tr').last.locator('td').last).to_be_visible()
        page.reload();page.locator('.eis-view-preferences summary').click();self.identity(page, 'second@example.test')
        expect(page.get_by_label('Сохранённые наборы')).to_be_disabled()
        self.identity(page, 'first@example.test')
        expect(page.get_by_label('Сохранённые наборы')).to_be_enabled()
        self.assertEqual(page.get_by_label('Сохранённые наборы').locator('option').all_text_contents(), ['Выберите набор', 'Мои условия'])
        self.assertEqual(errors, [])
        self.assertTrue(all(method == 'GET' and url == 'https://preferences.eis.test/' for method, url in requests))

    def test_storage_failure_and_missing_options_are_safe_on_mobile(self):
        from playwright.sync_api import expect
        page, requests, errors = self.page(self.browser(), mobile=True, blocked_storage=True)
        page.get_by_role('button', name='Компактная', exact=True).click()
        expect(page.locator('.eis-preference-status')).to_contain_text('на этой странице')
        expect(page.locator('.eis-preference-note')).to_contain_text('только на этой странице')
        page.locator('#status').select_option('pending')
        page.get_by_label('Название набора').fill('<script>Безопасный текст</script>')
        page.get_by_role('button', name='Сохранить фильтры', exact=True).click()
        page.evaluate("document.querySelector('#status option[value=pending]').remove();document.getElementById('from').remove()")
        page.locator('.eis-preference-use').get_by_role('button', name='Применить', exact=True).click()
        expect(page.locator('.eis-preference-status')).to_contain_text('Недоступные условия пропущены')
        self.assertEqual(page.locator('.eis-view-preferences script').count(), 0)
        self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), 390)
        for button in page.locator('.eis-view-preferences button').all():
            if button.is_visible():self.assertGreaterEqual(button.bounding_box()['height'], 44)
        page.get_by_role('button', name='Удалить набор', exact=True).click()
        expect(page.get_by_label('Название набора')).to_be_focused()
        page.keyboard.press('Escape')
        expect(page.locator('.eis-view-preferences')).not_to_have_attribute('open', '')
        self.assertEqual(errors, [])
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[-1], ('GET', 'https://preferences.eis.test/me/profile'))

    def test_malformed_preferences_cannot_hide_identity_or_actions(self):
        from playwright.sync_api import expect
        page, requests, errors = self.page(self.browser())
        page.evaluate("localStorage.setItem('eis223.display.v1.registry',JSON.stringify({version:1,density:'malicious',hidden:['Номер закупки#0','Действия#2','unknown#44']}))")
        page.reload();page.locator('.eis-view-preferences summary').click()
        expect(page.locator('tbody td').first).to_be_visible()
        expect(page.locator('tbody td').last).to_be_visible()
        expect(page.get_by_role('button', name='Обычная', exact=True)).to_have_attribute('aria-pressed', 'true')
        page.evaluate("localStorage.setItem('eis223.display.v1.registry','not json')")
        page.reload();page.locator('.eis-view-preferences summary').click()
        expect(page.locator('tbody td').nth(1)).to_be_visible()
        expect(page.get_by_label('Сохранённые наборы')).to_be_disabled()
        self.assertEqual(errors, [])
        self.assertTrue(all(url == 'https://preferences.eis.test/' and method == 'GET' for method, url in requests))

    def test_manual_adapter_restores_the_existing_local_filters_without_starting_a_check(self):
        from playwright.sync_api import expect
        from scripts.account_pages_v1 import HOME
        browser = self.browser()
        context = browser.new_context(viewport={'width': 390, 'height': 900}, has_touch=True, is_mobile=True)
        self.addCleanup(context.close)
        rows = [{'reg_number': '32612345678', 'customer': 'Первый', 'job_status': 'done',
                 'documents_checked': 1, 'violations': 0, 'violation_types': [], 'card_available': True},
                {'reg_number': '32612345679', 'customer': 'Второй', 'job_status': 'leased',
                 'documents_checked': 0, 'violations': 0, 'violation_types': [], 'card_available': True}]
        markup = HOME.replace('</body>', workspace_preferences() + '</body>')
        requests, errors = [], []
        def handler(route):
            path = route.request.url.split('preferences.eis.test')[-1]
            requests.append((route.request.method, path))
            if path == '/account/home':
                route.fulfill(status=200, content_type='text/html', body=markup)
            else:
                payload = {'/me/manual-checks': rows, '/me/inn-search': {'searches': []}, '/me/profile': {'email': 'manual@example.test'}}.get(path)
                route.fulfill(status=200 if payload is not None else 404, content_type='application/json', body=json.dumps(payload))
        context.route('**/*', handler)
        page = context.new_page();page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto('https://preferences.eis.test/account/home')
        expect(page.locator('#manual-rows tr')).to_have_count(2)
        self.assertNotIn(('GET', '/me/profile'), requests, 'Opening a list must not fetch identity solely for preferences')
        page.locator('.eis-view-preferences summary').click()
        page.locator('#manual-search').fill('Второй')
        page.locator('#manual-status').select_option('leased')
        expect(page.locator('#manual-rows tr')).to_have_count(1)
        page.get_by_label('Название набора').fill('Только в работе')
        page.get_by_role('button', name='Сохранить фильтры', exact=True).click()
        expect(page.locator('.eis-preference-status')).to_contain_text('сохранён')
        page.locator('#manual-filters-reset').click()
        expect(page.locator('#manual-rows tr')).to_have_count(2)
        page.locator('.eis-preference-use').get_by_role('button', name='Применить', exact=True).click()
        expect(page.locator('#manual-search')).to_have_value('Второй')
        expect(page.locator('#manual-status')).to_have_value('leased')
        expect(page.locator('#manual-rows tr')).to_have_count(1)
        self.assertEqual(errors, [])
        self.assertTrue(all(method == 'GET' for method, _ in requests))
        self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), 390)

    def test_real_registry_button_cascade_keeps_all_density_and_action_states_readable(self):
        from tools.render_interface_preview import CONTRAST, fixtures, registry_markup
        browser = self.browser()
        api = fixtures()
        for width in (1440, 390):
            with self.subTest(width=width):
                context = browser.new_context(viewport={'width': width, 'height': 1000},
                                              locale='ru-RU', reduced_motion='reduce')
                self.addCleanup(context.close)
                errors, requests = [], []
                def handler(route):
                    path = urlsplit(route.request.url).path
                    requests.append((route.request.method, path))
                    if path == '/':
                        route.fulfill(status=200, content_type='text/html', body=registry_markup())
                    else:
                        payload = api.get(path)
                        route.fulfill(status=200 if payload is not None else 404,
                                      content_type='application/json', body=json.dumps(payload))
                context.route('**/*', handler)
                page = context.new_page();page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto('https://preferences.eis.test/')
                panel = page.locator('.eis-view-preferences[data-eis-preference-surface=registry]')
                panel.locator('summary').click()
                # Enable the ordinary action buttons using the real saved-set flow.
                panel.get_by_label('Название набора').fill('Проверка контраста')
                panel.get_by_role('button', name='Сохранить фильтры', exact=True).click()
                page.wait_for_function("!document.querySelector('.eis-view-preferences .eis-preference-use button').disabled")
                for density in ('compact', 'comfortable'):
                    panel.locator('[data-eis-density='+density+']').click()
                    for control in panel.locator('button:not(:disabled)').all():
                        if not control.is_visible():continue
                        page.mouse.move(0, 0)
                        normal = control.evaluate(CONTRAST)
                        self.assertGreaterEqual(normal['ratio'], 4.5, (width, density, control.inner_text(), 'normal', normal))
                        control.hover()
                        hovered = control.evaluate(CONTRAST)
                        self.assertGreaterEqual(hovered['ratio'], 4.5, (width, density, control.inner_text(), 'hover', hovered))
                        page.keyboard.press('Tab')
                        control.focus()
                        focused = control.evaluate(CONTRAST)
                        self.assertGreaterEqual(focused['ratio'], 4.5, (width, density, control.inner_text(), 'focus', focused))
                self.assertEqual(errors, [])
                self.assertTrue(all(method == 'GET' for method, _ in requests))
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), width)


if __name__ == '__main__':
    unittest.main()
