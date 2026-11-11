"""Navigation and disclosure contracts for the standalone account screens."""
import unittest
import json
import os
from urllib.parse import urlsplit
from html.parser import HTMLParser

from scripts.account_pages_v1 import HOME, LOGIN, MEMOS, REGISTER, THEMATIC


class PageElements(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.elements = []
        self.stack = []
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.elements.append((tag, attrs, tuple(self.stack)))
        if tag not in {'meta', 'input', 'link', 'br', 'hr', 'img'}:
            self.stack.append((tag, attrs.get('id', ''), attrs.get('class', '')))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break


class AccountPagesExperienceTests(unittest.TestCase):
    def test_protected_screens_identify_one_current_section(self):
        for markup, active in ((HOME, '/account/home'),
                               (THEMATIC, '/account/thematic'),
                               (MEMOS, '/account/memos')):
            with self.subTest(active=active):
                elements = PageElements(markup).elements
                current = [attrs['href'] for tag, attrs, _ in elements
                           if tag == 'a' and attrs.get('aria-current') == 'page']
                self.assertEqual(current, [active])
                self.assertEqual(sum(tag == 'h1' for tag, _, _ in elements), 1)
                ids = [attrs['id'] for _, attrs, _ in elements if 'id' in attrs]
                self.assertEqual(len(ids), len(set(ids)))
                hrefs = {attrs.get('href') for tag, attrs, _ in elements if tag == 'a'}
                self.assertTrue({'/', '/#cabinet', '/account/profile'}.issubset(hrefs))
                self.assertNotIn('/account/admin', hrefs)
                self.assertNotIn('/account/manager', hrefs)

    def test_public_authentication_does_not_present_private_navigation(self):
        for markup in (LOGIN, REGISTER):
            hrefs = {attrs.get('href') for tag, attrs, _
                     in PageElements(markup).elements if tag == 'a'}
            self.assertFalse({'/#cabinet', '/account/profile', '/account/home',
                              '/account/thematic', '/account/memos'} & hrefs)

    def test_memo_creation_is_a_native_disclosure_without_changing_form_controls(self):
        elements = PageElements(MEMOS).elements
        panel = next(attrs for tag, attrs, _ in elements
                     if tag == 'details' and attrs.get('id') == 'memo-create-panel')
        self.assertNotIn('open', panel)
        form = next(ancestors for tag, attrs, ancestors in elements
                    if tag == 'form' and attrs.get('id') == 'memo-save')
        self.assertTrue(any(tag == 'details' and ident == 'memo-create-panel'
                            for tag, ident, _ in form))
        expected = {'memo-requisites', 'memo-recipient', 'memo-executor', 'memo-phone',
                    'memo-save-requisites', 'memo-create'}
        self.assertTrue(expected.issubset({attrs.get('id') for _, attrs, _ in elements}))
        for tag, attrs, ancestors in elements:
            if tag == 'input' and any(parent == 'form' and ident == 'memo-save'
                                      for parent, ident, _ in ancestors):
                self.assertTrue(any(parent == 'label' for parent, _, _ in ancestors))

    def test_load_failures_have_explicit_non_submitting_retry_controls(self):
        for markup, prefix in ((HOME, 'manual'), (THEMATIC, 'thematic'), (MEMOS, 'memo')):
            with self.subTest(prefix=prefix):
                attrs = next(attrs for tag, attrs, _ in PageElements(markup).elements
                             if tag == 'button' and attrs.get('id') == prefix + '-retry')
                self.assertEqual(attrs.get('type'), 'button')
                self.assertIn('hidden', attrs)


@unittest.skipUnless(os.environ.get('EIS_ACCOUNT_PAGES_BROWSER_TESTS') == '1',
                     'Set EIS_ACCOUNT_PAGES_BROWSER_TESTS=1 for the local Chromium regression')
class AccountPagesBrowserTests(unittest.TestCase):
    def test_filtered_empty_reset_survives_blur_and_mobile_actions_fit(self):
        from playwright.sync_api import sync_playwright

        origin = 'https://account-pages.example.test'
        number = '32612345678'
        memo = dict(id='memo-fixture', reg_number=number,
                    created_at='2026-11-11T12:00:00Z', downloadable=True)
        purchase = dict(reg_number=number, customer='Демонстрационный заказчик',
                        job_status='completed', card_available=True)
        errors = []
        requests = []
        documents = {'/account/memos': MEMOS, '/account/home': HOME}

        def handler(route):
            parsed = urlsplit(route.request.url)
            requests.append((route.request.method, parsed.path))
            if parsed.netloc != 'account-pages.example.test':
                self.fail('Unexpected external request: ' + route.request.url)
            if parsed.path in documents:
                route.fulfill(content_type='text/html', body=documents[parsed.path])
                return
            fixtures = {'/me/memo-archive': [memo], '/me/assignments/memos': [],
                        '/me/manual-checks': [purchase], '/me/inn-search': {'searches': []}}
            self.assertIn(parsed.path, fixtures)
            route.fulfill(content_type='application/json', body=json.dumps(fixtures[parsed.path]))

        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH', '/usr/bin/chromium'),
                                        headless=True)
            try:
                for width in (1440, 390):
                    with self.subTest(width=width):
                        context = browser.new_context(viewport={'width': width, 'height': 900},
                                                      locale='ru-RU', reduced_motion='reduce')
                        context.route('**/*', handler)
                        page = context.new_page()
                        page.on('pageerror', lambda error: errors.append(str(error)))
                        page.goto(origin + '/account/memos')
                        page.locator('a[download]').first.wait_for()
                        search = page.locator('#memo-search')
                        search.fill('11111111111')
                        # Click the newly rendered empty-state action while the search
                        # still owns focus: its blur emits change before click lands.
                        page.locator('#memo-rows button', has_text='Сбросить фильтры').click()
                        self.assertEqual(search.input_value(), '')
                        self.assertEqual(page.locator('a[download]').count(), 2)
                        self.assertFalse(page.locator('#memo-create-panel').evaluate('e=>e.open'))
                        active = page.locator('.account-sections [aria-current=page]')
                        self.assertEqual(active.get_attribute('href'), '/account/memos')
                        self.assertTrue(active.evaluate('e=>{const r=e.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth}'))
                        if width == 390:
                            self.assertTrue(page.locator('#memo-rows td[data-label="Действия"]').evaluate(
                                'e=>{const r=e.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth}'))
                            self.assertTrue(page.locator('.memo-action a').evaluate_all(
                                'els=>els.every(e=>e.getBoundingClientRect().height>=44)'))
                        page.goto(origin + '/account/home')
                        page.locator('#manual-rows tr').wait_for()
                        self.assertTrue(page.locator('#manual-inn-table').is_hidden())
                        if width == 390:
                            self.assertTrue(page.locator('#manual-rows td[data-label="Действия"]').evaluate(
                                'e=>{const r=e.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth}'))
                            self.assertTrue(page.locator('.manual-actions a,.manual-actions button').evaluate_all(
                                'els=>els.every(e=>e.getBoundingClientRect().height>=44)'))
                        self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
                        context.close()
            finally:
                browser.close()
        self.assertEqual(errors, [])
        self.assertTrue(all(method == 'GET' for method, _ in requests))


if __name__ == '__main__':
    unittest.main()
