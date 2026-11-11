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
REG2 = '32612345679'
REG3 = '32612345680'
LONG_NAME = 'Демонстрационное учреждение научных исследований и инженерно-технического обеспечения закупочной деятельности'
PERSON = {'id': 'preview-user', 'email': 'preview@example.test', 'last_name': 'Иванов',
          'first_name': 'Александр', 'patronymic': 'Сергеевич',
          'full_name': 'Иванов Александр Сергеевич', 'role': 'manager', 'status': 'approved', 'completed': True}
PURCHASE = {'reg_number': REG, 'title': 'Поставка лабораторного оборудования для исследовательского центра',
            'customer': LONG_NAME, 'customer_inn': '7700000000', 'method': 'Электронный конкурс',
            'price': '1 250 000,00 ₽', 'documents_checked': 3, 'documents_found': 3,
            'violations': 1, 'job_status': 'completed', 'card_available': True,
            'legal_review_state': 'completed', 'legal_review_documents': 3,
            'started_at': STAMP, 'published': '2026-11-05', 'submission_end': '2026-11-19',
            'url': 'https://zakupki.gov.ru/223/purchase/public/purchase/info/common-info.html?regNumber=' + REG}
PURCHASE2 = {**PURCHASE, 'reg_number': REG2, 'title': 'Обслуживание инженерных систем здания',
             'customer': 'Демонстрационный центр эксплуатации', 'price': '840 000,00 ₽',
             'documents_checked': 2, 'documents_found': 5, 'violations': 0,
             'job_status': 'leased', 'legal_review_state': 'running', 'legal_review_documents': 2,
             'url': PURCHASE['url'].replace(REG, REG2)}
PURCHASE3 = {**PURCHASE, 'reg_number': REG3, 'title': 'Поставка офисной мебели',
             'customer': 'Демонстрационное учреждение развития', 'price': '420 000,00 ₽',
             'documents_checked': 1, 'documents_found': 1, 'legal_review_documents': 1,
             'url': PURCHASE['url'].replace(REG, REG3)}
TASK = {'id': 'preview-task', 'title': 'Проверить документацию и сроки размещения закупки',
        'reg_number': REG, 'status': 'in_progress', 'due_date': '2026-11-18',
        'overdue': False, 'employee': PERSON['email'], 'user_id': PERSON['id'],
        'assignee_id': PERSON['id'], 'assignee_name': PERSON['full_name'],
        'created_at': STAMP, 'note': 'Демонстрационное поручение для проверки переноса текста.'}
TASK_PENDING = {**TASK, 'id': 'preview-task-pending', 'status': 'submitted',
                'title': 'Рассмотреть результаты проверки документации', 'overdue': False}
TASK_OVERDUE = {**TASK, 'id': 'preview-task-overdue', 'reg_number': REG2,
                'title': 'Проверить условия договора на обслуживание систем',
                'due_date': '2026-11-09', 'overdue': True}
TASK_DONE = {**TASK, 'id': 'preview-task-done', 'status': 'completed',
             'title': 'Завершённое поручение по архивной закупке', 'due_date': '2026-11-08', 'overdue': False}
TASKS = [TASK, TASK_PENDING, TASK_OVERDUE, TASK_DONE]
BATCH = {'id': 'preview-batch', 'title': 'Проверка условий закупок оборудования', 'status': 'active',
         'categories': ['dates', 'docs'], 'purchases': 12, 'violations': 2,
         'created_at': STAMP, 'date_from': '2026-11-01', 'date_to': '2026-11-10'}
MEMO = {'id': 'preview-memo', 'reg_number': REG, 'created_at': STAMP, 'status': 'submitted',
        'downloadable': True, 'formats': ['odt', 'pdf'], 'title': 'Служебная записка',
        'user_id': PERSON['id'], 'user_name': PERSON['full_name']}
MEMO_APPROVED = {**MEMO, 'id': 'preview-memo-approved', 'reg_number': REG2,
                 'status': 'approved', 'employee': PERSON['email'], 'submitted_at': STAMP}
MEMO = {**MEMO, 'employee': PERSON['email'], 'submitted_at': STAMP}
NOTIFICATION = {'id': 'preview-notification', 'title': 'Результаты проверки готовы',
                'detail': 'Сохранены документы и результаты демонстрационной закупки.',
                'created_at': STAMP, 'read': False}


def fixtures():
    return {
        '/me/profile': PERSON,
        '/auth/me': PERSON,
        '/me/summary': {'manual_checks': 3, 'memos': 1},
        '/me/manual-checks': [PURCHASE, PURCHASE2, PURCHASE3],
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
        '/me/center/dashboard': {'task_active': 3, 'campaign_active': 0, 'unread_notifications': 1,
            'finding_decisions': {'confirmed': 2}, 'as_of': STAMP, 'tasks': [TASK, TASK_PENDING, TASK_OVERDUE],
            'campaigns': [], 'memo_reviews': {'submitted': 1}},
        '/me/center/progress': {'available': True, 'note': 'Локальный демонстрационный набор.',
            'checks': [{**PURCHASE, 'stage': 'Проверка завершена', 'legal_review_completed': True}]},
        '/me/center/saved-searches': [],
        '/me/center/search': {'results': [], 'scope': 'Локальная демонстрация'},
        '/me/workbench/templates': [],
        '/me/campaigns': [], '/me/campaign-goals/violations': {'findings': []}, '/me/inn-search': {'searches': []},
        '/me/campaigns/options': {'categories': [], 'templates': []},
        '/manager/dashboard': {'staff': 1, 'tasks_total': 4, 'tasks_overdue': 1,
            'tasks_pending': 1, 'real_confirmed_findings': 8, 'manager_approved_findings': 5,
            'real_documents_checked': 48, 'memos_pending': 1,
            'metric_note': 'Показатели из локальных демонстрационных данных.',
            'tasks_by_day': [{'day': '2026-11-09', 'count': 2}, {'day': '2026-11-10', 'count': 4}]},
        '/manager/tasks': TASKS, '/manager/team': [PERSON], '/manager/team/available': [],
        '/manager/memos': [MEMO, MEMO_APPROVED], '/manager/alerts': [], '/manager/thematic': [BATCH],
        '/manager/quality': {'items': [], 'summary': {}}, '/manager/campaigns': [],
        '/manager/campaigns/options': {'categories': [], 'templates': [], 'staff': []},
        '/manager/finding-submissions': [], '/manager/history': [],
        '/manager/admin/mail/status': {'configured': False, 'note': 'Локальная демонстрация'},
    }


