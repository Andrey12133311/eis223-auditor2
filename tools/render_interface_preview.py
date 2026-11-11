"""Render and inspect real UI builders using fictional, fully local fixtures.

No request is sent to production. The shared registry is a structural fixture,
not the private restored production runtime. This runner validates presentation,
navigation and form states; it makes no assertion about backend correctness.

Run with PYTHONPATH=.:scripts python tools/render_interface_preview.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]
from scripts.account_site_ui_v1 import login_portal, site_header
from scripts.account_pages_v1 import HOME, THEMATIC, MEMOS
from scripts.v361_home_shell import home_shell
from scripts.v370_staff_ui import render_workbench
from scripts.v368_manager_ui import render_manager
from scripts.v371_profile_ui import render_profile
from scripts.v343_fast_cards import render as render_card

ORIGIN = 'https://preview.eis.test'
STAMP = '2026-11-11T12:00:00Z'
REG = '32612345678'
LONG_NAME = 'Демонстрационное учреждение научных исследований и инженерно-технического обеспечения закупочной деятельности'
PERSON = {'id': 'preview-user', 'email': 'preview@example.test', 'last_name': 'Иванов',
          'first_name': 'Александр', 'patronymic': 'Сергеевич',
          'full_name': 'Иванов Александр Сергеевич', 'role': 'manager', 'status': 'approved', 'completed': True}
PURCHASE = {'reg_number': REG, 'title': 'Поставка лабораторного оборудования для исследовательского центра',
            'customer': LONG_NAME, 'customer_inn': '7700000000', 'method': 'Электронный конкурс',
            'price': '1 250 000,00 ₽', 'documents_checked': 8, 'documents_found': 8,
            'violations': 1, 'job_status': 'completed', 'card_available': True,
            'legal_review_state': 'completed', 'legal_review_documents': 8,
            'started_at': STAMP, 'published': '2026-11-05', 'submission_end': '2026-11-19',
            'url': 'https://zakupki.gov.ru/223/purchase/public/purchase/info/common-info.html?regNumber=' + REG}
TASK = {'id': 'preview-task', 'title': 'Проверить документацию и сроки размещения закупки',
        'reg_number': REG, 'status': 'in_progress', 'due_date': '2026-11-18',
        'assignee_id': PERSON['id'], 'assignee_name': PERSON['full_name'],
        'created_at': STAMP, 'note': 'Демонстрационное поручение для проверки переноса текста.'}
BATCH = {'id': 'preview-batch', 'title': 'Проверка условий закупок оборудования', 'status': 'active',
         'categories': ['dates', 'docs'], 'purchases': 12, 'violations': 2,
         'created_at': STAMP, 'date_from': '2026-11-01', 'date_to': '2026-11-10'}
MEMO = {'id': 'preview-memo', 'reg_number': REG, 'created_at': STAMP, 'status': 'submitted',
        'downloadable': True, 'formats': ['odt', 'pdf'], 'title': 'Служебная записка',
        'user_id': PERSON['id'], 'user_name': PERSON['full_name']}
NOTIFICATION = {'id': 'preview-notification', 'title': 'Результаты проверки готовы',
                'detail': 'Сохранены документы и результаты демонстрационной закупки.',
                'created_at': STAMP, 'read': False}


def fixtures():
    return {
        '/me/profile': PERSON,
        '/auth/me': PERSON,
        '/me/summary': {'manual_checks': 3, 'memos': 1},
        '/me/manual-checks': [PURCHASE],
        '/api/thematic/history': {'batches': [BATCH]},
        '/api/thematic/unassigned': {'batches': []},
        '/me/memo-archive': [MEMO],
        '/me/notifications': [NOTIFICATION],
        '/me/alerts': [NOTIFICATION],
        '/me/favorites': [],
        '/me/assignments': [TASK],
        '/me/assignments/memos': [],
        '/me/workbench/overview': {'checks_total': 12, 'reviews_completed': 8,
            'unique_confirmed_findings': 2, 'documents_checked': 48, 'attention_required': 1, 'memo_count': 1},
        '/me/center/dashboard': {'task_active': 1, 'campaign_active': 0, 'unread_notifications': 1,
            'finding_decisions': {'confirmed': 2}, 'as_of': STAMP, 'tasks': [TASK],
            'campaigns': [], 'memo_reviews': {'submitted': 1}},
        '/me/center/progress': {'available': True, 'note': 'Локальный демонстрационный набор.',
            'checks': [{**PURCHASE, 'stage': 'Проверка завершена', 'legal_review_completed': True}]},
        '/me/center/saved-searches': [],
        '/me/center/search': {'results': [], 'scope': 'Локальная демонстрация'},
        '/me/workbench/templates': [],
        '/me/campaigns': [], '/me/campaign-goals/violations': {'findings': []}, '/me/inn-search': {'searches': []},
        '/me/campaigns/options': {'categories': [], 'templates': []},
        '/manager/dashboard': {'staff': 4, 'tasks_total': 12, 'tasks_overdue': 1,
            'tasks_pending': 3, 'real_confirmed_findings': 8, 'manager_approved_findings': 5,
            'real_documents_checked': 48, 'memos_pending': 1,
            'metric_note': 'Показатели из локальных демонстрационных данных.',
            'tasks_by_day': [{'day': '2026-11-09', 'count': 2}, {'day': '2026-11-10', 'count': 4}]},
        '/manager/tasks': [TASK], '/manager/team': [PERSON], '/manager/team/available': [],
        '/manager/memos': [MEMO], '/manager/alerts': [], '/manager/thematic': [BATCH],
        '/manager/quality': {'items': [], 'summary': {}}, '/manager/campaigns': [],
        '/manager/campaigns/options': {'categories': [], 'templates': [], 'staff': []},
        '/manager/finding-submissions': [], '/manager/history': [],
        '/manager/admin/mail/status': {'configured': False, 'note': 'Локальная демонстрация'},
    }


def registry_markup():
    rows = ''.join('<tr><td><a href="/check-new/' + REG + '">№ ' + REG + '</a><br><small>05.11.2026</small></td>'
        '<td>' + LONG_NAME + '<br><small>ИНН 7700000000</small></td><td>Поставка лабораторного оборудования</td>'
        '<td>1 250 000 ₽</td><td class="status-violation">Требует внимания</td></tr>' for _ in range(4))
    return ('<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" '
        'content="width=device-width,initial-scale=1"><title>Локальная демонстрация реестра</title>'
        '<style>*{box-sizing:border-box}body{margin:0}small{color:#636366}label{display:flex;flex-direction:column;gap:5px}'
        '.filters{display:flex;flex-wrap:wrap}input,select,button{padding:8px 11px;font:inherit}button{cursor:pointer}'
        '#v257-tabs{display:flex;gap:6px;margin:0 0 14px;flex-wrap:wrap}#v257-tabs a{border:1px solid;border-radius:8px;text-decoration:none}</style>'
        + home_shell().decode() + '</head><body>' + site_header('manager').decode()
        + '<main><div class="stats"><div class="stat"><small>Закупок в реестре</small><b>128</b></div>'
        '<div class="stat"><small>Документов проверено</small><b>864</b></div><div class="stat"><small>Требует внимания</small><b>12</b></div></div>'
        '<nav id="v257-tabs"><a href="/" aria-current="page">Все закупки</a><a href="/">С нарушениями</a></nav>'
        '<form class="filters"><label>Поиск<input placeholder="Номер закупки, заказчик или ИНН"></label>'
        '<label>Состояние<select><option>Все состояния</option></select></label><label>Дата размещения<input type="date"></label>'
        '<button type="button">Применить</button></form><table><thead><tr><th>Номер закупки</th><th>Заказчик</th>'
        '<th>Предмет закупки</th><th>Стоимость</th><th>Проверка</th></tr></thead><tbody>' + rows + '</tbody></table></main></body></html>')


def pages():
    finding = {'code': 'PREVIEW-ONLY', 'title': 'Требуется проверить срок размещения документов',
        'law': 'Демонстрационный пример основания', 'evidence': 'Фрагмент документа для проверки оформления и переноса длинного текста.',
        'document': 'Документация о закупке лабораторного оборудования.pdf',
        'source_url': PURCHASE['url'], 'status': 'check', 'item': 'Срок размещения документации'}
    docs = [{'name': 'Документация о закупке лабораторного оборудования и техническое задание.pdf',
             'doc_type': 'Документация', 'status': 'Проверен', 'url': PURCHASE['url']}]
    card = render_card(REG, (PURCHASE, [], [finding], docs, 0, 1, 1))
    return {'/': ('registry', registry_markup()), '/account/login': ('login', login_portal()),
        '/account/register': ('register', login_portal('register')),
        '/account/workbench': ('staff', render_workbench()),
        '/account/manager': ('manager', render_manager('manager')),
        '/account/profile': ('profile', render_profile()), '/account/home': ('manual', HOME),
        '/account/thematic': ('thematic', THEMATIC), '/account/memos': ('memos', MEMOS),
        '/check-new/' + REG: ('card', card)}


CONTRAST = r"""el => {
 const s=getComputedStyle(el),nums=c=>(c.match(/[\d.]+/g)||[]).map(Number);
 let fg=nums(s.color),bg=[255,255,255];
 for(let n=el;n;n=n.parentElement){const c=nums(getComputedStyle(n).backgroundColor);if(c.length>=3&&(c.length<4||c[3]===1)){bg=c;break}}
 const lum=c=>c.slice(0,3).map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4}).reduce((v,x,i)=>v+x*[.2126,.7152,.0722][i],0);
 const a=lum(fg),b=lum(bg);return {color:s.color,background:bg,ratio:(Math.max(a,b)+.05)/(Math.min(a,b)+.05)};
}"""


def run(output: Path):
    from playwright.sync_api import sync_playwright
    output.mkdir(parents=True, exist_ok=True)
    documents = pages()
    api = fixtures()
    for _, (name, markup) in documents.items():
        (output / (name + '.html')).write_text(markup)
    results = {'fixture_only': True, 'registry_is_structural_fixture': True,
               'checks': [], 'failures': [], 'screenshots': [], 'page_errors': [], 'unknown_api_paths': []}
    def check(name, ok, detail=None):
        record = {'name': name, 'ok': bool(ok)}
        if detail is not None:
            record['detail'] = detail
        results['checks'].append(record)
        if not ok:
            results['failures'].append(record)
    def handler(route):
        request = route.request
        parsed = urlsplit(request.url)
        if parsed.netloc != 'preview.eis.test':
            route.abort()
            return
        if request.resource_type == 'document' and parsed.path in documents:
            route.fulfill(status=200, content_type='text/html', body=documents[parsed.path][1])
            return
        payload = api.get(parsed.path)
        if payload is None:
            if parsed.path not in results['unknown_api_paths']:
                results['unknown_api_paths'].append(parsed.path)
            payload = []
        # Every request, including a hypothetical action, is fulfilled locally.
        route.fulfill(status=200, content_type='application/json', body=json.dumps(payload))
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path='/usr/bin/chromium', headless=True)
        for width in (1440, 768, 390):
            context = browser.new_context(viewport={'width': width, 'height': 1000},
                device_scale_factor=1, reduced_motion='reduce')
            context.route('**/*', handler)
            page = context.new_page()
            page.on('pageerror', lambda error: results['page_errors'].append(str(error)))
            for path, (name, _) in documents.items():
                page.goto(ORIGIN + path)
                page.wait_for_timeout(150)
                if name == 'registry':
                    page.locator('#eis223-toggle-cabinet').click()
                    page.wait_for_timeout(100)
                    check(f'{name}-{width}-cabinet-toggle', page.locator('#eis223-cabinet').is_visible())
                if name == 'login':
                    page.locator('#tab-register').click()
                    check(f'login-{width}-registration-tab', page.locator('#registration-form').is_visible())
                    page.locator('#tab-login').click()
                    check(f'login-{width}-login-tab', page.locator('#login-form').is_visible())
                if name == 'staff':
                    page.locator('[data-staff-tab="bulk"]').click()
                    check(f'staff-{width}-bulk-tab', page.locator('[data-staff-section="bulk"]').is_visible())
                    page.locator('[data-staff-tab="overview"]').click()
                if name == 'manager':
                    page.locator('[data-tab="tasks"]').click()
                    check(f'manager-{width}-tasks-tab', page.locator('[data-section="tasks"]').is_visible())
                    page.locator('[data-tab="overview"]').click()
                if name == 'card':
                    disclosure = page.locator('#audit-review')
                    if disclosure.count():
                        before = disclosure.get_attribute('open') is not None
                        disclosure.locator('summary').click()
                        check(f'card-{width}-disclosure', (disclosure.get_attribute('open') is not None) != before)
                overflow = page.evaluate('({width:innerWidth,scroll:document.documentElement.scrollWidth})')
                check(f'{name}-{width}-viewport', overflow['scroll'] <= width + 1, overflow)
                check(f'{name}-{width}-one-h1', page.locator('h1').count() == (0 if name == 'registry' else 1))
                for selector in ('#eis223-topbar-fullname', '#staff-user-name', '#mgr-my-name', '[aria-selected="true"]'):
                    matches = page.locator(selector)
                    if matches.count() and matches.first.is_visible():
                        pair = matches.first.evaluate(CONTRAST)
                        check(f'{name}-{width}-contrast-{selector}', pair['ratio'] >= 4.5, pair)
                # Real keyboard input should produce a visible focus indication.
                page.locator('body').click(position={'x': 1, 'y': 1})
                page.keyboard.press('Tab')
                focus = page.evaluate("""() => {const e=document.activeElement,s=getComputedStyle(e);return {tag:e.tagName,visible:e.matches(':focus-visible'),outline:s.outlineStyle}}""")
                check(f'{name}-{width}-keyboard-focus', focus['tag'] != 'BODY' and focus['visible'] and focus['outline'] != 'none', focus)
                shot = output / f'{name}-{width}.png'
                page.screenshot(path=str(shot), full_page=True)
                results['screenshots'].append(str(shot))
            context.close()
        touch = browser.new_context(viewport={'width': 390, 'height': 844}, has_touch=True,
                                    is_mobile=True, reduced_motion='reduce')
        touch.route('**/*', handler)
        page = touch.new_page()
        touch_targets = {'/': ['#eis223-toggle-cabinet', '#eis223-logout'],
            '/account/login': ['.submit', '#login-email', '#tab-login'],
            '/account/workbench': ['[data-staff-tab="overview"]', '#staff-refresh', 'header a', '#staff-global-search button'],
            '/account/manager': ['[data-tab="overview"]', 'header a'],
            '/account/profile': ['button[type="submit"]', 'input'],
            '/check-new/' + REG: ['a.action']}
        for path, targets in touch_targets.items():
            page.goto(ORIGIN + path)
            page.wait_for_timeout(100)
            for target in targets:
                for index, element in enumerate(page.locator(target).all()):
                    if element.is_visible():
                        size = element.bounding_box()
                        check(f'touch-{path}-{target}-{index}', size['height'] >= 44, size)
        touch.close()
        browser.close()
    check('local-fixture-no-page-errors', not results['page_errors'], sorted(set(results['page_errors'])))
    check('local-fixture-all-api-paths-explicit', not results['unknown_api_paths'], results['unknown_api_paths'])
    (output / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"{len(results['checks'])} browser checks, {len(results['failures'])} failures; results: {output / 'results.json'}")
    for failure in results['failures']:
        print(json.dumps(failure, ensure_ascii=False))
    if results['page_errors']:
        print('Fixture page errors:', json.dumps(sorted(set(results['page_errors'])), ensure_ascii=False))
    return bool(results['failures'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('/workspace/eis223-preview/after'))
    args = parser.parse_args()
    raise SystemExit(run(args.output))
