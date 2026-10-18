"""Optional Chromium UI regression. Local synthetic accounts; no network or live writes.
Run with repo on PYTHONPATH and playwright available.
"""
import json
import os
from playwright.sync_api import sync_playwright
from scripts.account_pages_v1 import ADMIN
from scripts.account_site_ui_v1 import site_header
from scripts.v368_manager_ui import render_manager

employee={'id':'e','email':'employee@example.test','full_name':'Тестовый Сотрудник','role':'user','status':'approved','manager_id':'m1'}
leaders=[{'id':f'm{i}','email':f'manager{i}@example.test','full_name':f'Руководитель {i}','role':'manager','status':'approved'} for i in (1,2)]
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,executable_path=os.environ.get('CHROMIUM','/usr/bin/chromium'),args=['--no-sandbox'],env={'HOME':'/tmp/eis-team-ui','PATH':os.environ['PATH']})
    page=browser.new_page(viewport={'width':1280,'height':900})
    state={'calls':[],'attached':False,'fail':False}
    def route(r):
        path=r.request.url.split('fixture.test')[-1]
        if path=='/':return r.fulfill(content_type='text/html',body=state['html'])
        if r.request.method in ('PUT','POST'):
            state['calls'].append((path,r.request.post_data))
            if state['fail']:return r.fulfill(status=409,json={'detail':'Назначение изменилось. Обновите список.'})
            if path.endswith('/attach'):state['attached']=True;return r.fulfill(json={'manager':'m2','employee':'e'})
            data=r.request.post_data_json;employee['manager_id']=data['manager_id'];return r.fulfill(json={'manager':data['manager_id'],'employee':'e'})
        if path=='/admin/roles/users':return r.fulfill(json=[employee,*leaders])
        if path=='/manager/team/available':return r.fulfill(json=[] if state['attached'] else [employee])
        if path=='/manager/team':return r.fulfill(json=[{**employee,'manager_id':'m2','manager_email':leaders[1]['email']}] if state['attached'] else [])
        if path.endswith(('/dashboard','/quality','/options','/profile','/summary')):return r.fulfill(json={})
        return r.fulfill(json=[])
    page.route('**/*',route)
    for name,html in [('admin',ADMIN),('cabinet',site_header('admin'))]:
        employee['manager_id']='m1';state.update(html=html,calls=[],fail=False)
        page.goto('https://fixture.test/')
        if name=='cabinet':
            page.click('#eis223-toggle-cabinet');page.click('#eis223-admin-open')
        select=page.get_by_label('Руководитель сотрудника employee@example.test',exact=True)
        assert select.input_value()=='m1'
        button=page.get_by_role('button',name='Сохранить руководителя',exact=True)
        assert button.is_disabled()
        select.select_option('m2');assert not button.is_disabled()
        page.once('dialog',lambda d:d.dismiss());button.click();assert state['calls']==[]
        page.once('dialog',lambda d:d.accept());button.click()
        page.wait_for_function("document.querySelector('select[aria-label=\"Руководитель сотрудника employee@example.test\"]').parentElement.parentElement.textContent.includes('Сейчас: Руководитель 2')")
        assert len(state['calls'])==1 and button.is_disabled()
        select.select_option('');state['fail']=True
        page.once('dialog',lambda d:d.accept());button.click()
        page.get_by_text('Не удалось сохранить руководителя:',exact=False).wait_for()
        assert not button.is_disabled()
        state['fail']=False;page.once('dialog',lambda d:d.accept());button.click()
        page.wait_for_function("document.querySelector('select[aria-label=\"Руководитель сотрудника employee@example.test\"]').parentElement.parentElement.textContent.includes('Сейчас: Без руководителя')")
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),name
        page.screenshot(path='/tmp/team-'+name+'-mobile.png',full_page=True)
        page.set_viewport_size({'width':1280,'height':900})
        print('PASS',name,'current assignment, cancel, save, failed save/retry, detach, mobile width')
    state.update(html=render_manager('manager'),calls=[],fail=False)
    page.goto('https://fixture.test/');page.click('[data-tab="team"]')
    select=page.locator('#mgr-available-employee');select.select_option('e')
    page.once('dialog',lambda d:d.dismiss());page.click('#mgr-attach-self');assert state['calls']==[]
    page.once('dialog',lambda d:d.accept());page.click('#mgr-attach-self')
    page.get_by_text('Сотрудник прикреплён к вам',exact=True).wait_for()
    assert len(state['calls'])==1 and state['calls'][0][0]=='/manager/team/e/attach'
    assert page.locator('#mgr-attach-self').is_disabled()
    assert page.locator('#mgr-team-rows').inner_text().find('Тестовый Сотрудник')>=0
    assert select.locator('option').count()==1
    page.screenshot(path='/tmp/team-manager-desktop.png',full_page=True)
    print('PASS manager self-attach, cancel, refreshed team and eligible list')
    browser.close()
