/* ЕИС 223-ФЗ → Railway. Запускается из консоли на zakupki.gov.ru.
 * Без расширений. Только официальная поисковая страница ЕИС.
 * ОДНА выдача поиска за 5 минут; до ДВУХ закупок за цикл; документы последовательно.
 * Работает только пока этот браузер и эта страница открыты.
 */
(async function () {
  "use strict";
  const SITE = "https://zakupki.gov.ru";
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
    console.log("[ЕИС-5мин] Загрузчик уже запущен. Чтобы остановить: __eis223_five_minutes.stop()");
    return;
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
    const headers = {"x-eis223-upload-token":key};
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
    return new DOMParser().parseFromString(html,"text/html");
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
  function discoverDocs(doc,reg) {
    const documents = [];
    const seen = new Set();
    for (const a of doc.querySelectorAll("a[href]")) {
      let u;
      try {u = new URL(a.getAttribute("href"),SITE);} catch {continue;}
      if (u.origin !== SITE ||
          u.pathname !== "/223/filestore/public/1.0/download/fz223/file.html")
        continue;
      const uid = (u.searchParams.get("uid") || "").toUpperCase();
      if (!/^[0-9A-F]{32}$/.test(uid) || seen.has(uid))continue;
      let text = [
        a.getAttribute("download"),
        a.getAttribute("title"),
        a.textContent,
        a.closest("tr,li,.file")?.textContent
      ].filter(Boolean).map(s => String(s).replace(/\s+/g," ").trim());
      let name = "";
      for (const value of text) {
        let candidates = value.match(/[^\\/<>:"|?*]{1,220}\.(?:docx|doc|xlsx|xls|pdf|rtf|odt|ods|zip|xml)\b/ig) || [];
        if (candidates.length) {
          name = candidates[0].replace(/^(?:скачать|файл|открыть)[ :–-]*/i,"").trim();
          break;
        }
      }
      if (!EXT.test(name))continue;
      name = name.replace(/^.*[\\/]/,"").slice(0,200);
      seen.add(uid);
      documents.push({name,url:u.href});
      if (documents.length >= 70)break;
    }
    return documents;
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
    const source="/223/purchase/public/purchase/info/documents.html?regNumber="+reg;
    const page=await eisHtml(source);
    const docs=discoverDocs(page,reg);
    if (!docs.length) {
      log(reg,"на странице ЕИС не обнаружены ссылки на оригиналы. Не засчитываю.");
      return false;
    }
    await api("/api/v299/eis223/public-manifest","POST",{
      reg_number:reg,source_page:SITE+source,
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
    admitted++;
    log(reg,"метаданные приняты, новых оригиналов в цикле:",read,
      "; общая очередь юридической проверки запускается после получения всех доступных файлов");
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