def registry_markup():
    rows = ''
    for index, purchase in enumerate([PURCHASE, PURCHASE2, PURCHASE3]):
        state = 'Проверка выполняется' if index == 1 else 'Требует внимания'
        state_class = 'status-pending' if index == 1 else 'status-violation'
        rows += ('<tr data-reg="' + purchase['reg_number'] + '"><td><a href="/check-new/' + purchase['reg_number'] + '">№ '
            + purchase['reg_number'] + '</a><br><small>05.11.2026</small></td><td>'
            + purchase['customer'] + '<br><small>ИНН 7700000000</small></td><td>' + purchase['title']
            + '</td><td>' + purchase['price'] + '</td><td class="' + state_class + '">' + state + '</td></tr>')
    return ('<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" '
        'content="width=device-width,initial-scale=1"><title>Локальная демонстрация реестра</title>'
        '<style>*{box-sizing:border-box}body{margin:0}small{color:#636366}label{display:flex;flex-direction:column;gap:5px}'
        '.filters{display:flex;flex-wrap:wrap}input,select,button{padding:8px 11px;font:inherit}button{cursor:pointer}'
        '#v257-tabs{display:flex;gap:6px;margin:0 0 14px;flex-wrap:wrap}#v257-tabs a{border:1px solid;border-radius:8px;text-decoration:none}</style>'
        + home_shell().decode() + '</head><body>' + site_header('manager').decode()
        + '<main><div class="stats"><div class="stat"><small>Закупок в реестре</small><b>3</b></div>'
        '<div class="stat"><small>Документов проверено</small><b>6</b></div><div class="stat"><small>Требует внимания</small><b>2</b></div></div>'
        '<nav id="v257-tabs"><a href="/" aria-current="page">Все закупки</a><a href="/">С нарушениями</a></nav>'
        '<form class="filters"><label>Поиск<input placeholder="Номер закупки, заказчик или ИНН"></label>'
        '<label>Состояние<select id="preview-status"><option value="">Все состояния</option><option value="violations">С нарушениями</option><option value="pending">Проверка не завершена</option><option value="missing_docs">Документы не получены</option></select></label><label>Дата размещения<input type="date"></label>'
        '<button id="preview-apply" type="button">Применить</button></form><table><thead><tr><th>Номер закупки</th><th>Заказчик</th>'
        '<th>Предмет закупки</th><th>Стоимость</th><th>Проверка</th></tr></thead><tbody>' + rows + '</tbody></table></main></body></html>')


def pages():
    review = {'code': 'PREVIEW-REVIEW', 'title': 'Требуется уточнить срок размещения документов',
        'law': 'Демонстрационный пример основания', 'evidence': 'Необходимо сверить дату публикации изменения с датой решения в оригинале.',
        'document': 'Изменения документации.pdf', 'source_url': PURCHASE['url'],
        'status': 'check', 'item': 'Срок размещения изменения документации'}
    finding = {'code': 'PREVIEW-CONFIRMED', 'title': 'Противоречие в условиях оплаты',
        'law': 'Демонстрационный пример применённой нормы',
        'evidence': 'В пункте 4.2 проекта договора указан срок 30 рабочих дней, а в разделе 7 документации — 10 рабочих дней.',
        'match_text': 'Оплата производится в течение 30 рабочих дней после подписания акта.',
        'document': 'Проект договора.pdf', 'source_url': PURCHASE['url'], 'location': 'пункт 4.2',
        'effective_status': 'confirmed'}
    docs = [{'name': 'Документация о закупке лабораторного оборудования и техническое задание.pdf',
             'doc_type': 'Документация', 'status': 'checked', 'url': PURCHASE['url']},
            {'name': 'Проект договора.pdf', 'doc_type': 'Проект договора', 'status': 'checked', 'url': PURCHASE['url']},
            {'name': 'Изменения документации.pdf', 'doc_type': 'Изменение', 'status': 'checked', 'url': PURCHASE['url']}]
    card = render_card(REG, (PURCHASE, [finding], [review], docs, 1, 1, 3),
                       legal_review={'state': 'completed', 'documents_read': 3})
    pending_docs = [
        {'name': 'Документация на обслуживание инженерных систем.pdf', 'doc_type': 'Документация', 'status': 'checked', 'url': PURCHASE2['url']},
        {'name': 'Проект договора на обслуживание.pdf', 'doc_type': 'Проект договора', 'status': 'checked', 'url': PURCHASE2['url']},
    ]
    card2 = render_card(REG2, (PURCHASE2, [], [], pending_docs, 0, 0, 2),
                        legal_review={'state': 'running', 'documents_read': 2})
    incomplete = {**finding, 'code': 'PREVIEW-INCOMPLETE', 'title': 'Неполное доказательство условия оплаты', 'evidence': '',
                  'document': 'Проект договора поставки мебели.pdf', 'source_url': PURCHASE3['url']}
    office_docs = [{'name': 'Проект договора поставки мебели.pdf', 'doc_type': 'Проект договора',
                    'status': 'checked', 'url': PURCHASE3['url']}]
    card3 = render_card(REG3, (PURCHASE3, [incomplete], [], office_docs, 1, 0, 1),
                        legal_review={'state':'completed','documents_read':1})
    return {'/': ('registry', registry_markup()), '/account/login': ('login', login_portal()),
        '/account/register': ('register', login_portal('register')),
        '/account/workbench': ('staff', render_workbench()),
        '/account/manager': ('manager', render_manager('manager')),
        '/account/profile': ('profile', render_profile()), '/account/home': ('manual', HOME),
        '/account/thematic': ('thematic', THEMATIC), '/account/memos': ('memos', MEMOS),
        '/check-new/' + REG: ('card', card), '/check-new/' + REG2: ('card-pending', card2),
        '/check-new/' + REG3: ('card-incomplete', card3)}


CONTRAST = r"""el => {
 const s=getComputedStyle(el),nums=c=>(c.match(/[\d.]+/g)||[]).map(Number);
 let fg=nums(s.color),bg=[255,255,255];
 for(let n=el;n;n=n.parentElement){const c=nums(getComputedStyle(n).backgroundColor);if(c.length>=3&&(c.length<4||c[3]===1)){bg=c;break}}
 const lum=c=>c.slice(0,3).map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4}).reduce((v,x,i)=>v+x*[.2126,.7152,.0722][i],0);
 const a=lum(fg),b=lum(bg);return {color:s.color,background:bg,ratio:(Math.max(a,b)+.05)/(Math.min(a,b)+.05)};
}"""


