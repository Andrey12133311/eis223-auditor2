// Exercise the real intake and reporting functions without network or a browser.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../scripts/v299_browser_collector.js'),'utf8');
function section(start,end){
  const from=source.indexOf(start),to=source.indexOf(end,from);
  assert.ok(from>=0&&to>from,`Missing collector section: ${start}`);
  return source.slice(from,to);
}
const intake=section('  function originalIntake(', '  async function heartbeat(');
const poll=section('  async function pollManual()', '  manualTimer=setInterval(');
const run=section('  async function run()', '  /* Manual searches');
const REG='32610000001';
function setup({total=12,received=total,registered=total,visible=total,
                checked=0,extension='.pdf',downloadFailure=false}={}){
  const state={manifest_documents:registered,originals_received:received,
    visible_attachments:visible,checked_documents:checked,needs_metadata:false,
    // Legacy backend field includes the audit count, not just transport.
    complete:checked>=total};
  const docs=Array.from({length:total},(_,i)=>({uid:String(i),
    name:`Оригинал ${i}${extension}`,url:`https://example.test/file/${i}`}));
  const manifest=docs.slice(0,registered).map((d,i)=>({...d,
    ext:extension,received:i<received}));
  const reports=[],downloads=[],timers=[];
  let claimed=false,discoveries=0;
  const c=vm.createContext({
    active:true,busy:false,manualBusy:false,backendPausedUntil:0,pausedUntil:0,
    pendingReport:null,uploaded:0,MAX_FILES:20,MAX_BUYS:2,INTERVAL:300000,
    BACKOFF:1200000,EXT:/\.(?:pdf|zip)$/i,Date,Number,Math,String,Error,URL,
    cycles:0,rotatingIndex:0,SITE:'https://zakupki.gov.ru',SEARCH:'https://zakupki.gov.ru/search',
    log:()=>{},setTimeout:(fn,delay)=>{timers.push(delay);return 1;},heartbeat:async()=>{},
    discoverFromOfficialCard:async()=>{discoveries++;return {docs,source:'https://zakupki.gov.ru/documents',
      landing:'https://zakupki.gov.ru/card',customer:{name:'Заказчик',inn:'7700000000'},title:'Поставка'};},
    getKnown:async()=>({[REG]:{...state}}),
    downloadOfficialFile:async item=>{downloads.push(item.uid);
      if(downloadFailure)throw Error('Оригинал недоступен');
      return {extension,buffer:new ArrayBuffer(10)};},
    preparedFileName:item=>item.name,
    uploadOriginalData:async()=>{c.uploaded++;state.originals_received++;return true;},
    api:async url=>{
      if(url.endsWith('/next')){if(claimed)return {job:null};claimed=true;
        return {job:{id:'fixture',kind:'reg',target:REG}};}
      if(url.includes('/manifest/'))return {documents:manifest};
      if(url.endsWith('/public-manifest'))return {accepted:true};
      throw Error('Unexpected request '+url);
    },
    flushPendingReport:async()=>{if(c.pendingReport){
      reports.push(JSON.parse(JSON.stringify(c.pendingReport.report)));c.pendingReport=null;}},
    eisHtml:async()=>({}),registryNumbers:()=>[REG],
  });
  vm.runInContext(intake+poll+run+
    ';this.originalIntake=originalIntake;this.handleRegistry=handleRegistry;'+
    'this.pollManual=pollManual;this.run=run;',c);
  return {c,state,docs,reports,downloads,discoveries:()=>discoveries};
}
(async()=>{
  // Finished downloads never wait for one checked database row per attachment.
  // The same case covers a pending audit, SHA dedup and archive extraction.
  for(const fixture of [{checked:0},{checked:1},{total:1,extension:'.zip',checked:0}]){
    const {c,reports,downloads}=setup(fixture);
    await c.pollManual();
    assert.equal(reports.length,1);
    assert.equal(reports[0].status,'done'); // download queue only
    assert.equal(reports[0].accepted,0); // existing bytes are not new progress
    assert.equal(reports[0].found,1); // backend must dispatch its offline audit
    assert.match(reports[0].detail,/оригиналы ЕИС сохранены/);
    assert.match(reports[0].detail,/результат юридической проверки ожидается/);
    assert.doesNotMatch(reports[0].detail,/получены и проверены|проверка завершена/i);
    assert.deepEqual(downloads,[]);
  }

  // New uploads also complete transport before asynchronous legal analysis.
  const fresh=setup({total:2,received:0,checked:0});
  await fresh.c.pollManual();
  assert.equal(fresh.reports[0].status,'done');
  assert.equal(fresh.reports[0].accepted,2);
  assert.equal(fresh.state.checked_documents,0);
  assert.equal(fresh.downloads.length,2);

  // A later backend snapshot cannot shrink the expectation just observed
  // during this upload, even if the final /known response is stale.
  const stale=setup({received:11,visible:15});
  let snapshots=0;
  stale.c.getKnown=async()=>({[REG]:{...stale.state,
    visible_attachments:++snapshots===1?15:12}});
  await stale.c.pollManual();
  assert.equal(stale.reports[0].status,'partial');
  assert.match(stale.reports[0].detail,/12 из 15/);

  // A tab with fewer links cannot shrink persisted official expectations.
  const older=setup({total:5,received:5,registered:5,visible:12,checked:50});
  await older.c.pollManual();
  assert.equal(older.reports[0].status,'partial');
  assert.match(older.reports[0].detail,/5 из 12/);
  assert.equal(older.reports[0].accepted||0,0);

  // Unregistered/unavailable links remain incomplete even with many checked rows.
  const missing=setup({total:12,received:11,registered:11,visible:12,
    checked:50,downloadFailure:true});
  await missing.c.pollManual();
  assert.equal(missing.reports[0].status,'partial');
  assert.match(missing.reports[0].detail,/11 из 12/);
  assert.deepEqual(missing.downloads,['11']);

  const {c}=setup();
  assert.equal(c.originalIntake({},0).complete,false);
  assert.equal(c.originalIntake({manifest_documents:12,originals_received:12,
    visible_attachments:15},5).complete,false);
  assert.equal(c.originalIntake({manifest_documents:12,originals_received:12,
    visible_attachments:12},15).complete,false);
  assert.equal(c.originalIntake({manifest_documents:12,originals_received:12,
    visible_attachments:12},5).complete,true);

  // Background discovery prioritizes genuinely missing bytes, without spinning
  // on already-saved duplicates/archives while the legal worker is pending.
  const background=setup({checked:0});
  await background.c.run();
  assert.equal(background.discoveries(),0);
  // The existing periodic revisit still looks for newly published originals.
  background.c.cycles=2;
  await background.c.run();
  assert.equal(background.discoveries(),1);

  console.log('V377 collector completion: saved bytes, pending audit, dedup, archives, missing expectations and periodic rediscovery passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
