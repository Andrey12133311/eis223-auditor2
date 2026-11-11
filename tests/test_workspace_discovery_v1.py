"""Native modal search over mocked same-origin reads; no production data."""
import json
import os
import unittest
from urllib.parse import parse_qs, urlsplit

from scripts.workspace_discovery_v1 import workspace_discovery


ORIGIN = "https://discovery.example.test"
REG = "32612345678"
MANUAL = {"reg_number": REG, "customer": "Научный центр", "customer_inn": "7701234567",
          "card_available": True, "job_status": "done"}


@unittest.skipUnless(os.environ.get("EIS_DISCOVERY_BROWSER_TESTS") == "1", "Optional local Chromium")
class DiscoveryBrowserTests(unittest.TestCase):
    def test_lazy_search_safe_results_native_keyboard_and_access_expiry(self):
        from playwright.sync_api import expect, sync_playwright
        calls = []
        errors = []
        held = []
        focus_reads = []
        base_reads = []
        control = {"deny": False, "hold_base": False}
        markup = ('<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><body>'
                  '<header><a href="/">ЕИС Аудитор</a></header><main>Локальная проверка</main>'
                  + workspace_discovery() + '</body></html>')

        def handler(route):
            request = route.request
            parsed = urlsplit(request.url)
            if request.url == ORIGIN + '/':
                route.fulfill(status=200, content_type='text/html', body=markup)
                return
            calls.append((parsed.path, request.method, parsed.query))
            if parsed.path == '/me/center/search':
                query = parse_qs(parsed.query).get('q', [''])[0]
                if control['deny']:
                    route.fulfill(status=403, content_type='application/json', body='{}')
                    return
                if query == 'старый':
                    held.append(route)
                    return
                if query == '7701234567':
                    focus_reads.append(route)
                    return
                items = ([{'kind': 'finding', 'title': 'Новый материал', 'reg_number': REG},
                          {'kind': 'finding', 'title': '<img src=x onerror=alert(1)> Новый',
                           'reg_number': REG, 'url': 'javascript:alert(1)'},
                          {'kind': 'unknown', 'title': 'Новый опасный', 'url': 'https://evil.test'}]
                         if query == 'Новый' else [])
                value = {'results': items, 'truncated': False}
            else:
                value = {'/me/manual-checks': [MANUAL], '/me/assignments': [],
                         '/me/memo-archive': [], '/api/thematic/history': {'batches': []}}.get(parsed.path)
                if value is None:
                    route.abort()
                    self.fail('Unexpected read: ' + request.url)
                if control['hold_base']:
                    base_reads.append((route, value))
                    return
            route.fulfill(status=200, content_type='application/json', body=json.dumps(value))

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path='/usr/bin/chromium', chromium_sandbox=True)
            try:
                page = browser.new_page(viewport={'width': 390, 'height': 844}, has_touch=True, is_mobile=True)
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.route('**/*', handler)
                page.goto(ORIGIN + '/', wait_until='domcontentloaded')
                self.assertEqual(calls, [])
                page.keyboard.press('Control+k')
                dialog = page.locator('#eis-global-search-dialog')
                field = page.locator('#eis-global-search-input')
                expect(dialog).to_be_visible()
                expect(field).to_be_focused()
                page.wait_for_function("!document.getElementById('eis-global-search-status').textContent.includes('Загружаем')")
                field.fill('7701234567')
                expect(page.locator('#eis-global-search-results')).to_contain_text('Научный центр')
                self.assertEqual(page.locator('#eis-global-search-results a').first.get_attribute('href'), '/check-new/' + REG)
                field.press('ArrowDown')
                expect(page.locator('#eis-global-search-results a').first).to_be_focused()
                page.wait_for_timeout(300)
                self.assertEqual(len(focus_reads), 1)
                focus_reads.pop().fulfill(status=200, content_type='application/json', body='{"results":[]}')
                expect(page.locator('#eis-global-search-results a').first).to_be_focused()
                page.keyboard.press('Escape')
                expect(dialog).to_be_hidden()
                expect(page.locator('#eis-global-search-open')).to_be_focused()
                page.locator('#eis-global-search-open').click()
                control['hold_base'] = True
                page.evaluate("document.dispatchEvent(new CustomEvent('eis:workspace-error',{detail:{path:'/me/manual-checks'}}))")
                retry = page.locator('#eis-global-search-retry')
                retry.click()
                page.wait_for_timeout(100)
                self.assertEqual(len(base_reads), 4)
                retry.click()
                page.wait_for_timeout(100)
                self.assertEqual(len(base_reads), 8)
                expect(page.locator('#eis-global-search-results')).to_have_attribute('aria-busy', 'true')
                control['hold_base'] = False
                for route, value in base_reads[-4:]:
                    route.fulfill(status=200, content_type='application/json', body=json.dumps(value))
                field.fill('старый')
                page.wait_for_timeout(300)
                self.assertEqual(len(held), 1)
                field.fill('Новый')
                expect(page.locator('#eis-global-search-results')).to_contain_text('Новый материал')
                try:
                    held.pop().fulfill(status=200, content_type='application/json', body=json.dumps({
                        'results': [{'kind': 'finding', 'title': 'Старый материал', 'reg_number': REG}]}))
                except Exception:
                    pass  # The browser may have already cancelled the old transport.
                self.assertNotIn('Старый материал', page.locator('#eis-global-search-results').inner_text())
                self.assertEqual(page.locator('#eis-global-search-results :is(img,script,[onerror])').count(), 0)
                self.assertEqual(page.locator('#eis-global-search-results a').count(), 2)
                self.assertTrue(all(href == '/account/workbench' for href in
                                    page.locator('#eis-global-search-results a').evaluate_all('(xs)=>xs.map(x=>x.getAttribute("href"))')))
                control['deny'] = True
                field.fill('закрыто')
                expect(page.locator('#eis-global-search-status')).to_contain_text('доступ ограничен')
                links = page.locator('#eis-global-search-results a')
                self.assertEqual(links.count(), 1)
                self.assertEqual(links.first.get_attribute('href'), '/account/login')
                self.assertNotIn('Научный центр', page.locator('#eis-global-search-results').inner_text())
                self.assertTrue(all(method == 'GET' for _, method, _ in calls))
                self.assertEqual(errors, [])
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), 390)
                self.assertGreaterEqual(page.locator('#eis-global-search-close').bounding_box()['height'], 44)
            finally:
                for route in held + focus_reads + [route for route, _ in base_reads]:
                    try:
                        route.abort()
                    except Exception:
                        pass
                browser.close()


if __name__ == '__main__':
    unittest.main()