def cabinet_view(page):
    """Use the real navigation, including its mobile modal, to open My Cabinet."""
    if not page.locator('#eis-shell-overview').is_visible():
        page.locator('#eis-shell-nav-toggle').click()
    page.locator('#eis-shell-overview').click()
    page.wait_for_function("document.body.dataset.eisView==='cabinet'")
    page.wait_for_function("document.getElementById('eis223-my-procurements').querySelector('a')!==null")


def registry_view(page):
    if not page.locator('#eis-shell-registry').is_visible():
        page.locator('#eis-shell-nav-toggle').click()
    page.locator('#eis-shell-registry').click()
    page.wait_for_function("document.body.dataset.eisView==='registry'")


def visible_focus(page):
    return page.evaluate("""() => {
      const e=document.activeElement;if(!e||e===document.body||e.closest('[hidden],[inert]'))return false;
      const r=e.getBoundingClientRect();if(r.top<0||r.bottom>innerHeight||r.width<=0||r.height<=0)return false;
      const hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
      return hit===e||e.contains(hit);
    }""")


def clarity_checks(browser, handler, output, check, results):
    """Prove that the workspace has one clear active view and a safe next action."""
    context = browser.new_context(viewport={'width':1440,'height':700},locale='ru-RU',reduced_motion='reduce')
    context.route('**/*',handler)
    page=context.new_page()
    page.on('pageerror',lambda error:results['page_errors'].append(str(error)))
    page.goto(ORIGIN+'/')
    page.wait_for_function("document.body.dataset.eisView==='registry'")
    check('clarity-registry-only',page.locator('body>main').is_visible() and page.locator('#eis223-cabinet').is_hidden())
    check('clarity-one-active-registry-item',page.locator('#eis-shell-nav [aria-current="page"]').count()==1
          and page.locator('#eis-shell-registry').get_attribute('aria-current')=='page')
    check('clarity-no-duplicate-header-navigation',page.locator('#eis223-toggle-cabinet').is_hidden()
          and (not page.locator('.eis223-menu .eis223-manager-link').count() or page.locator('.eis223-menu .eis223-manager-link').is_hidden()))
    page.locator('main .filters input').first.fill('лабораторное оборудование')
    page.locator('#preview-status').select_option('pending')
    page.evaluate('window.scrollTo(0,Math.min(140,document.documentElement.scrollHeight-innerHeight))')
    scroll_before=page.evaluate('scrollY')
    cabinet_view(page)
    check('clarity-cabinet-only',page.locator('#eis223-cabinet').is_visible() and page.locator('body>main').is_hidden())
    check('clarity-one-active-cabinet-item',page.locator('#eis-shell-nav [aria-current="page"]').count()==1
          and page.locator('#eis-shell-overview').get_attribute('aria-current')=='page')
    check('clarity-cabinet-label-consistent',page.locator('#eis223-cabinet-title').inner_text().strip()=='Мой кабинет')
    check('clarity-cabinet-url-state',urlsplit(page.url).fragment=='cabinet')
    check('clarity-cabinet-no-viewport-overflow',page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
    # The one primary submit action validates an empty number before any request.
    methods=[]
    page.on('request',lambda request:methods.append(request.method))
    page.locator('#eis223-start-check').focus()
    page.keyboard.press('Enter')
    invalid_focus=page.locator('#eis223-add-procurement input[name="reg"]').evaluate('el=>document.activeElement===el&&!el.validity.valid')
    page.wait_for_timeout(80)
    check('clarity-empty-submit-focuses-invalid-number',invalid_focus)
    check('clarity-empty-submit-sends-no-job',not any(method not in ('GET','HEAD') for method in methods))
    page.locator('#eis223-my-procurements a[href="/check-new/'+REG+'"]').first.click()
    page.wait_for_function("document.getElementById('eis-quicklook-content').textContent.includes('Поставка')")
    check('clarity-cabinet-opens-saved-inspector',page.locator('#eis-quicklook').is_visible() and page.locator('body>main').is_hidden())
    registry_view(page)
    check('clarity-view-switch-closes-inspector',page.locator('#eis-quicklook').is_hidden())
    check('clarity-registry-keeps-filter-inputs',page.locator('main .filters input').first.input_value()=='лабораторное оборудование'
          and page.locator('#preview-status').input_value()=='pending')
    page.wait_for_timeout(60)
    check('clarity-registry-keeps-scroll',abs(page.evaluate('scrollY')-scroll_before)<3,{'before':scroll_before,'after':page.evaluate('scrollY')})
    # A fresh link to the cabinet restores the correct view without a fictitious route.
    page.goto(ORIGIN+'/#cabinet')
    page.wait_for_function("document.body.dataset.eisView==='cabinet'")
    check('clarity-cabinet-hash-entry',page.locator('body>main').is_hidden() and page.locator('#eis223-cabinet').is_visible())
    registry_view(page)
    page.locator('#eis-shell-overview').focus();page.keyboard.press('Enter')
    page.wait_for_function("document.body.dataset.eisView==='cabinet'")
    check('clarity-keyboard-view-switch',page.locator('body>main').is_hidden())
    page.go_back();page.wait_for_function("document.body.dataset.eisView==='registry'")
    check('clarity-browser-back-restores-registry',page.locator('#eis223-cabinet').is_hidden())
    page.go_forward();page.wait_for_function("document.body.dataset.eisView==='cabinet'")
    check('clarity-browser-forward-restores-cabinet',page.locator('body>main').is_hidden())
    registry_view(page)
    page.locator('body>main').evaluate("e=>e.style.minHeight='3200px'")
    page.evaluate('window.scrollTo(0,850)')
    cabinet_view(page);registry_view(page);page.wait_for_timeout(60)
    check('clarity-long-registry-restores-scroll',abs(page.evaluate('scrollY')-850)<3,{'scroll':page.evaluate('scrollY')})
    check('clarity-long-registry-restores-visible-focus',visible_focus(page))
    page.keyboard.press('Tab')
    check('clarity-long-registry-next-tab-visible',visible_focus(page))
    context.close()

    mobile=browser.new_context(viewport={'width':390,'height':600},locale='ru-RU',has_touch=True,is_mobile=True,reduced_motion='reduce')
    mobile.route('**/*',handler);page=mobile.new_page()
    page.on('pageerror',lambda error:results['page_errors'].append(str(error)))
    page.goto(ORIGIN+'/');cabinet_view(page)
    check('clarity-mobile-cabinet-only',page.locator('body>main').is_hidden() and page.locator('#eis223-cabinet').is_visible())
    check('clarity-mobile-menu-dismissed-on-selection',page.locator('#eis-shell-nav').is_hidden() and not page.locator('#eis223-cabinet').evaluate('e=>e.inert'))
    check('clarity-mobile-cabinet-no-overflow',page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
    page.locator('#eis223-start-check').click()
    control=page.locator('#eis223-add-procurement input[name="reg"]')
    check('clarity-mobile-empty-submit-focus',control.evaluate('el=>document.activeElement===el&&!el.validity.valid'))
    check('clarity-mobile-number-input-reachable',control.evaluate("el=>{const r=el.getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight&&document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)===el}"))
    registry_view(page)
    check('clarity-mobile-return-registry',page.locator('body>main').is_visible() and page.locator('#eis223-cabinet').is_hidden())
    page.locator('body>main').evaluate("e=>e.style.minHeight='3200px'")
    page.evaluate('window.scrollTo(0,850)')
    cabinet_view(page);registry_view(page);page.wait_for_timeout(60)
    check('clarity-mobile-long-registry-restores-scroll',abs(page.evaluate('scrollY')-850)<3,{'scroll':page.evaluate('scrollY')})
    check('clarity-mobile-long-registry-restores-visible-focus',visible_focus(page))
    page.keyboard.press('Tab')
    check('clarity-mobile-long-registry-next-tab-visible',visible_focus(page))
    mobile.close()


def submission_checks(browser, handler, output, check, results, control):
    """Explicit submit sends one local fixture request; opening a view sends none."""
    context=browser.new_context(viewport={'width':1440,'height':900},locale='ru-RU',reduced_motion='reduce')
    context.route('**/*',handler)
    page=context.new_page()
    page.on('pageerror',lambda error:results['page_errors'].append(str(error)))
    requests_before_view=len(control['submission_requests'])
    page.goto(ORIGIN+'/');cabinet_view(page)
    form=page.locator('#eis223-add-procurement')
    number=form.locator('input[name="reg"]')
    submit=page.locator('#eis223-start-check')
    check('submit-one-form-and-action',form.count()==1 and submit.count()==1
          and submit.get_attribute('type')=='submit' and form.locator('#eis223-start-check').count()==1)
    check('submit-number-and-action-together',form.evaluate("e=>!!e.closest('.eis223-cabinet-heading')"))
    check('submit-opening-cabinet-creates-no-job',len(control['submission_requests'])==requests_before_view)
    descriptions=(number.get_attribute('aria-describedby') or '').split()
    check('submit-number-has-visible-guidance',set(['eis223-start-help','eis223-add-status']).issubset(descriptions)
          and page.locator('#eis223-start-help').is_visible())
    before=len(control['submission_requests'])
    submit.click();page.wait_for_timeout(60)
    check('submit-empty-rejected-before-request',len(control['submission_requests'])==before
          and number.evaluate('e=>!e.validity.valid&&document.activeElement===e')
          and number.get_attribute('aria-invalid')=='true')
    number.fill('123');number.press('Enter');page.wait_for_timeout(60)
    check('submit-invalid-number-rejected',len(control['submission_requests'])==before
          and number.evaluate('e=>!e.validity.valid') and bool(page.locator('#eis223-add-status').inner_text().strip()))
    # Hold the genuine fetch call before the local route, so pending UI can be
    # inspected without delaying or contacting any real endpoint.
    page.evaluate("""() => {
      const real=window.fetch.bind(window);window.__manualFetchCalls=0;window.__holdManual=true;
      window.fetch=(input,options={})=>{
        const url=new URL(typeof input==='string'?input:input.url,document.baseURI);
        if(url.pathname.startsWith('/me/manual-checks/')&&String(options.method||'GET').toUpperCase()==='POST'){
          window.__manualFetchCalls++;
          if(window.__holdManual){window.__holdManual=false;return new Promise((resolve,reject)=>{
            window.__releaseManual=()=>real(input,options).then(resolve,reject);
          })}
        }
        return real(input,options);
      };
    }""")
    number.fill(REG)
    reads_before=control['manual_reads']
    submit.click()
    page.wait_for_function("document.getElementById('eis223-start-check').disabled")
    check('submit-pending-disables-action',submit.is_disabled())
    check('submit-pending-announces-busy',form.get_attribute('aria-busy')=='true')
    check('submit-pending-keeps-number-stable',number.evaluate('e=>e.readOnly') and number.input_value()==REG)
    pending_shot=output/'cabinet-submit-pending-1440.png'
    page.screenshot(path=str(pending_shot));results['screenshots'].append(str(pending_shot))
    submit.evaluate('e=>e.click()');page.keyboard.press('Enter')
    form.evaluate("e=>e.requestSubmit(document.getElementById('eis223-start-check'))")
    page.wait_for_timeout(50)
    check('submit-pending-prevents-duplicate',page.evaluate('window.__manualFetchCalls')==1)
    page.evaluate('window.__releaseManual()')
    page.wait_for_function("!document.getElementById('eis223-start-check').disabled")
    check('submit-valid-explicit-request-once',len(control['submission_requests'])==before+1
          and control['submission_requests'][-1]=='/me/manual-checks/'+REG)
    check('submit-success-clears-number-and-refreshes',number.input_value()=='' and control['manual_reads']>reads_before)
    check('submit-success-status-visible',bool(page.locator('#eis223-add-status').inner_text().strip())
          and form.get_attribute('aria-busy')!='true')
    check('submit-success-restores-editing',not number.evaluate('e=>e.readOnly'))
    success_shot=output/'cabinet-submit-success-1440.png'
    page.screenshot(path=str(success_shot));results['screenshots'].append(str(success_shot))
    control['submission_status']=503
    number.fill(REG2);submit.click()
    page.wait_for_function("!document.getElementById('eis223-start-check').disabled")
    status=page.locator('#eis223-add-status').inner_text().strip()
    check('submit-error-retains-number',number.input_value()==REG2)
    check('submit-error-is-actionable',bool(status) and any(text in status.lower() for text in ['не удалось','ошибка','попробуйте','позже']))
    check('submit-error-restores-action',submit.is_enabled() and form.get_attribute('aria-busy')!='true')
    error_shot=output/'cabinet-submit-error-1440.png'
    page.screenshot(path=str(error_shot));results['screenshots'].append(str(error_shot))
    control['submission_status']=202
    context.close()

    mobile=browser.new_context(viewport={'width':390,'height':600},locale='ru-RU',has_touch=True,is_mobile=True,reduced_motion='reduce')
    mobile.route('**/*',handler);page=mobile.new_page()
    page.on('pageerror',lambda error:results['page_errors'].append(str(error)))
    page.goto(ORIGIN+'/');cabinet_view(page)
    form=page.locator('#eis223-add-procurement');number=form.locator('input[name="reg"]');submit=page.locator('#eis223-start-check')
    check('submit-mobile-field-and-action-fit',page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
          and form.bounding_box()['width']<=390)
    check('submit-mobile-touch-targets',number.bounding_box()['height']>=44 and submit.bounding_box()['height']>=44)
    before=len(control['submission_requests'])
    number.fill(REG3);number.press('Enter')
    page.wait_for_function("!document.getElementById('eis223-start-check').disabled&&document.querySelector('#eis223-add-procurement input[name=reg]').value===''")
    check('submit-mobile-enter-explicitly-starts-once',len(control['submission_requests'])==before+1)
    mobile.close()


def account_archive_checks(browser, handler, check, results):
    """The reset offered in an empty archive remains usable after input blur."""
    for width in (1440,390):
        context=browser.new_context(viewport={'width':width,'height':844},locale='ru-RU',
            has_touch=width==390,is_mobile=width==390,reduced_motion='reduce')
        context.route('**/*',handler);page=context.new_page()
        page.on('pageerror',lambda error:results['page_errors'].append(str(error)))
        methods=[]
        page.on('request',lambda request:methods.append(request.method))
        page.goto(ORIGIN+'/account/memos')
        downloads=page.locator('#memo-rows a[download]')
        page.wait_for_function("document.querySelectorAll('#memo-rows a[download]').length===2")
        check(f'memo-{width}-create-form-collapsed',page.locator('#memo-create-panel').get_attribute('open') is None)
        search=page.locator('#memo-search');search.fill('99999999999')
        reset=page.locator('#memo-rows').get_by_role('button',name='Сбросить фильтры',exact=True)
        reset.wait_for(state='visible')
        check(f'memo-{width}-filtered-empty-offers-reset',downloads.count()==0 and reset.count()==1)
        # A real click blurs the search input first. The product must retain this
        # very button through that change event so its click handler still fires.
        reset.click()
        page.wait_for_function("document.getElementById('memo-search').value===''&&document.querySelectorAll('#memo-rows a[download]').length===2")
        check(f'memo-{width}-empty-row-reset-restores-downloads',search.input_value()=='' and downloads.count()==2)
        check(f'memo-{width}-reset-does-not-create-document',all(method in ('GET','HEAD') for method in methods))
        check(f'memo-{width}-reset-keeps-viewport',page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
        context.close()


def panel_checks(browser, handler, output, check, results, control):
    """Exercise the real new sidebar, inspector and manager drill-down behavior."""
    context = browser.new_context(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce')
    context.route('**/*', handler)
    page = context.new_page()
    page.on('pageerror', lambda error: results['page_errors'].append(str(error)))
    page.goto(ORIGIN + '/')
    if not page.locator('#eis-shell-nav').count():
        check('panels-installed', False, 'Workspace panel builder has not yet been integrated')
        context.close()
        return
    page.locator('#eis-shell-nav').wait_for(state='visible')
    nav = page.locator('#eis-shell-nav')
    check('sidebar-expanded-width', nav.bounding_box()['width'] >= 180, nav.bounding_box())
    page.evaluate("window.__previewFilterApply=0;document.getElementById('preview-apply').addEventListener('click',()=>window.__previewFilterApply++)")
    page.locator('[data-eis-filter-value=\"pending\"]').click()
    check('registry-shortcuts-use-existing-filter', page.locator('#preview-status').input_value() == 'pending' and page.evaluate('window.__previewFilterApply') == 1)
    page.locator('#preview-status').select_option('')
    page.locator('#eis-shell-nav-toggle').click()
    check('sidebar-collapses', nav.bounding_box()['width'] <= 84, nav.bounding_box())
    page.reload()
    page.locator('#eis-shell-nav').wait_for(state='visible')
    check('sidebar-preference-persists', nav.bounding_box()['width'] <= 84, nav.bounding_box())
    page.locator('#eis-shell-nav-toggle').click()
    first = page.locator('main table a[href="/check-new/' + REG + '"]').first
    second = page.locator('main table a[href="/check-new/' + REG2 + '"]').first
    first.scroll_into_view_if_needed()
    before_scroll = page.evaluate('scrollY')
    first.click()
    page.locator('#eis-quicklook').wait_for(state='visible')
    page.wait_for_function("document.getElementById('eis-quicklook-content').textContent.includes('Поставка')")
    check('quicklook-opens-selected-card', REG in page.locator('#eis-quicklook-title').inner_text())
    check('quicklook-retains-scroll', abs(page.evaluate('scrollY') - before_scroll) < 3)
    for tab in ['findings', 'documents', 'summary']:
        page.locator('[data-quicklook-tab="' + tab + '"]').click()
        check('quicklook-tab-' + tab, page.locator('[data-quicklook-tab="' + tab + '"]').get_attribute('aria-selected') == 'true')
        if tab == 'findings':
            for disclosure in page.locator('#eis-quicklook-content details').all():
                if disclosure.get_attribute('open') is None:
                    disclosure.locator('summary').click()
            check('quicklook-evidence-visible', 'Противоречие' in page.locator('#eis-quicklook-content').inner_text())
            page.screenshot(path=str(output / 'workspace-desktop-evidence.png'), full_page=False)
            results['screenshots'].append(str(output / 'workspace-desktop-evidence.png'))
        if tab == 'documents':
            check('quicklook-documents-visible', 'Проект договора' in page.locator('#eis-quicklook-content').inner_text())
    # Capture the real split layout, without leaving a keyboard-focus outline on a random link.
    page.locator('#eis-quicklook-title').click()
    page.screenshot(path=str(output / 'workspace-desktop-panels.png'), full_page=False)
    results['screenshots'].append(str(output / 'workspace-desktop-panels.png'))
    page.keyboard.press('Escape')
    check('quicklook-escape-closes', page.locator('#eis-quicklook').is_hidden())
    check('quicklook-returns-focus', first.evaluate('el=>document.activeElement===el'))
    # Modified clicks retain normal browser link behavior rather than opening the inspector.
    prevented = first.evaluate("el=>!el.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,ctrlKey:true,button:0}))")
    check('quicklook-preserves-modified-click', not prevented and page.locator('#eis-quicklook').is_hidden())
    # A resolved but delayed first response must never replace the newly selected row.
    page.evaluate("""() => {
      const original=window.fetch;window.__previewDelay=true;
      window.fetch=async (...args)=>{const response=await original(...args);
        if(window.__previewDelay&&String(args[0]).includes('/check-new/32612345678')){
          const body=await response.text();await new Promise(r=>setTimeout(r,350));
          return new Response(body,{status:response.status,headers:response.headers});
        }return response;};
    }""")
    first.click()
    page.wait_for_timeout(80)
    second.click()
    page.wait_for_function("document.getElementById('eis-quicklook-content').textContent.includes('Обслуживание')")
    page.wait_for_timeout(400)
    check('quicklook-stale-response-discarded', REG2 in page.locator('#eis-quicklook-title').inner_text()
          and 'Обслуживание' in page.locator('#eis-quicklook-content').inner_text())
    page.evaluate('window.__previewDelay=false')
    page.locator('#eis-quicklook-close').click()
    # Failed requests clear stale evidence and offer an explicit recoverable error.
    for code in [503, 401]:
        control['failure'] = code
        first.click()
        page.wait_for_timeout(150)
        content = page.locator('#eis-quicklook-content').inner_text()
        check('quicklook-http-' + str(code) + '-no-stale-evidence', 'В пункте 4.2' not in content and 'Обслуживание инженерных' not in content)
        check('quicklook-http-' + str(code) + '-explicit-state', bool(content.strip()) and any(word in content.casefold() for word in ['не удалось','недоступ','ошибк','войти','вход','сесси','повтор']))
        page.locator('#eis-quicklook-close').click()
        control['failure'] = None
    control['html'] = ('<section data-eis-section="summary" data-eis-reg="' + REG + '"><h2>Проверка безопасного содержимого</h2>'
        '<script>window.__eisInjected=true</script><img src="https://outside.invalid/image" onerror="window.__eisInjected=true">'
        '<p id="foreign-id" onclick="window.__eisInjected=true">Обычный текст</p><form action="/auth/logout"><button>Отправить</button></form>'
        '<a href="javascript:window.__eisInjected=true">Небезопасная ссылка</a><a href="https://name:password@example.test/">Ссылка с реквизитами</a></section>')
    first.click()
    page.wait_for_function("document.getElementById('eis-quicklook-content').textContent.includes('Проверка безопасного')")
    safe = page.locator('#eis-quicklook-content')
    check('quicklook-sanitizes-active-content', safe.locator('script,img,form,button,[onclick],[onerror],[id]').count() == 0
          and page.evaluate('window.__eisInjected===undefined'))
    check('quicklook-drops-unsafe-hrefs', safe.locator('a[href]').count() == 0)
    page.locator('#eis-quicklook-close').click()
    control['html'] = None
    page.locator('main table a[href="/check-new/' + REG3 + '"]').first.click()
    page.wait_for_function("document.getElementById('eis-quicklook-content').textContent.includes('неполными доказательствами')")
    check('quicklook-summary-keeps-evidence-warning', 'ODT' in page.locator('#eis-quicklook-content').inner_text())
    page.locator('[data-quicklook-tab="findings"]').click()
    warning = page.locator('#eis-quicklook-content').inner_text()
    check('quicklook-findings-keeps-evidence-warning', 'неполными доказательствами' in warning and 'ODT' in warning)
    page.locator('#eis-quicklook-close').click()
    first.click()
    page.wait_for_function("document.getElementById('eis-quicklook-content').textContent.includes('Поставка')")
    page.locator('#eis-shell-nav-toggle').click()
    page.screenshot(path=str(output / 'workspace-desktop-collapsed.png'), full_page=False)
    results['screenshots'].append(str(output / 'workspace-desktop-collapsed.png'))
    page.locator('#eis-quicklook-close').click()
    # The manager metrics operate on the authoritative fields in the local API fixtures.
    page.goto(ORIGIN + '/account/manager')
    page.locator('[data-mgr-shortcut="pending"]').click()
    check('manager-pending-shortcut', page.locator('#mgr-task-status').input_value() == 'submitted'
          and page.locator('#mgr-task-rows tr').count() == 1
          and 'Рассмотреть результаты' in page.locator('#mgr-task-rows').inner_text())
    page.locator('[data-tab="overview"]').click()
    page.locator('[data-mgr-shortcut="overdue"]').click()
    check('manager-overdue-shortcut', page.locator('#mgr-task-status').input_value() == 'overdue'
          and page.locator('#mgr-task-rows tr').count() == 1
          and 'Проверить условия договора' in page.locator('#mgr-task-rows').inner_text())
    page.locator('#mgr-task-search').fill('нет-совпадений')
    check('manager-filter-empty-state', page.locator('#mgr-task-rows tr').count() == 0
          and page.locator('#mgr-task-empty-reset').is_visible())
    page.locator('#mgr-task-empty-reset').click()
    check('manager-clear-task-filters', page.locator('#mgr-task-rows tr').count() == 4)
    page.locator('[data-tab="overview"]').click()
    page.locator('[data-mgr-shortcut="memos"]').click()
    check('manager-memos-shortcut', page.locator('#mgr-memo-status').input_value() == 'submitted'
          and page.locator('#mgr-memo-rows tr').count() == 1)
    page.locator('#mgr-memo-status').select_option('rejected')
    check('manager-memo-empty-state', page.locator('#mgr-memo-rows tr').count() == 0
          and page.locator('#mgr-memo-empty-reset').is_visible())
    page.locator('#mgr-memo-empty-reset').click()
    check('manager-clear-memo-filter', page.locator('#mgr-memo-rows tr').count() == 2)
    page.locator('[data-tab="overview"]').click()
    page.locator('[data-mgr-shortcut="team"]').click()
    check('manager-team-shortcut', page.locator('[data-section="team"]').is_visible())
    context.close()

    mobile = browser.new_context(viewport={'width': 390, 'height': 844}, has_touch=True, is_mobile=True, reduced_motion='reduce')
    mobile.route('**/*', handler)
    page = mobile.new_page()
    page.on('pageerror', lambda error: results['page_errors'].append(str(error)))
    page.goto(ORIGIN + '/')
    page.locator('#eis-shell-nav-toggle').click()
    page.locator('#eis-shell-nav').wait_for(state='visible')
    close_box=page.locator('#eis-shell-nav-close').bounding_box()
    check('mobile-menu-close-touch-target', close_box['width']>=44 and close_box['height']>=44, close_box)
    page.screenshot(path=str(output / 'workspace-mobile-menu.png'), full_page=False)
    results['screenshots'].append(str(output / 'workspace-mobile-menu.png'))
    page.keyboard.press('Escape')
    check('mobile-menu-escape', page.locator('#eis-shell-nav').is_hidden())
    page.locator('main table a[href="/check-new/' + REG + '"]').first.click()
    page.locator('#eis-quicklook').wait_for(state='visible')
    page.wait_for_function("document.getElementById('eis-quicklook-content').textContent.includes('Поставка')")
    box = page.locator('#eis-quicklook').bounding_box()
    check('mobile-quicklook-fullscreen', box['width'] >= 388 and box['height'] >= 840, box)
    check('mobile-quicklook-is-modal', page.locator('#eis-quicklook').get_attribute('aria-modal') == 'true')
    # Cycle beyond every control and prove keyboard focus cannot escape the modal.
    for _ in range(18):
        page.keyboard.press('Tab')
    check('mobile-quicklook-traps-focus', page.locator('#eis-quicklook').evaluate('el=>el.contains(document.activeElement)'))
    page.locator('[data-quicklook-tab="findings"]').click()
    page.screenshot(path=str(output / 'workspace-mobile-inspector.png'), full_page=False)
    results['screenshots'].append(str(output / 'workspace-mobile-inspector.png'))
    page.keyboard.press('Escape')
    check('mobile-quicklook-escape', page.locator('#eis-quicklook').is_hidden())
    check('mobile-no-viewport-overflow', page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
    page.set_viewport_size({'width':390,'height':600})
    page.goto(ORIGIN+'/')
    page.locator('main table a[href="/check-new/'+REG+'"]').first.click()
    page.locator('#eis-quicklook').wait_for(state='visible')
    check('mobile-short-viewport-card-reachable', page.locator('#eis-quicklook').is_visible())
    mobile.close()



def capture_showcase(output: Path):
    """Capture fresh final-source evidence without repeating the behavior suite."""
    from playwright.sync_api import sync_playwright
    output.mkdir(parents=True, exist_ok=True)
    documents, api = pages(), fixtures()
    for _, (name, markup) in documents.items():
        (output / (name + '.html')).write_text(markup)
    unknown=[]
    def handler(route):
        parsed=urlsplit(route.request.url)
        if parsed.netloc!='preview.eis.test':route.abort();return
        if parsed.path in documents:route.fulfill(status=200,content_type='text/html',body=documents[parsed.path][1]);return
        if route.request.method not in ('GET','HEAD') or parsed.path not in api:
            unknown.append(route.request.method+' '+parsed.path)
            route.fulfill(status=404,content_type='application/json',body=json.dumps({'detail':'Missing explicit local preview fixture'}));return
        route.fulfill(status=200,content_type='application/json',body=json.dumps(api[parsed.path]))
    saved=[]
    def secondary_surfaces(page,width):
        # These are the same real builders as the primary screens. Wait for the
        # explicit fixture content so no screenshot records a transient spinner.
        for path,name,ready in (
            ('/account/home','manual',"document.querySelectorAll('#manual-rows tr').length===3"),
            ('/account/thematic','thematic',"document.getElementById('real-thematic-list').children.length>0"),
            ('/account/memos','memos',"document.getElementById('memo-rows').children.length>0"),
            ('/account/workbench','staff',"document.getElementById('staff-active-tasks').textContent==='3'"),
            ('/account/manager','manager',"document.getElementById('mgr-kpi-staff').textContent==='1'"),
            ('/account/profile','profile',"document.getElementById('profile-email').textContent==='preview@example.test'"),
            ('/account/login','login','document.readyState===\"complete\"'),
        ):
            page.goto(ORIGIN+path);page.wait_for_function(ready)
            filename=name+'-'+str(width)+'.png'
            page.screenshot(path=str(output/filename),full_page=True)
            if filename not in saved:saved.append(filename)
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True)
        desktop=browser.new_context(viewport={'width':1440,'height':1000},locale='ru-RU',reduced_motion='reduce')
        desktop.route('**/*',handler);page=desktop.new_page();page.goto(ORIGIN+'/')
        page.locator('#eis-shell-nav').wait_for(state='visible')
        page.screenshot(path=str(output/'workspace-desktop.png'));saved.append('workspace-desktop.png')
        cabinet_view(page)
        page.wait_for_function("document.getElementById('eis223-my-memos').querySelector('a')!==null")
        page.locator('#eis223-cabinet-title').click();page.evaluate('window.scrollTo(0,0)')
        page.screenshot(path=str(output/'cabinet-1440.png'),full_page=True);saved.append('cabinet-1440.png')
        page.locator('#eis223-start-check').click()
        page.screenshot(path=str(output/'cabinet-validation-1440.png'));saved.append('cabinet-validation-1440.png')
        registry_view(page)
        page.locator('main table a[href="/check-new/'+REG+'"]').first.click()
        page.wait_for_function("document.getElementById('eis-quicklook-content').textContent.includes('Поставка')")
        page.locator('#eis-quicklook-title').click()
        page.screenshot(path=str(output/'workspace-desktop-panels.png'));saved.append('workspace-desktop-panels.png')
        page.locator('[data-quicklook-tab="findings"]').click()
        for disclosure in page.locator('#eis-quicklook-content details').all():
            if disclosure.get_attribute('open') is None:disclosure.locator('summary').click()
        page.locator('#eis-quicklook-title').click()
        page.screenshot(path=str(output/'workspace-desktop-evidence.png'));saved.append('workspace-desktop-evidence.png')
        page.locator('[data-quicklook-tab="summary"]').click();page.locator('#eis-shell-nav-toggle').click()
        page.locator('#eis-quicklook-title').click()
        page.screenshot(path=str(output/'workspace-desktop-collapsed.png'));saved.append('workspace-desktop-collapsed.png')
        page.goto(ORIGIN+'/account/manager');page.wait_for_function("document.getElementById('mgr-kpi-staff').textContent==='1'")
        page.screenshot(path=str(output/'manager-1440.png'));saved.append('manager-1440.png')
        page.goto(ORIGIN+'/check-new/'+REG);page.screenshot(path=str(output/'card-1440.png'),full_page=True);saved.append('card-1440.png')
        secondary_surfaces(page,1440)
        desktop.close()
        mobile=browser.new_context(viewport={'width':390,'height':844},locale='ru-RU',has_touch=True,is_mobile=True,reduced_motion='reduce')
        mobile.route('**/*',handler);page=mobile.new_page();page.goto(ORIGIN+'/')
        page.locator('#eis-shell-nav-toggle').click();page.screenshot(path=str(output/'workspace-mobile-menu.png'));saved.append('workspace-mobile-menu.png')
        page.locator('#eis-shell-nav-close').click()
        cabinet_view(page)
        page.wait_for_function("document.getElementById('eis223-my-memos').querySelector('a')!==null")
        page.locator('#eis223-cabinet-title').click();page.evaluate('window.scrollTo(0,0)')
        page.screenshot(path=str(output/'cabinet-390.png'),full_page=True);saved.append('cabinet-390.png')
        page.screenshot(path=str(output/'cabinet-mobile.png'));saved.append('cabinet-mobile.png')
        registry_view(page)
        page.locator('main table a[href="/check-new/'+REG+'"]').first.click()
        page.wait_for_function("document.getElementById('eis-quicklook-content').textContent.includes('Поставка')")
        page.screenshot(path=str(output/'workspace-mobile-summary.png'));saved.append('workspace-mobile-summary.png')
        page.locator('[data-quicklook-tab="findings"]').click()
        for disclosure in page.locator('#eis-quicklook-content details').all():
            if disclosure.get_attribute('open') is None:disclosure.locator('summary').click()
        page.locator('#eis-quicklook-title').click()
        page.screenshot(path=str(output/'workspace-mobile-inspector.png'));saved.append('workspace-mobile-inspector.png')
        page.goto(ORIGIN+'/check-new/'+REG);page.screenshot(path=str(output/'card-390.png'),full_page=True);saved.append('card-390.png')
        secondary_surfaces(page,390)
        mobile.close();browser.close()
    (output/'showcase.json').write_text(json.dumps({'fixture_only':True,'source':'fresh local builders','screenshots':saved,'unknown_api_paths':unknown},ensure_ascii=False,indent=2))
    print('Captured',len(saved),'fresh showcase screenshots in',output)
    if unknown:print('Missing explicit showcase fixtures:',unknown)
    return 1 if unknown else 0



def run(output: Path):
    from playwright.sync_api import sync_playwright
    output.mkdir(parents=True, exist_ok=True)
    documents = pages()
    api = fixtures()
    for _, (name, markup) in documents.items():
        (output / (name + '.html')).write_text(markup)
    results = {'fixture_only': True, 'registry_is_structural_fixture': True,
               'checks': [], 'failures': [], 'screenshots': [], 'page_errors': [], 'unknown_api_paths': []}
    control = {'failure': None, 'html': None, 'card_requests': [], 'submission_status': 202, 'submission_requests': [], 'manual_reads': 0}
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
        if parsed.path == '/me/manual-checks' and request.method == 'GET':
            control['manual_reads'] += 1
        if parsed.path.startswith('/me/manual-checks/') and request.method == 'POST':
            control['submission_requests'].append(parsed.path)
            status=control['submission_status']
            route.fulfill(status=status,content_type='application/json',body=json.dumps(
                {'ok': True, 'demo': True, 'reg_number': parsed.path.rsplit('/',1)[-1]}
                if 200<=status<300 else {'detail':'Локальная демонстрационная ошибка'}))
            return
        if parsed.path.startswith('/check-new/'):
            control['card_requests'].append(parsed.path)
            if control.get('html') is not None:
                route.fulfill(status=200, content_type='text/html', body=control['html'])
                return
            if control['failure']:
                route.fulfill(status=control['failure'], content_type='application/json', body='{}')
                return
        if parsed.path in documents:
            route.fulfill(status=200, content_type='text/html', body=documents[parsed.path][1])
            return
        payload = api.get(parsed.path)
        if payload is None or request.method not in ('GET','HEAD'):
            missing=parsed.path if request.method in ('GET','HEAD') else request.method+' '+parsed.path
            if missing not in results['unknown_api_paths']:
                results['unknown_api_paths'].append(missing)
            route.fulfill(status=404,content_type='application/json',body=json.dumps({'detail':'Missing explicit local preview fixture'}))
            return
        # Reads and the explicit submit fixture above are fulfilled locally.
        route.fulfill(status=200, content_type='application/json', body=json.dumps(payload))
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path='/usr/bin/chromium', headless=True)
        for width in (1440, 768, 390):
            context = browser.new_context(viewport={'width': width, 'height': 1000},
                device_scale_factor=1, locale='ru-RU', reduced_motion='reduce')
            context.route('**/*', handler)
            page = context.new_page()
            page.on('pageerror', lambda error: results['page_errors'].append(str(error)))
            for path, (name, _) in documents.items():
                page.goto(ORIGIN + path)
                page.wait_for_timeout(150)
                if name == 'registry':
                    cabinet_view(page)
                    check(f'{name}-{width}-cabinet-toggle', page.locator('#eis223-cabinet').is_visible())
                    registry_view(page)
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
        clarity_checks(browser, handler, output, check, results)
        submission_checks(browser, handler, output, check, results, control)
        account_archive_checks(browser, handler, check, results)
        panel_checks(browser, handler, output, check, results, control)
        touch = browser.new_context(viewport={'width': 390, 'height': 844}, has_touch=True,
                                    is_mobile=True, reduced_motion='reduce')
        touch.route('**/*', handler)
        page = touch.new_page()
        touch_targets = {'/': ['#eis-shell-nav-toggle', '#eis223-logout'],
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
    parser.add_argument('--showcase-only', action='store_true', help='Refresh final-source screenshots only')
    args = parser.parse_args()
    raise SystemExit(capture_showcase(args.output) if args.showcase_only else run(args.output))
