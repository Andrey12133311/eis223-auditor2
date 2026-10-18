"""Exercise the personal INN form and filters against a real DOM, without EIS."""
import os
from pathlib import Path
import shutil
import subprocess
import unittest

from scripts.account_pages_v1 import HOME


NODE_TEST = r"""
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {JSDOM}=require('jsdom');
const html=fs.readFileSync(0,'utf8');
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const purchase={reg_number:'32616412317',title:'<script>unsafe</script>',customer:'Заказчик',
 customer_inn:'7707083893',job_status:'done',violations:1,violation_types:['Сроки'],
 started_at:'2026-10-18T12:00:00Z'};

async function start(responder,query=''){
 const dom=new JSDOM(html,{url:'https://site.example/account/home'+query,runScripts:'outside-only'});
 const w=dom.window,doc=w.document,calls=[],timers=new Map();let nextTimer=1;
 w.setTimeout=(fn,delay)=>{const id=nextTimer++;timers.set(id,{fn,delay});return id};
 w.clearTimeout=id=>timers.delete(id);
 w.fetch=async(url,options={})=>{
  const call={url,method:options.method||'GET',options};calls.push(call);
  const answer=await responder(call);
  return {ok:true,status:200,json:async()=>answer};
 };
 for(const script of doc.querySelectorAll('script'))w.eval(script.textContent);
 await flush();
 return {dom,w,doc,calls,timers,
  el:id=>doc.getElementById(id),
  submit:async inn=>{
   doc.getElementById('manual-inn').value=inn;
   doc.getElementById('manual-inn-search').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));
   await flush();
  },
  tick:async()=>{const pending=[...timers.values()];timers.clear();for(const t of pending)await t.fn();await flush()},
 };
}

async function journey(){
 let reads=0,added=false;
 const ui=await start(call=>{
  if(call.url==='/me/manual-checks')return added?[purchase]:[];
  if(call.url==='/me/manual-checks/'+purchase.reg_number){added=true;return {accepted:true}};
  if(call.method==='POST')return {id:'search-one',status:'queued',existing:[],search_pages_pending:1,
    bridge:{server_active:true,inn_search_available:true}};
  reads++;
  return {id:'search-one',job:{kind:'inn',status:'done'},existing:reads===1?[]:[purchase,purchase],
   verification_pending:reads===1?1:0,search_pages_pending:0,
   bridge:{server_active:true,inn_search_available:true}};
 });
 assert.equal(ui.el('manual-inn-submit').textContent,'Проверить по ИНН');
 await ui.submit('7707083893');
 const post=ui.calls.find(c=>c.url==='/me/inn-search');
 assert.equal(post.method,'POST');
 assert.deepEqual(JSON.parse(post.options.body),{inn:'7707083893'});
 assert.equal(post.options.credentials,'same-origin');
 assert.match(ui.el('manual-inn-message').textContent,/сохранён в очереди VPS/);
 assert.doesNotMatch(ui.el('manual-inn-message').textContent,/следующих страницах/);
 await ui.tick();
 assert.match(ui.el('manual-inn-message').textContent,/проверяет ИНН заказчика/);
 assert.equal(ui.timers.size,1,'verification must continue polling after root discovery completed');
 await ui.tick();
 assert.match(ui.el('manual-inn-message').textContent,/завершён.*Найдено закупок: 1/);
 assert.equal(ui.timers.size,0,'a finished search stops polling');
 assert.equal(ui.el('manual-inn-results').children.length,1,'duplicate registrations render once');
 assert.equal(ui.el('manual-inn-table').hidden,false);
 assert.equal(ui.el('manual-inn-results').querySelector('script'),null,'source strings remain text');
 assert.ok(!ui.calls.some(c=>c.url.startsWith('/me/manual-checks/')&&c.method==='POST'),
  'finding a result does not silently add it to personal checks');
 ui.el('manual-inn-results').querySelector('button').click();await flush();
 assert.equal(ui.calls.filter(c=>c.url==='/me/manual-checks/'+purchase.reg_number&&c.method==='POST').length,1);
 assert.equal(ui.el('manual-rows').children.length,1,'explicit check updates personal list');
 ui.dom.window.close();
}

async function waiting(){
 for(const [bridge,expected] of [
  [{server_active:false,inn_search_available:false},/VPS сейчас не на связи/],
  [{server_active:true,inn_search_available:false},/требуется обновление/],
 ]){
  const ui=await start(call=>call.url==='/me/manual-checks'?[]:{id:'waiting',job:{status:'done'},
   existing:[],search_pages_pending:2,verification_pending:1,bridge});
  await ui.submit('7707083893');await ui.tick();
  assert.match(ui.el('manual-inn-message').textContent,expected);
  assert.doesNotMatch(ui.el('manual-inn-message').textContent,/следующих страницах|проверяет ИНН/);
  assert.equal(ui.timers.size,1,'offline or old workers leave a saved request polling');
  ui.dom.window.close();
 }
 const ui=await start(call=>call.url==='/me/manual-checks'?[]:{id:'paged',job:{status:'done'},
  existing:[],search_pages_pending:1,bridge:{server_active:true,inn_search_available:true}});
 await ui.submit('7707083893');await ui.tick();
 assert.match(ui.el('manual-inn-message').textContent,/следующих страницах/);
 assert.equal(ui.timers.size,1);
 ui.dom.window.close();
}

async function filtersAndPrefill(){
 const second={...purchase,reg_number:'32616412318',customer:'Другой заказчик',violations:0,
   violation_types:[],job_status:'queued'};
 const ui=await start(()=>[purchase,second],'?inn=7707083893');
 assert.equal(ui.el('manual-inn').value,'7707083893');
 assert.ok(!ui.calls.some(c=>c.url==='/me/inn-search'),'favorite link prefills without submitting');
 for(const [id,value] of [['manual-search','missing'],['manual-status','done'],
  ['manual-date-start','2026-10-19'],['manual-date-end','2026-10-20'],['manual-violation-type','Сроки']]){
  ui.el(id).value=value;ui.el(id).dispatchEvent(new ui.w.Event('input',{bubbles:true}));
 }
 ui.el('manual-only-violations').checked=true;
 assert.equal(ui.timers.size,1);
 ui.el('manual-filters-reset').click();
 assert.equal(ui.timers.size,0,'reset cancels pending filter rendering');
 for(const id of ['manual-search','manual-status','manual-date-start','manual-date-end','manual-violation-type']){
  assert.equal(ui.el(id).value,'');
 }
 assert.equal(ui.el('manual-only-violations').checked,false);
 assert.equal(ui.el('manual-rows').children.length,2);
 assert.equal(ui.calls.length,1,'personal filters and reset use existing data');
 assert.equal(ui.el('manual-inn').value,'7707083893','list reset does not erase the separate INN form');
 await ui.submit('bad');
 assert.equal(ui.calls.length,1,'invalid INN does not submit');
 assert.match(ui.el('manual-inn-message').textContent,/10 или 12 цифр/);
 ui.dom.window.close();
 const invalid=await start(()=>[],'?inn=bad');
 assert.equal(invalid.el('manual-inn').value,'');invalid.dom.window.close();
}

({journey,waiting,filtersAndPrefill}[process.argv[1]])().then(()=>console.log('OK')).catch(error=>{
 console.error(error);process.exitCode=1;
});
"""


class ManualInnUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("node"):
            raise unittest.SkipTest("Node is unavailable")
        cls.env = os.environ.copy()
        modules = Path("/tmp/eis-dashboard-dom/node_modules")
        if modules.is_dir():
            cls.env["NODE_PATH"] = os.pathsep.join(filter(None, [cls.env.get("NODE_PATH"), str(modules)]))
        check = subprocess.run(["node", "-e", "require('jsdom')"], env=cls.env, capture_output=True)
        if check.returncode:
            raise unittest.SkipTest("jsdom is unavailable")

    def run_dom(self, case):
        result = subprocess.run(["node", "-e", NODE_TEST, case], input=HOME, text=True,
                                capture_output=True, env=self.env, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_inn_submit_poll_verify_and_explicit_check(self):
        self.run_dom("journey")

    def test_waiting_status_does_not_claim_offline_vps_is_searching(self):
        self.run_dom("waiting")

    def test_personal_filter_reset_and_favorite_inn_prefill(self):
        self.run_dom("filtersAndPrefill")
