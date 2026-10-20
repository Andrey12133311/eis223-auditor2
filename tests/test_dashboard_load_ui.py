"""Browser regression: feedback never changes responses or creates retries."""
import json
import os
import re
import shutil
import subprocess
import unittest
from dashboard_load_ui import SCRIPT

RUNNER = r"""
const {JSDOM, VirtualConsole} = require('jsdom');
const input=JSON.parse(require('fs').readFileSync(0,'utf8'));
const errors=[],vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
const dom=new JSDOM('<!doctype html><html><head></head><body><main><form id="filters"></form></main></body></html>',{url:'https://test.example/',runScripts:'outside-only',virtualConsole:vc});
const w=dom.window;let calls=0,submits=0,lastOptions=null,lastInput=null;
const form=w.document.getElementById('filters');form.addEventListener('submit',e=>{e.preventDefault();submits++});
const text=()=>w.document.getElementById('eis-dashboard-load-text')?.textContent||'';
const box=()=>w.document.getElementById('eis-dashboard-load-notice');
const button=()=>w.document.getElementById('eis-dashboard-load-retry');
const settle=()=>new Promise(r=>setTimeout(r,15));
(async()=>{
 let response=new Response(JSON.stringify({stats:{purchases:0}}),{status:200});
 const error=new Error('fixture');let pending=[];
 w.fetch=async (url,options)=>{calls++;lastOptions=options;lastInput=url;
  if(input.case==='network')throw error;
  if(input.case==='order')return new Promise(resolve=>pending.push(resolve));
  return response;
 };
 w.eval(input.script);let result={};
 if(input.case==='warming'){
  response=new Response(JSON.stringify({detail:'warming'}),{status:503,headers:{'Retry-After':'120','X-EIS-Dashboard-State':'warming'}});
  const actual=await w.fetch('/api/v257/dashboard?inn=7700000000');await settle();
  result={same:actual===response,status:actual.status,text:text(),disabled:button().disabled,calls,submits};
 }else if(input.case==='retry'){
  response=new Response('{}',{status:503,headers:{'Retry-After':'0','X-EIS-Dashboard-State':'warming'}});
  await w.fetch('/api/v257/dashboard');button().click();await settle();result={calls,submits};
 }else if(input.case==='stale'){
  response=new Response(JSON.stringify({membership_stale:true,membership_refreshed_at:'2026-10-20T18:40:00Z',stats:{purchases:123}}));
  const actual=await w.fetch('/api/v257/dashboard');await settle();result={text:text(),data:await actual.json(),calls};
 }else if(input.case==='other'){
  const opt={method:'POST',credentials:'include',headers:{'x-fixture':'retained'}};
  const actual=await w.fetch('/api/v257/dashboard',opt);await settle();result={same:actual===response,optionsSame:lastOptions===opt,text:text(),calls};
 }else if(input.case==='credentials'){
  const ctl=new AbortController(),opt={credentials:'same-origin',signal:ctl.signal};
  const actual=await w.fetch('/api/v257/dashboard?q=secret',opt);await settle();result={same:actual===response,optionsSame:lastOptions===opt,url:lastInput,text:text(),calls,data:await actual.json()};
 }else if(input.case==='network'){
  let same=false;try{await w.fetch('/api/v257/dashboard')}catch(e){same=e===error}
  await settle();result={same,text:text(),calls};
 }else if(input.case==='order'){
  const a=w.fetch('/api/v257/dashboard?q=old'),b=w.fetch('/api/v257/dashboard?q=new');
  pending[1](new Response(JSON.stringify({membership_stale:false})));await b;await settle();
  pending[0](new Response('{}',{status:503,headers:{'X-EIS-Dashboard-State':'warming','Retry-After':'10'}}));await a;await settle();
  result={text:text(),hidden:box()?.hidden??true,calls};
 }else if(input.case==='auth'){
  response=new Response('{}',{status:401});const actual=await w.fetch('/api/v257/dashboard');await settle();result={same:actual===response,text:text(),calls,submits};
 }
 result.errors=errors;dom.window.close();process.stdout.write(JSON.stringify(result));
})().catch(e=>{console.error(e);dom.window.close();process.exitCode=1});
"""


class DashboardLoadUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which('node'):
            raise unittest.SkipTest('Node required')
        probe = subprocess.run(['node','-e',"require('jsdom')"],capture_output=True,timeout=10)
        if probe.returncode:
            if os.environ.get('EIS_REQUIRE_BROWSER_TESTS') == '1':
                raise RuntimeError('CI requires jsdom; do not skip browser tests')
            raise unittest.SkipTest('jsdom required')

    def run_case(self, case):
        script=re.sub(r'^<script[^>]*>|</script>$','',SCRIPT.strip())
        p=subprocess.run(['node','-e',RUNNER],input=json.dumps({'case':case,'script':script}),text=True,capture_output=True,timeout=10)
        self.assertEqual(p.returncode,0,p.stderr)
        d=json.loads(p.stdout);self.assertEqual(d['errors'],[]);return d

    def test_warming_keeps_failure_and_respects_retry_after_without_automatic_request(self):
        r=self.run_case('warming')
        self.assertTrue(r['same']);self.assertTrue(r['disabled'])
        self.assertEqual((r['status'],r['calls'],r['submits']),(503,1,0))
        self.assertIn('не удалены',r['text'])

    def test_explicit_retry_sends_one_form_submit_only(self):
        self.assertEqual({k:v for k,v in self.run_case('retry').items() if k!='errors'}, {'calls':1,'submits':1})

    def test_stale_real_snapshot_is_labelled_without_changing_counts(self):
        r=self.run_case('stale');self.assertEqual(r['data']['stats']['purchases'],123)
        self.assertIn('последний полученный',r['text']);self.assertEqual(r['calls'],1)

    def test_post_requests_and_options_are_untouched(self):
        r=self.run_case('other');self.assertTrue(r['same'] and r['optionsSame']);self.assertEqual(r['text'],'')

    def test_abort_signal_credentials_filters_and_response_are_untouched(self):
        r=self.run_case('credentials');self.assertTrue(r['same'] and r['optionsSame'])
        self.assertEqual(r['url'],'/api/v257/dashboard?q=secret');self.assertEqual(r['data']['stats']['purchases'],0)

    def test_network_error_is_not_swallowed_or_retried(self):
        r=self.run_case('network');self.assertTrue(r['same']);self.assertEqual(r['calls'],1);self.assertIn('Сервер не ответил',r['text'])

    def test_old_request_error_cannot_replace_new_success(self):
        r=self.run_case('order');self.assertEqual(r['text'],'');self.assertTrue(r['hidden']);self.assertEqual(r['calls'],2)

    def test_auth_error_is_not_retried_or_disguised(self):
        r=self.run_case('auth');self.assertTrue(r['same']);self.assertEqual((r['calls'],r['submits']),(1,0));self.assertEqual(r['text'],'')

if __name__=='__main__':unittest.main()
