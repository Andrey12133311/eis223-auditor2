"""Exercise the audit-card editor with a real DOM and controlled HTTP responses."""
import os
from pathlib import Path
import shutil
import subprocess
import unittest

from scripts.account_memo_requisites_ui_v1 import render_memo_requisites_editor


REG = "32616474277"
NODE_TEST = r'''
const assert=require('node:assert/strict'),fs=require('node:fs');
const {JSDOM}=require('jsdom');
const html=fs.readFileSync(0,'utf8');
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const defaults={recipient:'Руководителю',executor:'Иванов И. И.',phone:'123'};
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}}
function start(responder){
 const dom=new JSDOM('<!doctype html><html lang="ru"><body>'+html+'</body></html>',{url:'https://site.example/thematic/card/batch/32616474277',runScripts:'outside-only'});
 const w=dom.window,doc=w.document,calls=[];
 w.HTMLDialogElement.prototype.showModal=function(){this.open=true};
 w.HTMLDialogElement.prototype.close=function(){if(!this.open)return;this.open=false;this.dispatchEvent(new w.Event('close'))};
 w.fetch=async(url,options={})=>{const call={url,options,method:options.method||'GET'};calls.push(call);const answer=await responder(call,calls.length);return {ok:(answer.status||200)<400,status:answer.status||200,json:async()=>answer.body}};
 for(const script of doc.querySelectorAll('script'))w.eval(script.textContent);
 const root=doc.querySelector('.memo-requisites-widget'),el=role=>root.querySelector('[data-role="'+role+'"]');
 const dialog=root.querySelector('dialog'),form=el('form'),field=key=>form.elements.namedItem(key);
 return {dom,w,doc,calls,root,el,dialog,form,field,
  open:async()=>{el('open').focus();el('open').click();await flush()},
  submit:async()=>{form.dispatchEvent(new w.Event('submit',{cancelable:true,bubbles:true}));await flush()},
  close:async()=>{el('close').click();await flush()},
 };
}
async function journey(){
 let saved={...defaults},events=[];
 const ui=start(call=>{if(call.method==='PUT'){saved=JSON.parse(call.options.body);saved.recipient=saved.recipient.trim();return {body:{saved:true,fields:saved}}}return {body:{saved:false,fields:saved}}});
 assert.equal(ui.calls.length,0,'rendering a card must not query requisites before opening');
 assert.equal(ui.el('open').getAttribute('aria-haspopup'),'dialog');
 assert.equal(ui.dialog.getAttribute('aria-labelledby'),ui.root.querySelector('h2').id);
 ui.root.addEventListener('eis223:memo-requisites-saved',event=>events.push(event.detail));
 await ui.open();
 assert.equal(ui.dialog.open,true);assert.equal(ui.field('recipient').value,defaults.recipient);
 assert.equal(ui.doc.activeElement,ui.field('recipient'));assert.equal(ui.el('fields').disabled,false);
 ui.field('recipient').value='  Начальнику <script>unsafe</script>  ';
 ui.field('executor').value='Петров П. П.';ui.field('phone').value='';
 await ui.submit();
 const call=ui.calls[1];assert.equal(call.method,'PUT');
 assert.equal(call.url,'/me/memo-requisites/32616474277');
 assert.equal(call.options.credentials,'same-origin');assert.equal(call.options.cache,'no-store');
 assert.equal(call.options.headers['Content-Type'],'application/json');
 assert.deepEqual(JSON.parse(call.options.body),{recipient:'  Начальнику <script>unsafe</script>  ',executor:'Петров П. П.',phone:''});
 assert.equal(ui.field('recipient').value,'Начальнику <script>unsafe</script>');
 assert.match(ui.el('status').textContent,/Реквизиты сохранены/);
 assert.equal(ui.root.querySelectorAll('script').length,0,'API values must never become markup');
 assert.equal(events.length,1);assert.equal(events[0].reg,'32616474277');
 await ui.close();assert.equal(ui.dialog.open,false);assert.equal(ui.doc.activeElement,ui.el('open'));
 await ui.open();assert.equal(ui.calls.length,3);assert.equal(ui.field('phone').value,'');assert.equal(ui.field('executor').value,'Петров П. П.');
 ui.dom.window.close();
}
async function loadFailures(){
 let status=502;
 const ui=start(()=>status===200?{body:{fields:defaults}}:{status,body:{detail:'must not inject <script>bad</script>'}});
 await ui.open();assert.match(ui.el('status').textContent,/Сервер временно недоступен/);
 assert.equal(ui.el('fields').disabled,true);assert.equal(ui.el('save').disabled,true);assert.equal(ui.el('retry').hidden,false);
 await ui.submit();assert.equal(ui.calls.length,1,'failed loads must not save empty fields over existing values');
 status=401;ui.el('retry').click();await flush();
 assert.equal(ui.el('login').hidden,false);assert.equal(ui.el('login').getAttribute('href'),'/account/login');
 status=200;ui.el('retry').click();await flush();
 assert.equal(ui.el('login').hidden,true);assert.equal(ui.el('save').disabled,false);assert.equal(ui.field('executor').value,defaults.executor);
 ui.dom.window.close();
}
async function saveFailures(){
 let response=deferred(),writes=0;
 const ui=start(call=>{if(call.method==='PUT'){writes++;return response.promise}return {body:{fields:defaults}}});
 await ui.open();ui.field('recipient').value='Правка не должна потеряться';await ui.submit();
 await ui.submit();assert.equal(writes,1,'double submit must not duplicate the save');
 assert.equal(ui.el('close').disabled,true);
 ui.dialog.dispatchEvent(new ui.w.KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true}));assert.equal(ui.dialog.open,true,'saving must not be silently abandoned');
 response.resolve({status:502,body:{}});await flush();
 assert.equal(ui.field('recipient').value,'Правка не должна потеряться');assert.equal(ui.el('save').disabled,false);assert.equal(ui.el('close').disabled,false);
 assert.match(ui.el('status').textContent,/Не удалось сохранить/);
 response=deferred();await ui.submit();assert.equal(writes,2);
 response.resolve({body:{saved:true,fields:{...defaults,recipient:'Правка не должна потеряться'}}});await flush();
 assert.match(ui.el('status').textContent,/Реквизиты сохранены/);ui.dom.window.close();
}
async function staleLoads(){
 const first=deferred(),second=deferred();let count=0;
 const ui=start(()=>++count===1?first.promise:second.promise);
 await ui.open();assert.equal(ui.el('fields').disabled,true);await ui.close();
 assert.equal(ui.calls[0].options.signal.aborted,true);await ui.open();
 second.resolve({body:{saved:true,fields:{...defaults,recipient:'Свежие данные'}}});await flush();
 first.resolve({body:{fields:{...defaults,recipient:'Устаревшие данные'}}});await flush();
 assert.equal(ui.field('recipient').value,'Свежие данные');assert.match(ui.el('status').textContent,/Загружены ваши реквизиты/);
 assert.equal(ui.el('save').disabled,false);ui.dom.window.close();
}
async function keyboardAndTimeout(){
 let pending=false;
 const ui=start(call=>pending?new Promise((resolve,reject)=>call.options.signal.addEventListener('abort',()=>reject(new ui.w.DOMException('aborted','AbortError')))):{body:{fields:defaults}});
 await ui.open();ui.el('save').focus();
 ui.dialog.dispatchEvent(new ui.w.KeyboardEvent('keydown',{key:'Tab',bubbles:true,cancelable:true}));assert.equal(ui.doc.activeElement,ui.field('recipient'));
 ui.dialog.dispatchEvent(new ui.w.KeyboardEvent('keydown',{key:'Tab',shiftKey:true,bubbles:true,cancelable:true}));assert.equal(ui.doc.activeElement,ui.el('save'));
 ui.dialog.dispatchEvent(new ui.w.KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true}));assert.equal(ui.dialog.open,false);assert.equal(ui.doc.activeElement,ui.el('open'));
 let timeout;ui.w.setTimeout=fn=>{timeout=fn;return 1};ui.w.clearTimeout=()=>{};pending=true;
 await ui.open();timeout();await flush();
 assert.match(ui.el('status').textContent,/не ответил вовремя/);assert.equal(ui.el('retry').hidden,false);assert.equal(ui.el('save').disabled,true);
 ui.dom.window.close();
}
({journey,loadFailures,saveFailures,staleLoads,keyboardAndTimeout}[process.argv[1]])().then(()=>console.log('OK')).catch(error=>{console.error(error);process.exitCode=1});
'''


