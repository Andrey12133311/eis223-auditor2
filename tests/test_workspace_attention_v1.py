"""Attention uses only loaded owner data and never infers audit completion."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest

from lxml import html
from scripts.workspace_attention_v1 import workspace_attention


NODE = r'''
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {JSDOM}=require('jsdom');
const markup=fs.readFileSync(0,'utf8');
const REG='32612345678',REG2='32612345679';
function start(){
 const dom=new JSDOM(markup,{url:'https://attention.example.test/',runScripts:'outside-only'});
 const w=dom.window,document=w.document,requests=[];
 const BaseDate=w.Date;
 w.Date=class extends BaseDate{constructor(...args){super(...(args.length?args:['2026-11-11T20:00:00Z']))}};
 w.fetch=(...args)=>{requests.push(args);throw Error('Attention must not fetch')};
 for(const script of document.querySelectorAll('script'))w.eval(script.textContent);
 const emit=(path,data)=>document.dispatchEvent(new w.CustomEvent('eis:workspace-data',{detail:{path,data}}));
 const fail=path=>document.dispatchEvent(new w.CustomEvent('eis:workspace-error',{detail:{path}}));
 const get=id=>document.getElementById('eis-attention'+(id?'-'+id:''));
 const ready=(manual=[],tasks=[])=>{emit('/me/manual-checks',manual);emit('/me/assignments',tasks)};
 return {dom,w,document,requests,emit,fail,get,ready,close:()=>dom.window.close()};
}
const complete={reg_number:REG,job_status:'completed',legal_review_state:'completed',legal_review_documents:3,card_available:true};
function sourceCompleteness(){
 const ui=start();
 assert.equal(ui.get('count').textContent,'—');
 assert.equal(ui.get().getAttribute('aria-busy'),'true');
 ui.emit('/me/manual-checks',[]);
 assert.match(ui.get('status').textContent,/Загружаем/);
 assert.equal(ui.get('count').textContent,'—');
 ui.fail('/me/assignments');
 assert.match(ui.get('status').textContent,/Часть данных не обновлена/);
 assert.equal(ui.get('count').textContent,'—');
 assert.equal(ui.get('retry').hidden,false);
 let retries=0;ui.document.addEventListener('eis:workspace-refresh',()=>retries++);
 ui.get('retry').click();assert.equal(retries,1);assert.equal(ui.get().getAttribute('aria-busy'),'true');
 ui.emit('/me/assignments',[]);
 assert.equal(ui.get('count').textContent,'0');
 assert.match(ui.get('status').textContent,/В загруженных/);
 assert.equal(ui.get('retry').hidden,true);assert.deepEqual(ui.requests,[]);ui.close();
}
function completionIsStrict(){
 const ui=start();ui.ready([complete]);assert.equal(ui.get('count').textContent,'0');
 for(const value of [0,'0',null,undefined,false,-1,'not a count']){
  ui.ready([{...complete,legal_review_documents:value}]);
  assert.equal(ui.get('count').textContent,'1');
  assert.match(ui.get('list').textContent,/Юридическая проверка не завершена/);
 }
 ui.ready([{...complete,legal_review_documents:'3'}]);assert.equal(ui.get('count').textContent,'0');
 ui.ready([{...complete,legal_review_state:'running'}]);assert.match(ui.get('list').textContent,/Идёт юридическая проверка/);
 ui.ready([{...complete,legal_review_state:'new_state'}]);assert.match(ui.get('list').textContent,/уточняется/);
 assert.doesNotMatch(ui.get('list').textContent,/new_state|Нарушение подтверждено/);ui.close();
}
function mergeAndSafeLinks(){
 const ui=start();
 const title='<img src=x onerror=alert(1)> Поручение';
 ui.ready([{...complete,job_status:'failed',legal_review_state:'pending'}],[
  {reg_number:REG,status:'returned',overdue:true,title},
  {reg_number:REG2,status:'in_progress',overdue:false,due_date:'2026-11-12',title:'На завтра'},
 ]);
 assert.equal(ui.get('count').textContent,'2');assert.equal(ui.get('list').children.length,2);
 const first=ui.get('list').firstElementChild;
 assert.equal(first.dataset.reg,REG);
 assert.match(first.textContent,/Ошибка получения документов/);
 assert.match(first.textContent,/Срок поручения истёк/);
 assert.match(first.textContent,/возвращено на доработку/);
 assert.match(first.textContent,/<img src=x/);
 assert.equal(first.querySelector('img'),null);
 assert.deepEqual([...first.querySelectorAll('a')].map(a=>a.getAttribute('href')),['/check-new/'+REG,'/account/workbench']);
 ui.fail('/me/manual-checks');assert.equal(ui.get('count').textContent,'2');assert.match(ui.get('status').textContent,/доступные сведения/);
 ui.ready([{...complete,job_status:'failed',card_available:false}],[]);
 assert.equal(ui.get('list').querySelector('a').getAttribute('href'),'/account/home');
 assert.deepEqual(ui.requests,[]);ui.close();
}
function deadlinesUseOnlyAuthoritativeOverdue(){
 const ui=start();
 const task={reg_number:REG,status:'assigned',title:'Поручение'};
 for(const [due,label] of [['2026-11-11','сегодня'],['2026-11-12','завтра']]){
  ui.ready([],[{...task,due_date:due,overdue:false}]);assert.match(ui.get('list').textContent,new RegExp(label));
 }
 for(const due of ['2026-11-10','2026-11-15','2026-02-30','not-a-date','2026-11-11T00:00:00Z']){
  ui.ready([],[{...task,due_date:due,overdue:false}]);assert.equal(ui.get('count').textContent,'0',due);
 }
 ui.ready([],[{...task,due_date:'2026-11-10'}]);assert.equal(ui.get('count').textContent,'0','absence of overdue is not overdue');
 ui.ready([],[{...task,due_date:null,overdue:true}]);assert.match(ui.get('list').textContent,/Срок поручения истёк/);
 for(const state of ['completed','cancelled']){
  ui.ready([],[{...task,status:state,due_date:'2026-11-11',overdue:true}]);assert.equal(ui.get('count').textContent,'0');
 }
 ui.ready([],[{...task,status:'submitted',due_date:'2026-11-11',overdue:false}]);assert.equal(ui.get('count').textContent,'0');ui.close();
}
function malformedSourcesAreNotEmpty(){
 const ui=start();ui.ready([],[{reg_number:'javascript:alert(1)',status:'returned'}]);
 assert.equal(ui.get('count').textContent,'—');assert.match(ui.get('status').textContent,/Часть состояний/);
 assert.equal(ui.get('list').children.length,0);
 ui.emit('/me/manual-checks',{rows:[]});assert.match(ui.get('status').textContent,/Часть данных/);
 assert.equal(ui.get('retry').hidden,false);
 ui.ready([],[{reg_number:REG,status:'future_state',overdue:false}]);
 assert.match(ui.get('status').textContent,/Часть состояний/);assert.equal(ui.get('count').textContent,'—');ui.close();
}
function boundedAndPrioritySorted(){
 const ui=start();
 const manual=Array.from({length:8},(_,i)=>({...complete,reg_number:String(32610000000+i),legal_review_state:'pending'}));
 manual[7].legal_review_state='failed';
 ui.ready(manual);
 assert.equal(ui.get('count').textContent,'8');assert.equal(ui.get('list').children.length,5);
 assert.equal(ui.get('list').firstElementChild.dataset.reg,manual[7].reg_number);
 assert.match(ui.get('status').textContent,/Показаны 5 из 8/);
 assert.deepEqual([...ui.get().querySelectorAll('.eis-attention-footer a')].map(a=>a.getAttribute('href')),['/account/home','/account/workbench']);ui.close();
}
function refreshPreservesKeyboardContext(){
 const ui=start(),pending={...complete,legal_review_state:'pending'};
 ui.emit('/me/manual-checks',[pending]);
 const action=ui.get('list').querySelector('a');action.focus();
 ui.emit('/me/assignments',[{reg_number:REG2,status:'returned',title:'Доработать'}]);
 const retained=ui.get('list').querySelector('[data-reg="'+REG+'"] a');
 assert.notEqual(retained,action,'the action was rebuilt by the independent response');
 assert.equal(ui.document.activeElement,retained,'focus stays on the same procurement and action');
 ui.fail('/me/manual-checks');
 assert.equal(ui.document.activeElement.getAttribute('href'),'/check-new/'+REG);
 ui.emit('/me/manual-checks',[complete]);
 assert.equal(ui.document.activeElement.getAttribute('href'),'/account/home','a disappeared check falls back to its full list');
 const task=ui.get('list').querySelector('a');task.focus();
 ui.emit('/me/assignments',[]);
 assert.equal(ui.document.activeElement.getAttribute('href'),'/account/workbench','a disappeared task falls back to its full list');
 const elsewhere=ui.document.createElement('button');elsewhere.textContent='Другое действие';ui.document.body.append(elsewhere);elsewhere.focus();
 ui.emit('/me/manual-checks',[pending]);ui.emit('/me/assignments',[]);
 assert.equal(ui.document.activeElement,elsewhere,'updates do not steal focus from another control');
 ui.get('list').querySelector('a').focus();ui.fail('/me/assignments');
 ui.emit('/me/manual-checks',[complete]);
 assert.equal(ui.document.activeElement,ui.get('retry'),'an incomplete source offers an accessible retry fallback');
 assert.deepEqual(ui.requests,[]);ui.close();
}
({sourceCompleteness,completionIsStrict,mergeAndSafeLinks,deadlinesUseOnlyAuthoritativeOverdue,malformedSourcesAreNotEmpty,boundedAndPrioritySorted,refreshPreservesKeyboardContext}[process.argv[1]])();
console.log('OK');
'''


class AttentionMarkupTests(unittest.TestCase):
    def test_is_labelled_with_real_full_list_routes_and_no_network_behavior(self):
        markup = workspace_attention()
        page = html.fromstring('<main>' + markup + '</main>')
        root = page.get_element_by_id('eis-attention')
        self.assertEqual(root.get('aria-labelledby'), 'eis-attention-title')
        self.assertEqual(root.get('aria-busy'), 'true')
        self.assertEqual(page.get_element_by_id('eis-attention-retry').get('type'), 'button')
        self.assertEqual(page.xpath('//*[@class="eis-attention-footer"]//a/@href'),
                         ['/account/home', '/account/workbench'])
        ids = page.xpath('//*[@id]/@id')
        self.assertEqual(len(ids), len(set(ids)))
        self.assertNotIn('fetch(', markup)
        self.assertNotIn('innerHTML', markup)


class AttentionBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which('node'):
            raise unittest.SkipTest('Node is unavailable')
        cls.env = os.environ.copy()
        modules = Path('/tmp/eis-dashboard-dom/node_modules')
        if modules.is_dir():
            cls.env['NODE_PATH'] = os.pathsep.join(filter(None, [cls.env.get('NODE_PATH'), str(modules)]))
        cls.env['TZ'] = 'America/Los_Angeles'
        check = subprocess.run(['node', '-e', "require('jsdom')"], env=cls.env, capture_output=True)
        if check.returncode:
            raise unittest.SkipTest('jsdom is unavailable')

    def run_case(self, case):
        result = subprocess.run(['node', '-e', NODE, case], input=workspace_attention(), text=True,
                                capture_output=True, env=self.env, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_initial_partial_failure_retry_and_empty_states(self):
        self.run_case('sourceCompleteness')

    def test_legal_completion_requires_real_document_count(self):
        self.run_case('completionIsStrict')

    def test_deduplicates_and_keeps_safe_actions_and_stale_information(self):
        self.run_case('mergeAndSafeLinks')

    def test_deadlines_preserve_calendar_day_and_server_overdue_flag(self):
        self.run_case('deadlinesUseOnlyAuthoritativeOverdue')

    def test_malformed_or_unknown_results_never_imply_all_clear(self):
        self.run_case('malformedSourcesAreNotEmpty')

    def test_errors_sort_first_and_large_lists_are_bounded(self):
        self.run_case('boundedAndPrioritySorted')

    def test_refresh_keeps_keyboard_focus_or_a_relevant_fallback(self):
        self.run_case('refreshPreservesKeyboardContext')


if __name__ == '__main__':
    unittest.main()
