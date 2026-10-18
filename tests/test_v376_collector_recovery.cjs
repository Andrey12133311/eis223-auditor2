// No network calls, tokens or real procurement data. Run with stock Node.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../scripts/v299_browser_collector.js'),'utf8');
const section=(start,end)=>source.slice(source.indexOf(start),source.indexOf(end));
const transport=section('  function backendFailure(', '  async function eisHtml(');
const poll=section('  async function pollManual()', '  manualTimer=setInterval(');
const run=section('  async function run()', '  /* Manual searches');
function setup(extra={}) {
  const context=vm.createContext({
    BACKEND_RETRY_MS:60000, BACKOFF:1200000, INTERVAL:300000,
    active:true,controller:new AbortController(),RAIL:'https://backend.test',key:'test-only',
    COLLECTOR_VERSION:'376',ArrayBuffer,Date,Error,Number,String,Math,
    pausedUntil:0,backendPausedUntil:0,pendingReport:null,busy:false,manualBusy:false,
    log:()=>{},uploaded:0,cycles:0,rotatingIndex:0,
    setTimeout:()=>1,heartbeat:async()=>{},...extra
  });
  vm.runInContext(transport+poll+run+';this.api=api;this.pollManual=pollManual;this.run=run;',context);
  return context;
}
function response(status,body={},headers={}) {
  return {status,ok:status>=200&&status<300,headers:{get:k=>headers[k]},json:async()=>body};
}
(async()=>{
  for(const status of [408,429,500,502,503,504,507]) {
    const c=setup({request:async()=>response(status,{detail:'temporarily unavailable'})});
    await assert.rejects(c.api('/test'),e=>e.backend&&e.retryMs===60000);
    assert.ok(c.pausedUntil>Date.now());
  }
  const c=setup({request:async()=>response(507,{code:'EIS223_STORAGE_FULL',retry_after_seconds:120})});
  await assert.rejects(c.api('/test'),e=>e.backend&&e.retryMs===120000);
  const header=setup({request:async()=>response(503,{}, {'Retry-After':'300'})});
  await assert.rejects(header.api('/test'),e=>e.retryMs===300000);
  const network=setup({request:async()=>{throw new TypeError('Failed to fetch')}});
  await assert.rejects(network.api('/test'),e=>e.backend);
  const html=setup({request:async()=>({...response(502),json:async()=>{throw Error('HTML')}})});
  await assert.rejects(html.api('/test'),e=>e.backend);
  for(const status of [400,401,403,404,422,426]) {
    const rejected=setup({request:async()=>response(status)});
    await assert.rejects(rejected.api('/test'),e=>!e.backend);
    assert.equal(rejected.pausedUntil,0);
  }
  const stopped=setup({active:false,request:async()=>{throw Error('stopped')}});
  await assert.rejects(stopped.api('/test'),e=>!e.backend);

  for(const body of [null, "bad", 5, []]) {
    for(const status of [200,503,507]) {
      const invalid=setup({request:async()=>response(status,body)});
      await assert.rejects(invalid.api('/test'),e=>e.backend);
    }
  }

  // A full metadata-only job succeeds, but its report hits a full disk.
  // Resume sends exactly that original result before leasing anything else.
  const calls=[];let broken=true;
  const jobs=setup({
    discoverFromOfficialCard:async()=>{},
    request:async(url,options)=>{
      calls.push({url,body:options.body});
      if(url.endsWith('/next'))return response(200,{job:calls.length===1?{id:'abc',kind:'reg',target:'32610000001',metadata_only:true}:null});
      return broken?response(507,{code:'EIS223_STORAGE_FULL'}):response(200,{});
    }
  });
  await jobs.pollManual();
  assert.equal(jobs.pendingReport.report.status,'done');
  assert.equal(jobs.manualBusy,false);
  await jobs.pollManual();assert.equal(calls.length,2); // shared cooldown
  broken=false;jobs.pausedUntil=0;jobs.backendPausedUntil=0;
  await jobs.pollManual();
  assert.equal(calls[2].url,'https://backend.test/api/v301/bridge/report/abc');
  assert.equal(calls[2].body,calls[1].body);
  assert.equal(calls[3].url,'https://backend.test/api/v301/bridge/next');
  assert.equal(jobs.pendingReport,null);

  // A failed EIS rate-limit report retries after the shorter backend
  // cooldown, without waiting 20 minutes or starting a new EIS search.
  let reports=0;
  const eisPaused=setup({pausedUntil:Date.now()+1200000,
    pendingReport:{id:'limited',report:{status:'deferred',defer:1200}},
    request:async(url)=>{assert.ok(url.endsWith('/report/limited'));reports++;return response(200,{})}
  });
  await eisPaused.pollManual();
  assert.equal(reports,1);assert.equal(eisPaused.pendingReport,null);

  // An upload-side outage defers the original job, without marking its
  // documents unavailable or contacting EIS again while storage is blocked.
  const deferred=setup({request:async()=>response(200,{job:{id:'xyz',kind:'reg',target:'32610000002'}})});
  deferred.handleRegistry=async()=>{throw vm.runInContext('backendFailure("storage")',deferred)};
  await deferred.pollManual();
  assert.equal(deferred.pendingReport.report.status,'deferred');
  assert.equal(deferred.pendingReport.report.defer,60);
  const before=deferred.cycles;
  await deferred.run();assert.equal(deferred.cycles,before);

  // Metadata writes must propagate backend errors, rather than claim done.
  const metadata=section('  async function reportOfficialPrice(', '  async function discoverFromOfficialCard(');
  const m=setup({request:async()=>response(507,{code:'EIS223_STORAGE_FULL'}),
    cardFactsFromDocument:()=>({prices:[{}],fields:{},price_formula:''})});
  vm.runInContext(metadata+';this.price=reportOfficialPrice;this.buyer=reportOfficialBuyer;',m);
  await assert.rejects(m.price('32610000001','source',{}),e=>e.backend);
  await assert.rejects(m.buyer('32610000001','source',{name:'Fixture'},''),e=>e.backend);
  assert.match(source,/if\(e.backoff\|\|e.backend\)throw e;/);
  console.log('V376 collector recovery: HTTP/network, metadata, cooldown and exact report replay passed');
})().catch(e=>{console.error(e);process.exitCode=1});