class MemoEditorMarkupTests(unittest.TestCase):
    def test_invalid_registration_cannot_enter_markup(self):
        for reg in (None, "123", '32616474277\"><script>', "32616474277\n"):
            with self.assertRaises(ValueError):
                render_memo_requisites_editor(reg)


class MemoEditorDomTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("node"):
            raise unittest.SkipTest("Node is unavailable")
        cls.env = os.environ.copy()
        modules = Path("/tmp/eis-dashboard-dom/node_modules")
        if modules.is_dir():
            cls.env["NODE_PATH"] = os.pathsep.join(filter(None, [cls.env.get("NODE_PATH"), str(modules)]))
        result = subprocess.run(["node", "-e", "require('jsdom')"], env=cls.env, capture_output=True)
        if result.returncode:
            raise unittest.SkipTest("jsdom is unavailable")

    def run_dom(self, case):
        result = subprocess.run(["node", "-e", NODE_TEST, case], input=render_memo_requisites_editor(REG),
                                text=True, capture_output=True, env=self.env, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_load_edit_save_and_reopen(self):
        self.run_dom("journey")

    def test_failed_load_keeps_save_disabled_and_can_retry(self):
        self.run_dom("loadFailures")

    def test_failed_save_preserves_edits_and_prevents_double_submit(self):
        self.run_dom("saveFailures")

    def test_closed_dialog_ignores_stale_response_after_reopen(self):
        self.run_dom("staleLoads")

    def test_keyboard_focus_escape_and_network_timeout(self):
        self.run_dom("keyboardAndTimeout")


if __name__ == "__main__":
    unittest.main()
