"""Exercise the exported fictional-data demonstration with every network blocked.

Run after export_interface_demo.py, e.g.:
  python tools/check_interface_demo.py --input /tmp/eis-preview/preview.html
Chromium and Playwright are development tools only, not application dependencies.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


REG = '32612345678'
REG2 = '32612345679'


def run(source: Path, output: Path | None = None, discovery: bool = False):
    from playwright.sync_api import expect, sync_playwright

    output = output or source.parent / 'offline-results.json'
    result = {'offline': True, 'network_requests': [], 'page_errors': [], 'checks': []}

    def check(name, ok, detail=None):
        row = {'name': name, 'ok': bool(ok)}
        if detail is not None:
            row['detail'] = detail
        result['checks'].append(row)
        print(name, 'PASS' if ok else 'FAIL', flush=True)

    def observe_fetch(frame):
        frame.locator('body').evaluate("""() => {
          const native=window.fetch;window.__offlineCalls=[];
          window.fetch=(input,options={})=>{
            options=options||{};
            const url=new URL(typeof input==='string'?input:input.url,document.baseURI);
            const method=String(options.method||input?.method||'GET').toUpperCase();
            window.__offlineCalls.push({path:url.pathname,method});
            const response=native(input,options);
            if(window.__offlineHoldPost&&method==='POST'&&url.pathname.startsWith('/me/manual-checks/'))
              return new Promise(resolve=>{window.__offlineReleasePost=()=>resolve(response)});
            return response;
          };
        }""")

    def post_count(frame):
        return frame.locator('body').evaluate("(e)=>window.__offlineCalls.filter(x=>x.method==='POST').length")

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path='/usr/bin/chromium', headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1000}, locale='ru-RU', reduced_motion='reduce')
        page.on('pageerror', lambda error: result['page_errors'].append(str(error)))

        def stop(route):
            result['network_requests'].append(route.request.url)
            route.abort()

        page.route('**/*', stop)
        try:
            page.set_content(source.read_text())
            frame = page.frame_locator('#demo-frame')
            frame.locator('body[data-eis-view="registry"]').wait_for()
            check('cabinet-route-listed', page.locator('#demo-route option[value="/#cabinet"]').count() == 1)
            frame.locator('.filters input').first.fill('Лабораторное оборудование')
            frame.locator('body').evaluate('window.__demoFrameIdentity=41')
            frame.locator('#eis-shell-overview').click()
            frame.locator('body[data-eis-view="cabinet"]').wait_for()
            check('cabinet-exclusive-view', frame.locator('body>main').is_hidden() and frame.locator('#eis223-cabinet').is_visible())
            expect(page.locator('#demo-route')).to_have_value('/#cabinet')
            check('outer-cabinet-selection-synced', page.locator('#demo-route').input_value() == '/#cabinet')
            frame.locator('#eis-shell-registry').click()
            frame.locator('body[data-eis-view="registry"]').wait_for()
            check('in-frame-view-navigation', frame.locator('body').evaluate('window.__demoFrameIdentity') == 41)
            check('in-frame-filters-preserved', frame.locator('.filters input').first.input_value() == 'Лабораторное оборудование')
            expect(page.locator('#demo-route')).to_have_value('/')
            check('outer-registry-selection-synced', page.locator('#demo-route').input_value() == '/')
            page.locator('#demo-route').select_option('/#cabinet')
            frame.locator('body[data-eis-view="cabinet"]').wait_for()
            check('direct-cabinet-dropdown-entry', frame.locator('#eis223-cabinet').is_visible() and frame.locator('body>main').is_hidden())
            observe_fetch(frame)
            number = frame.locator('#eis223-check-reg')
            submit = frame.locator('#eis223-start-check')
            form = frame.locator('#eis223-add-procurement')
            check('cabinet-no-automatic-launch', post_count(frame) == 0)
            submit.click()
            check('cabinet-empty-submit-blocked', post_count(frame) == 0 and number.get_attribute('aria-invalid') == 'true')
            check('cabinet-invalid-field-focused', number.evaluate('el=>document.activeElement===el'))
            number.fill('123')
            number.press('Enter')
            check('cabinet-invalid-enter-blocked', post_count(frame) == 0)
            frame.locator('body').evaluate('() => {window.__offlineHoldPost=true}')
            number.fill(REG)
            number.press('Enter')
            expect(form).to_have_attribute('aria-busy', 'true')
            check('cabinet-explicit-valid-post', post_count(frame) == 1)
            check('cabinet-submit-pending-disabled', submit.is_disabled())
            check('cabinet-submit-pending-readonly', number.evaluate('el=>el.readOnly'))
            submit.evaluate('el=>el.click()')
            check('cabinet-pending-click-no-duplicate', post_count(frame) == 1)
            form.evaluate("el=>el.requestSubmit(document.getElementById('eis223-start-check'))")
            check('cabinet-pending-requestSubmit-no-duplicate', post_count(frame) == 1)
            number.press('Enter')
            check('cabinet-pending-enter-no-duplicate', post_count(frame) == 1)
            frame.locator('body').evaluate('() => {window.__offlineHoldPost=false;window.__offlineReleasePost()}')
            expect(form).to_have_attribute('aria-busy', 'false')
            expect(number).to_have_value('')
            check('cabinet-submit-success-clears-field', number.input_value() == '')
            check('cabinet-submit-success-feedback', 'принят' in frame.locator('#eis223-add-status').inner_text())
            reads = frame.locator('body').evaluate("window.__offlineCalls.filter(x=>x.method==='GET'&&x.path==='/me/manual-checks').length")
            check('cabinet-submit-success-reads-refresh', reads > 0)
            frame.locator('body').evaluate("""() => {
              const native=window.fetch;window.__offlineRestoreFetch=native;
              window.fetch=(input,options={})=>String(options.method||'GET').toUpperCase()==='POST'&&String(input).includes('/me/manual-checks/')?
                Promise.resolve(new Response('{"detail":"Demo unavailable"}',{status:503,headers:{'Content-Type':'application/json'}})):native(input,options);
            }""")
            number.fill(REG2)
            submit.click()
            expect(form).to_have_attribute('aria-busy', 'false')
            check('cabinet-submit-error-keeps-field', number.input_value() == REG2 and submit.is_enabled())
            check('cabinet-submit-error-feedback', 'не удалось' in frame.locator('#eis223-add-status').inner_text().lower())
            frame.locator('body').evaluate('() => {window.fetch=window.__offlineRestoreFetch}')
            statuses = frame.locator('body').evaluate("""async()=>{
              const missing=await fetch('/api/offline-missing'),mutation=await fetch('/api/offline-missing',{method:'POST'});
              return [missing.status,mutation.status];
            }""")
            check('offline-unknown-read-rejected', statuses[0] == 404)
            check('offline-unknown-mutation-rejected', statuses[1] == 409)
            if discovery:
                discovery_checks(page, frame, check)
            first = frame.locator('#eis223-my-procurements a[href="/check-new/' + REG + '"]').first
            first.wait_for()
            first.click()
            frame.locator('#eis-quicklook').wait_for(state='visible')
            frame.locator('#eis-quicklook-content').get_by_text('Поставка лабораторного оборудования для исследовательского центра', exact=True).wait_for()
            check('cabinet-inspector-opens', frame.locator('#eis-quicklook').is_visible())
            frame.locator('#eis-quicklook-close').click()
            check('cabinet-inspector-restores-focus', first.evaluate('el=>document.activeElement===el'))
            first.click()
            frame.locator('#eis-quicklook').wait_for(state='visible')
            frame.locator('#eis-quicklook-full').click()
            frame.locator('[data-eis-section="summary"]').wait_for()
            check('offline-full-card-navigation', page.locator('#demo-route').input_value() == '/check-new/' + REG)
            if discovery:
                stages = frame.locator('.inspection-progress')
                check('offline-card-stages-present', stages.count() == 1 and stages.locator('ol>li').count() == 4)
                check('offline-card-stages-follow-saved-data', stages.locator('.inspection-step-complete').count() == 4)
                check('offline-card-stages-have-no-percentages', '%' not in stages.inner_text())
            page.locator('#demo-route').select_option('/account/manager')
            frame.locator('[data-mgr-shortcut="overdue"]').click()
            frame.locator('#mgr-task-rows tr').first.wait_for()
            check('offline-manager-filter', frame.locator('#mgr-task-status').input_value() == 'overdue' and frame.locator('#mgr-task-rows tr').count() == 1)
            page.locator('#demo-mobile').click()
            page.locator('#demo-route').select_option('/#cabinet')
            frame.locator('body[data-eis-view="cabinet"]').wait_for()
            frame.locator('#eis-shell-nav-toggle').click()
            frame.locator('#eis-shell-registry').click()
            frame.locator('body[data-eis-view="registry"]').wait_for()
            check('phone-registry-navigation', frame.locator('#eis223-cabinet').is_hidden() and frame.locator('body>main').is_visible())
            frame.locator('#eis-shell-nav-toggle').click()
            frame.locator('#eis-shell-overview').click()
            frame.locator('body[data-eis-view="cabinet"]').wait_for()
            check('phone-cabinet-navigation', frame.locator('#eis-shell-nav').is_hidden() and frame.locator('body>main').is_hidden())
            frame.locator('#eis223-start-check').click()
            check('phone-invalid-submit-focus', frame.locator('#eis223-check-reg').evaluate('el=>document.activeElement===el'))
            n = frame.locator('#eis223-check-reg').bounding_box()
            b = frame.locator('#eis223-start-check').bounding_box()
            check('phone-submit-placed-with-input', abs(n['y'] - b['y']) < 60)
            check('phone-no-overflow', frame.locator('body').evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
            if discovery:
                mobile_discovery_checks(page, frame, check)
            page.screenshot(path=str(source.parent / 'offline-cabinet-smoke.png'))
        except Exception as error:
            result['exception'] = str(error)
            print(str(error), flush=True)
        finally:
            browser.close()
    check('offline-no-network', not result['network_requests'], result['network_requests'])
    check('offline-no-page-errors', not result['page_errors'], result['page_errors'])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return bool(result.get('exception') or any(not row['ok'] for row in result['checks']))


def discovery_checks(page, frame, check):
    """Extend this gate only with the product's actual public DOM contracts."""
    from playwright.sync_api import expect
    calls_before = frame.locator('body').evaluate("window.__offlineCalls.filter(x=>x.method==='POST').length")
    attention = frame.locator('#eis-attention')
    expect(attention).to_have_attribute('aria-busy', 'false')
    expect(frame.locator('#eis-attention-count')).to_have_text('2')
    observations = frame.locator('#eis-attention-list').inner_text()
    check('offline-attention-uses-saved-states', 'истёк' in observations and 'доработку' in observations)
    check('offline-attention-deduplicates-procurements', frame.locator('#eis-attention-list>li').count() == 2)
    frame.locator('body').evaluate("() => document.dispatchEvent(new CustomEvent('eis:workspace-error',{detail:{path:'/me/assignments'}}))")
    expect(frame.locator('#eis-attention-retry')).to_be_visible()
    check('offline-attention-partial-failure-is-explicit', 'Часть данных' in frame.locator('#eis-attention-status').inner_text())
    frame.locator('#eis-attention-retry').click()
    expect(attention).to_have_attribute('aria-busy', 'false')
    expect(frame.locator('#eis-attention-retry')).to_be_hidden()
    check('offline-attention-retry-restores-state', frame.locator('#eis-attention-count').inner_text() == '2')
    opener = frame.locator('#eis-global-search-open')
    opener.click()
    dialog = frame.locator('#eis-global-search-dialog')
    expect(dialog).to_be_visible()
    check('offline-search-opens', dialog.is_visible())
    field = frame.locator('#eis-global-search-input')
    expect(field).to_be_focused()
    field.fill(REG)
    results = frame.locator('#eis-global-search-results')
    expect(results).to_contain_text(REG)
    check('offline-search-finds-number', REG in results.inner_text())
    field.fill('7710000000')
    expect(results).to_contain_text(REG2)
    check('offline-search-finds-inn', REG2 in results.inner_text())
    field.fill('эксплуатации')
    expect(results).to_contain_text(REG2)
    check('offline-search-finds-customer', REG2 in results.inner_text())
    field.fill('срок оплаты')
    expect(results).to_contain_text('Уточнить срок оплаты в проекте договора')
    check('offline-search-finds-saved-material', 'Уточнить срок оплаты в проекте договора' in results.inner_text())
    field.press('Escape')
    expect(dialog).to_be_hidden()
    check('offline-search-escape-returns-focus', opener.evaluate('el=>document.activeElement===el'))
    opener.press('Control+k')
    expect(dialog).to_be_visible()
    check('offline-search-keyboard-shortcut', dialog.is_visible())
    frame.locator('#eis-global-search-close').click()
    frame.locator('#eis-shell-registry').click()
    frame.locator('body[data-eis-view="registry"]').wait_for()
    preferences = frame.locator('[data-eis-preference-surface="registry"]')
    preferences.locator('summary').click()
    preferences.locator('[data-eis-density="compact"]').click()
    check('offline-density-changes-display', frame.locator('body>main table').first.evaluate("el=>el.classList.contains('eis-density-compact')"))
    column = preferences.locator('.eis-preference-columns input:not(:disabled)').first
    column.uncheck()
    check('offline-column-choice-preserves-identity', frame.locator('body>main table [data-eis-column-hidden="true"]').count() > 0
          and frame.locator('body>main table a[href="/check-new/' + REG + '"]').is_visible())
    column.check()
    query = frame.locator('body>main .filters input').first
    query.fill('оборудование')
    frame.locator('#preview-status').select_option('pending')
    preferences.locator('.eis-preference-save input').fill('Мой рабочий набор')
    preferences.get_by_role('button', name='Сохранить фильтры', exact=True).click()
    query.fill('другая строка')
    frame.locator('#preview-status').select_option('violations')
    preferences.get_by_role('button', name='Применить', exact=True).click()
    check('offline-saved-filter-restores-supported-values', query.input_value() == 'оборудование'
          and frame.locator('#preview-status').input_value() == 'pending')
    check('offline-filter-storage-scope-explained', bool(preferences.locator('.eis-preference-note').inner_text().strip()))
    preferences.get_by_role('button', name='Удалить набор', exact=True).click()
    check('offline-saved-filter-can-be-removed', preferences.locator('.eis-preference-use select').is_disabled())
    preferences.locator('summary').click()
    frame.locator('#eis-shell-overview').click()
    frame.locator('body[data-eis-view="cabinet"]').wait_for()
    calls_after = frame.locator('body').evaluate("window.__offlineCalls.filter(x=>x.method==='POST').length")
    check('offline-discovery-actions-start-no-check', calls_after == calls_before)
    opener.click()
    field.fill('32698765432')
    prefill = results.locator('a[href="/?check=32698765432#cabinet"]')
    expect(prefill).to_be_visible()
    prefill.click()
    frame.locator('body[data-eis-view="cabinet"]').wait_for()
    expect(frame.locator('#eis223-check-reg')).to_have_value('32698765432')
    check('offline-search-preserves-query-prefill', frame.locator('#eis223-check-reg').input_value() == '32698765432')
    check('offline-search-prefill-awaits-explicit-submit', frame.locator('#eis223-add-procurement').get_attribute('aria-busy') == 'false'
          and not frame.locator('#eis223-add-status').inner_text().strip())


def mobile_discovery_checks(page, frame, check):
    from playwright.sync_api import expect
    opener = frame.locator('#eis-global-search-open')
    opener.click()
    dialog = frame.locator('#eis-global-search-dialog')
    expect(dialog).to_be_visible()
    check('phone-search-opens', dialog.is_visible())
    check('phone-search-no-overflow', frame.locator('body').evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
    frame.locator('#eis-global-search-close').click()
    check('phone-search-close-returns-focus', opener.evaluate('el=>document.activeElement===el'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True, help='Exported preview.html')
    parser.add_argument('--output', type=Path, help='JSON report; defaults beside HTML')
    parser.add_argument('--discovery', action='store_true', help='Require the approved discovery features too')
    args = parser.parse_args()
    raise SystemExit(run(args.input, args.output, args.discovery))
