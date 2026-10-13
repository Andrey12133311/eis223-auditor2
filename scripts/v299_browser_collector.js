/* ЕИС 223-ФЗ → Railway. Запускается из консоли на zakupki.gov.ru.
 * Без расширений. Только официальная поисковая страница ЕИС.
 * ОДНА выдача поиска за 5 минут; до ДВУХ закупок за цикл; документы последовательно.
 * Работает только пока этот браузер и эта страница открыты.
 */
(async function () {
  "use strict";
  const SITE = "https://zakupki.gov.ru";
  const COLLECTOR_VERSION = "308"; // authoritative modern notice223 documents
  const RAIL = "https://web-production-1c2ea.up.railway.app";
  const INTERVAL = 300000; // не чаще одного поиска за пять минут
  const BACKOFF = 1200000; // 20 мин при блокировке ЕИС
  const MAX_BUYS = 2;
  const MAX_FILES = 12;
  const PAUSE_MS = 2500;
  const NUMBER = /^3[0-9]{10}$/;
  const EXT = /\.(docx|doc|xlsx|xls|pdf|rtf|odt|ods|zip|xml)$/i;

  if (location.origin !== SITE) {
    console.log("[ЕИС-5мин] Открой zakupki.gov.ru перед запуском.");
    return;
  }
  if (window.__eis223_five_minutes) {
    const previous=window.__eis223_five_minutes;
    if(previous.version===COLLECTOR_VERSION && previous.status()?.active) {
      console.log("[ЕИС-5мин] Актуальная версия v"+COLLECTOR_VERSION+" уже работает.");
      return;
    }
    try{previous.stop();}catch(e){console.warn("[ЕИС-5мин] Ошибка остановки старой версии:",e);}
    console.log("[ЕИС-5мин] Старая версия отключена, загружается v"+COLLECTOR_VERSION);
  }

  let key = prompt("Ключ EIS223_UPLOAD_TOKEN из Railway (токен ЕИС здесь не нужен):");
  if (!key || !key.trim()) {
    console.log("[ЕИС-5мин] Не указан ключ Railway.");
    return;
  }
  key = key.trim();
  let active = true, timer = null, busy = false;
  let manualTimer = null, manualBusy = false, pausedUntil = 0;
  let cycles = 0, uploaded = 0, admitted = 0, rotatingIndex = 0;
  const log = (...args) => console.log("[ЕИС-5мин]", ...args);
  const wait = ms => new Promise(ok => setTimeout(ok, ms));
  const controller = new AbortController();

  window.__eis223_five_minutes = {
    version:COLLECTOR_VERSION,
    stop() {
      active = false;
      clearTimeout(timer);
      clearInterval(manualTimer);
      controller.abort();
      log("Остановлено. Повторных запросов больше не будет.");
      delete window.__eis223_five_minutes;
    },
    status() {
      return {active, busy, manualBusy, cycles, admitted, uploaded,
        next_check: timer ? "через примерно 5 минут" : "после текущего цикла"};
    }
  };

  function isRateLimited(response) {
    return [403, 429, 503].includes(response.status);
  }
  async function request(url, options = {}, ms = 45000) {
    const ctrl = new AbortController();
    const timeout = setTimeout(() => ctrl.abort(), ms);
    try {
      const opt = {...options, signal:ctrl.signal};
      return await fetch(url, opt);
    } finally {
      clearTimeout(timeout);
    }
  }
  async function api(path, method, data) {
    const headers = {"x-eis223-upload-token":key,
      "x-eis223-collector-version":COLLECTOR_VERSION};
    if (data !== undefined) headers["content-type"] =
      data instanceof ArrayBuffer ? "application/octet-stream" : "application/json";
    const r = await request(RAIL + path,
      {method:method || "GET", headers,
        body:data === undefined ? undefined :
          data instanceof ArrayBuffer ? data : JSON.stringify(data)},
      60000);
    let body = {};
    try {body = await r.json();} catch (e) {
      throw new Error("Railway ответил не JSON: HTTP " + r.status);
    }
    if (!r.ok) throw new Error("Railway HTTP " + r.status + " " +
      String(body.detail || "").slice(0,160));
    return body;
  }
  async function eisHtml(path) {
    await wait(PAUSE_MS);
    const r = await request(SITE + path,
      {credentials:"same-origin",cache:"no-store",
        headers:{"Accept":"text/html,application/xhtml+xml"}}, 45000);
    if (isRateLimited(r)) {
      let e = new Error("ЕИС ограничила запросы: HTTP " + r.status);
      e.backoff = true;
      throw e;
    }
    if (!r.ok) throw new Error("ЕИС HTTP " + r.status + " " + path.slice(0,45));
    const html = await r.text();
    if (html.length > 6000000) throw new Error("Слишком большая HTML-страница");
    if (/captcha|робот|доступ временно ограничен/i.test(html.slice(0,2000)))
      throw Object.assign(new Error("ЕИС просит проверку браузера"),{backoff:true});
    const parsed=new DOMParser().parseFromString(html,"text/html");
    // Keep the URL actually returned after official EIS redirects.
    parsed.__eis223_source_url=r.url;
    return parsed;
  }
  const SEARCH = "/epz/order/extendedsearch/results.html?" +
    new URLSearchParams({
      sortBy:"UPDATE_DATE",sortDirection:"false",
      pageNumber:"1",recordsPerPage:"_20",
      showLotsInfoHidden:"false",fz223:"on",af:"on",
      currencyIdGeneral:"-1"
    });
  function registryNumbers(doc) {
    const numbers = new Set();
    for (const a of doc.querySelectorAll("a[href]")) {
      const href = a.getAttribute("href") || "";
      const match = href.match(/(?:[?&]regNumber=)(3[0-9]{10})(?:[&#]|$)/i);
      if (match) numbers.add(match[1]);
    }
    const txt = (doc.body?.textContent || "").slice(0,300000);
    if (numbers.size === 0) {
      const matches = txt.match(/(?:№|номер\s+закупки\s*:?)\s*(3[0-9]{10})/gi) || [];
      for (const m of matches) {
        const num = m.match(/3[0-9]{10}/);
        if (num) numbers.add(num[0]);
      }
    }
    return [...numbers].filter(x => NUMBER.test(x)).slice(0,30);
  }
  function searchTitle(doc,reg) {
    if(!doc)return "";
    for(const a of doc.querySelectorAll("a[href]")) {
      if(!(a.getAttribute("href")||"").includes("regNumber="+reg))continue;
      const block=a.closest(".search-registry-entry-block,.registry-entry,article,tr");
      const node=block?.querySelector(".registry-entry__body-title,.registry-entry__header-mid__title,.registry-entry__body-value,h2,h3");
      const value=(node?.textContent||"").split(/\s+/).join(" ").trim();
      if(value && value.length>12 && value.length<=800 && !/^[0-9]+$/.test(value))return value;
    }
    return "";
  }
  // Only certified public EIS file references are accepted. Reconstruct a
  // canonical URL from the exact 32-hex UID found in the official HTML.
  const FILE_PATH="/223/filestore/public/1.0/download/fz223/file.html";
  const EXT_NAME=/([^\\/:<>?"|*\r\n]{2,180}\.(?:docx|doc|xlsx|xls|pdf|rtf|odt|ods|zip|xml))(?=$|[\s,'"<>;)]|$)/i;
  const EIS_FILE_LINK=/(?:https?:\/\/zakupki\.gov\.ru)?\/223\/filestore\/public\/1\.0\/download\/fz223\/file\.html\?[^"'<>\\\s)]{0,420}/gi;
  function maybeFileUid(text) {
    if(!text)return null;
    const raw=String(text).replace(/&amp;/gi,"&").replace(/\\u0026/gi,"&");
    const matches=raw.match(EIS_FILE_LINK)||[];
    for(const href of matches) {
      try {
        const u=new URL(href,SITE);
        const uid=(u.searchParams.get("uid")||"").toUpperCase();
        if(u.origin===SITE&&u.pathname===FILE_PATH&&/^[0-9A-F]{32}$/.test(uid))
          return uid;
      }catch(e){}
    }
    return null;
  }
  function safeFilename(node){
    const values=[];
    let cur=node;
    for(let i=0;cur&&i<4;i++,cur=cur.parentElement){
      const attrs=["download","title","aria-label","data-file-name","data-filename"];
      for(const a of attrs){const v=cur.getAttribute?.(a);if(v)values.push(v);}
      if(cur.textContent&&cur.textContent.length<2400)values.push(cur.textContent);
      if(cur.matches?.("tr,li,.file,.attachment,.document-item,.file-item"))break;
    }
    for(let v of values){
      const found=String(v).replace(/\u00a0/g," ").match(EXT_NAME);
      if(!found)continue;
      const name=found[1].replace(/^\s*(?:скачать|файл|документ|загрузить|открыть)\s*[:–-]*\s*/ig,"")
        .replace(/^.*[\\/]/,"").replace(/[<>:"|?*]/g,"").trim().slice(0,190);
      if(EXT.test(name)&&name.length>4)return name;
    }
    return "";
  }
  function discoverDocs(doc,reg) {
    const documents=[],seen=new Set();
    const elements=doc.querySelectorAll("a,button,[onclick],[data-href],[data-url],[data-file-uid]");
    for(const a of elements) {
      const fields=["href","onclick","data-href","data-url","data-download-url"];
      let uid=null;
      for(const field of fields){
        uid=maybeFileUid(a.getAttribute?.(field));
        if(uid)break;
      }
      // Some official pages put only UID in data-file-uid, with a true
      // fz223 download-path in the containing row. Never trust bare UIDs.
      if(!uid)continue;
      if(seen.has(uid))continue;
      const name=safeFilename(a);
      if(!name)continue;
      seen.add(uid);
      documents.push({name,url:SITE+FILE_PATH+"?uid="+uid});
      if(documents.length>=70)break;
    }
    return documents;
  }
  function documentPageCandidates(reg,landing){
    const modern="/epz/order/notice/notice223/documents.html";
    const allowed=new Set([modern,"/223/purchase/public/purchase/info/documents.html"]);
    const candidates=[],seen=new Set();
    function add(raw) {
      try{
        const u=new URL(String(raw).replace(/&amp;/gi,"&"),SITE);
        if(u.origin!==SITE||!allowed.has(u.pathname))return;
        const number=u.searchParams.get("regNumber")||u.searchParams.get("purchaseNoticeNumber");
        if(number&&number!==reg)return;
        if(!number&&!/^[0-9]{1,15}$/.test(u.searchParams.get("noticeInfoId")||""))return;
        const out=u.pathname+u.search;
        if(!seen.has(out)){seen.add(out);candidates.push(out);}
      }catch(e){}
    }
    if(landing) {
      for(const el of landing.querySelectorAll("a[href],a[onclick],[data-href],[data-url]")){
        for(const field of ["href","onclick","data-href","data-url"]) {
          const raw=el.getAttribute(field)||"";
          if(raw.includes("/notice223/documents.html")){
            const m=raw.match(/(?:https?:\/\/zakupki\.gov\.ru)?\/epz\/order\/notice\/notice223\/documents\.html\?[^"'\\\s<>)]*/i);
            if(m)add(m[0]);
          }
        }
      }
    }
    add(modern+"?regNumber="+reg);
    // Newer cards sometimes route by notice number rather than regNumber.
    add(modern+"?purchaseNoticeNumber="+reg);
    add("/223/purchase/public/purchase/info/documents.html?regNumber="+reg);
    return candidates;
  }
  async function discoverFromOfficialCard(reg) {
    // New EIS cards reside under notice223; the old /223/purchase/... link
    // returns 404 for recently published notices.
    const landingPath="/epz/order/notice/notice223/common-info.html?regNumber="+reg;
    let landing=null;
    const problems=[];
    try{landing=await eisHtml(landingPath);}
    catch(e){
      if(e.backoff)throw e;
      problems.push("карточка "+String(e.message).slice(0,75));
    }
    const candidates=documentPageCandidates(reg,landing);
    for(const path of candidates){
      try{
        const page=await eisHtml(path);
        const docs=discoverDocs(page,reg);
        if(docs.length) {
          const actual=page.__eis223_source_url||SITE+path;
          const uri=new URL(actual,SITE);
          if(uri.origin!==SITE)throw Error("ЕИС перенаправила на другой домен");
          // Only actual official attachment pages, not an HTML error page.
          if(!["/epz/order/notice/notice223/documents.html",
                "/223/purchase/public/purchase/info/documents.html"].includes(uri.pathname))
            throw Error("ЕИС вернула не страницу документов");
          return {docs,source:uri.href,landing:landingPath};
        }
        problems.push(path.split("?")[0]+" — файлов 0");
      }catch(e){
        if(e.backoff)throw e;
        problems.push(String(e.message).slice(0,95));
      }
    }
    const reason="ЕИС: ссылки на оригиналы не найдены; "+
      problems.slice(0,5).join(" | ");
    throw new Error(reason.slice(0,260));
  }
  async function getKnown(numbers) {
    const path="/api/v299/eis223/known?regs="+encodeURIComponent(numbers.join(","));
    return (await api(path)).items || {};
  }

  async function fetchAndUploadOriginal(reg,item) {
    if (!active)return false;
    const u = new URL(item.url);
    if (u.origin !== SITE ||
        u.pathname !== "/223/filestore/public/1.0/download/fz223/file.html")
      return false;
    const uid=(u.searchParams.get("uid") || "").toUpperCase();
    if (!/^[0-9A-F]{32}$/.test(uid))return false;
    await wait(PAUSE_MS);
    const r=await request(u.href,{credentials:"same-origin",cache:"no-store"},60000);
    if (isRateLimited(r)) {
      throw Object.assign(new Error("ЕИС ограничила скачивание: "+r.status),
        {backoff:true});
    }
    if (!r.ok)throw new Error("Скачивание ЕИС HTTP "+r.status);
    const size=Number(r.headers.get("content-length") || "0");
    if (size>25*1024*1024)throw new Error("Оригинал превышает 25 МБ");
    const buffer=await r.arrayBuffer();
    if (buffer.byteLength<8 || buffer.byteLength>25*1024*1024)
      throw new Error("Недопустимый размер оригинала");
    const response=await api("/api/v295/eis223/original/"+reg+"/"+uid,"POST",buffer);
    if (!response.accepted)throw new Error("Railway не подтвердил файл");
    uploaded++;
    log(reg, item.name, "оригинал принят", buffer.byteLength,
        "байт; текст", response.extracted_chars);
    return true;
  }

  async function handleRegistry(reg,searchDoc) {
    const {docs,source,landing}=await discoverFromOfficialCard(reg);
    log(reg,"найдены официальные ссылки на документы:",docs.length,
        "страница:",source.split("?")[0]);
    await api("/api/v299/eis223/public-manifest","POST",{
      reg_number:reg,source_page:source,landing_page:SITE+landing,
      title:searchTitle(searchDoc,reg),customer:"",documents:docs
    });
    const list=await api("/api/v295/eis223/manifest/"+reg);
    let read=0;
    for(const item of list.documents || []) {
      if (!active || read>=MAX_FILES)break;
      if (item.received)continue;
      try{
        if(await fetchAndUploadOriginal(reg,item))read++;
      }catch(e){
        if(e.backoff)throw e;
        log(reg,item.name,"пропущен:",String(e.message).slice(0,130));
      }
    }
    if(read>0)admitted++;
    log(reg,"официальных ссылок:",docs.length,"оригиналов передано:",read,
      "; юридическая проверка запускается только при получении всех доступных файлов");
    if(!read)throw new Error("Документы обнаружены, но ни один оригинал не загружен");
    return true;
  }

  async function heartbeat(status,newPurchases,newOriginals) {
    try {
      await api("/api/v299/eis223/heartbeat","POST",{
        status,new_purchases:newPurchases,originals_uploaded:newOriginals
      });
    } catch(e) {log("Отчёт о цикле не отправлен:",e.message);}
  }
  async function run() {
    if(!active || busy)return;
    if(manualBusy || Date.now()<pausedUntil){
      timer=setTimeout(run,Math.max(30000,Math.min(300000,pausedUntil-Date.now())));
      return;
    }
    busy=true;
    let status="ok",added=0,downloadedStart=uploaded,delay=INTERVAL;
    cycles++;
    try {
      log("Цикл",cycles,": получаю первую страницу последних закупок 223-ФЗ");
      const search=await eisHtml(SEARCH);
      const numbers=registryNumbers(search);
      if (!numbers.length) {
        log("Номера закупок на странице ЕИС не обнаружены. Ничего не добавлено.");
        status="empty";
      } else {
        const known=await getKnown(numbers);
        const fresh=numbers.filter(n=>!known[n]?.complete);
        log("В выдаче",numbers.length,"номеров; ещё требуют загрузки",fresh.length);
        const picks=[];
        if(fresh.length) {
          for(let i=0;i<Math.min(MAX_BUYS,fresh.length);i++)
            picks.push(fresh[(rotatingIndex+i)%fresh.length]);
          rotatingIndex=(rotatingIndex+MAX_BUYS)%fresh.length;
        }
        for(const n of picks){
          if(!active)break;
          try{if(await handleRegistry(n,search))added++;}
          catch(e){
            if(e.backoff)throw e;
            log(n,"не удалось обработать:",String(e.message).slice(0,150));
          }
        }
      }
    } catch(e) {
      status=e.backoff?"rate_limited":"fetch_error";
      if(e.backoff)delay=BACKOFF;
      log("Ошибка цикла:",String(e.message).slice(0,170));
    } finally {
      busy=false;
      if(active){
        await heartbeat(status,added,uploaded-downloadedStart);
        log("Цикл окончен. Новых карточек:",added,"оригиналов:",
          uploaded-downloadedStart,"Следующий запрос через",
          Math.round(delay/60000),"мин.");
        timer=setTimeout(run,delay);
      }
    }
  }
  /* Manual searches are always first priority. Poll only Railway's queue,
   * not EIS, every 8s. EIS itself is queried only for a real new job. */
  async function pollManual() {
    if(!active || busy || manualBusy || Date.now()<pausedUntil)return;
    manualBusy=true;
    let job=null;
    let report=null;
    try{
      const next=await api("/api/v301/bridge/next");
      job=next.job;
      if(!job)return;
      log("Самостоятельная проверка:",job.kind,job.target,
          job.kind==="inn" ? "страница "+job.page : "");
      if(job.kind==="reg") {
        const already=await getKnown([job.target]);
        if(already[job.target]?.complete) {
          report={status:"done",detail:"Оригиналы уже сохранены; юридическая перепроверка поставлена в очередь",accepted:0,found:1};
        } else {
          const before=uploaded;
          const accepted=await handleRegistry(job.target,null);
          report={status:accepted?"done":"partial",
                  detail:accepted?"Оригиналы переданы на юридическую проверку":"ЕИС не предоставила доступные оригиналы",
                  accepted:uploaded-before,found:accepted?1:0};
        }
      } else if(job.kind==="inn") {
        const q="/epz/order/extendedsearch/results.html?"+
          new URLSearchParams({
            searchString:"",morphology:"on",
            openMode:"USE_DEFAULT_PARAMS",
            pageNumber:String(job.page),sortDirection:"false",
            recordsPerPage:"_20",showLotsInfoHidden:"false",
            fz223:"on",af:"on",ca:"on",pc:"on",pa:"on",
            customerInn:job.target,sortBy:"UPDATE_DATE",
            currencyIdGeneral:"-1"
          });
        const result=await eisHtml(q);
        const shown=result.querySelector('input[name="customerInn"]');
        if(shown && shown.value && shown.value.trim()!==job.target)
          throw Error("ЕИС вернула иной ИНН вместо запрошенного");
        const regs=registryNumbers(result);
        report={status:"done",found:regs.length,
                accepted:0,regs,more:regs.length>=20 && job.page<10,
                detail:regs.length?"Найдены номера закупок заказчика; загружаю оригиналы в приоритетном порядке":
                                  "По официальной выдаче ЕИС закупки по ИНН не найдены"};
        log("По ИНН",job.target,"найдено",regs.length,
            "закупок на странице",job.page);
      }
      if(!report)report={status:"error",detail:"Неизвестный вид задания"};
    }catch(e){
      if(e.backoff){
        pausedUntil=Date.now()+BACKOFF;
        report={status:"deferred",defer:1200,detail:"ЕИС ограничила запросы. Повтор через 20 минут"};
      }else{
        report={status:"partial",detail:String(e.message||e).slice(0,250)};
      }
      log("Самостоятельный поиск",job?.target||"",String(e.message||e).slice(0,170));
    }finally{
      if(job){
        try{
          await api("/api/v301/bridge/report/"+job.id,"POST",report||
            {status:"error",detail:"Загрузка прервана"});
        }catch(e){log("Отчёт о самостоятельном поиске не отправлен",e.message);}
      }
      manualBusy=false;
    }
  }
  manualTimer=setInterval(pollManual,8000);
  pollManual();
  log("Старт. Приоритетный поиск по номеру и ИНН опрашивает Railway каждые 8 секунд.");
  log("Фоновый поиск ЕИС: одна страница каждые 5 минут, до двух закупок.");
  log("Не закрывай вкладку; остановка: __eis223_five_minutes.stop()");
  await heartbeat("collector_started",0,0);
  run();
})();