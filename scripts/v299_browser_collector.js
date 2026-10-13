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
  function safeFilename(node) {
    // EIS displays many attachments as "Приложение №1 — ТЕХНИЧЕСКИЕ
    // ТРЕБОВАНИЯ" (no .pdf/.docx in the visible title).
    const values=[];
    let current=node;
    for(let depth=0;current&&depth<5;depth++,current=current.parentElement){
      for(const key of ["download","title","aria-label","data-file-name",
                        "data-filename","data-title","data-name"]){
        const value=current.getAttribute?.(key);
        if(value)values.push(String(value));
      }
      const txt=(current.textContent||"").replace(/\s+/g," ").trim();
      if(txt.length>2&&txt.length<900)values.push(txt);
      if(current.matches?.("tr,li,.file,.attachment,.document-item,.file-item,.file-download"))break;
    }
    const clean=value=>String(value||"").replace(/[\u0000-\u001f<>:"|?*\\/]/g," ")
      .replace(/(?:^|\s)(?:скачать|просмотреть|открыть|загрузить)(?:\s+файл)?(?=\s|$)/ig," ")
      .replace(/\s+/g," ").trim().slice(0,190);
    for(const text of values){
      const match=text.replace(/\u00a0/g," ").match(EXT_NAME);
      if(match){
        const name=clean(match[1]);
        if(EXT.test(name)&&name.length>4)return name;
      }
    }
    // Keep a real attachment label even when EIS omits the extension.
    // The extension will be derived from the official downloaded bytes,
    // never invented from the text or added to a counter prematurely.
    for(const text of values){
      const name=clean(text);
      if(name.length>=5&&!/^(?:файл|скачать|документ|загрузка|протоколы|прикрепленные файлы)$/i.test(name))
        return name;
    }
    return "";
  }
  function discoverDocs(doc,reg) {
    const documents=[],seen=new Set();
    const elements=doc.querySelectorAll(
      "a,button,[onclick],[data-href],[data-url],[data-file-uid],[data-download-url]");
    for(const element of elements){
      let uid=null;
      for(const key of ["href","onclick","data-href","data-url",
                        "data-download-url","data-file-url"]){
        uid=maybeFileUid(element.getAttribute?.(key));
        if(uid)break;
      }
      // Some EIS components store the download URL on a wrapper.
      let wrapper=element.parentElement;
      for(let j=0;!uid&&wrapper&&j<3;j++,wrapper=wrapper.parentElement){
        for(const key of ["href","onclick","data-href","data-url","data-download-url"]){
          uid=maybeFileUid(wrapper.getAttribute?.(key));
          if(uid)break;
        }
      }
      if(!uid||seen.has(uid))continue;
      const name=safeFilename(element);
      if(!name)continue;
      seen.add(uid);
      documents.push({name,url:SITE+FILE_PATH+"?uid="+uid,uid});
      if(documents.length>=100)break;
    }
    return documents;
  }
  function documentPageCandidates(reg,landing){
    const modern="/epz/order/notice/notice223/documents.html";
    const allowed=new Set([modern,"/223/purchase/public/purchase/info/documents.html"]);
    const candidates=[],seen=new Set();
    function add(raw) {
      try{
        const u=new URL(String(raw).replace(/&amp;/gi,"&"),
          SITE+"/epz/order/notice/notice223/common-info.html");
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
          if(raw.includes("documents.html?")){
            const m=raw.match(/(?:(?:https?:\/\/zakupki\.gov\.ru)?\/epz\/order\/notice\/notice223\/)?documents\.html\?[^"'\\\s<>)]*/i);
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

  function downloadUid(item) {
    const u=new URL(item.url,SITE);
    if(u.origin!==SITE||u.pathname!==FILE_PATH)
      throw new Error("Файл не принадлежит официальному хранилищу ЕИС");
    const uid=(u.searchParams.get("uid")||"").toUpperCase();
    if(!/^[0-9A-F]{32}$/.test(uid))throw new Error("Неверный UID файла ЕИС");
    return uid;
  }

  function sniffFileExtension(buffer,headers){
    const bytes=new Uint8Array(buffer);
    const mime=(headers.get("content-type")||"").split(";")[0].trim().toLowerCase();
    if(bytes.length<8)throw new Error("ЕИС вернула пустой файл");
    const prefix=new TextDecoder("latin1").decode(bytes.subarray(0,Math.min(80,bytes.length)));
    if(/^\s*<!doctype html|^\s*<html\b/i.test(prefix)||mime==="text/html")
      throw new Error("Вместо оригинала ЕИС вернула HTML");
    if(prefix.startsWith("%PDF"))return ".pdf";
    if(prefix.startsWith("{\\rtf"))return ".rtf";
    const header=new DataView(buffer,0,Math.min(8,buffer.byteLength));
    const ole=bytes.length>=8&&
      [0xD0,0xCF,0x11,0xE0,0xA1,0xB1,0x1A,0xE1].every((x,i)=>bytes[i]===x);
    if(ole){
      return mime.includes("excel")||mime.includes("spreadsheet")?".xls":".doc";
    }
    if(bytes[0]===0x50&&bytes[1]===0x4B){
      // ZIP central directory contains uncompressed filename entries even
      // when XML contents are compressed. Distinguish OOXML from .zip.
      const index=new TextDecoder("latin1").decode(
        bytes.subarray(Math.max(0,bytes.length-160000)));
      if(index.includes("word/document.xml"))return ".docx";
      if(index.includes("xl/workbook.xml"))return ".xlsx";
      if(index.includes("content.xml")&&
         (index.includes("mimetype")||mime.includes("opendocument")))
        return mime.includes("spreadsheet")?".ods":".odt";
      if(mime.includes("wordprocessingml"))return ".docx";
      if(mime.includes("spreadsheetml"))return ".xlsx";
      return ".zip";
    }
    if(/^\s*<\?xml|^\s*<[A-Za-z][\w:-]+[\s>]/.test(prefix)&&
       (mime.includes("xml")||prefix.trimStart().startsWith("<?xml")))
      return ".xml";
    throw new Error("Не удалось безопасно определить формат оригинала ЕИС");
  }

  function filenameFromHeader(headers){
    const disposition=headers.get("content-disposition")||"";
    const nameUtf=disposition.match(/filename\*\s*=\s*UTF-8''([^;]+)/i);
    const standard=disposition.match(/filename\s*=\s*"?([^";]+)"?/i);
    let filename="";
    try{
      filename=nameUtf?decodeURIComponent(nameUtf[1].trim()):(standard?.[1]||"");
    }catch(e){filename=standard?.[1]||"";}
    filename=filename.replace(/[\\/]/g," ").replace(/[<>:"|?*\u0000-\u001f]/g,"").trim();
    return filename.slice(0,190);
  }

  async function downloadOfficialFile(item){
    downloadUid(item);
    await wait(PAUSE_MS);
    const r=await request(item.url,{credentials:"same-origin",cache:"no-store"},60000);
    if(isRateLimited(r)){
      throw Object.assign(new Error("ЕИС ограничила скачивание: HTTP "+r.status),
        {backoff:true});
    }
    if(!r.ok)throw new Error("Скачивание ЕИС HTTP "+r.status);
    const size=Number(r.headers.get("content-length")||"0");
    if(size>25*1024*1024)throw new Error("Оригинал ЕИС превышает 25 МБ");
    const buffer=await r.arrayBuffer();
    if(buffer.byteLength<8||buffer.byteLength>25*1024*1024)
      throw new Error("Недопустимый размер оригинала ЕИС");
    const extension=sniffFileExtension(buffer,r.headers);
    return {buffer,extension,downloadName:filenameFromHeader(r.headers)};
  }

  async function uploadOriginalData(reg,item,buffer){
    const uid=downloadUid(item);
    const response=await api("/api/v295/eis223/original/"+reg+"/"+uid,"POST",buffer);
    if(!response.accepted)throw new Error("Railway не подтвердил файл");
    uploaded++;
    log(reg,item.name,"оригинал принят",buffer.byteLength,
        "байт; текст",response.extracted_chars);
    return true;
  }

  function preparedFileName(item,downloaded){
    const declared=String(item.name||"").trim();
    const fromHeader=downloaded.downloadName;
    const headerExt=fromHeader.match(EXT)?.[0]?.toLowerCase()||"";
    const detected=downloaded.extension;
    // Actual file signatures take precedence. A mislabeled ZIP cannot be
    // passed off as an audited DOCX/PDF.
    if(headerExt&&headerExt===detected)return fromHeader;
    let base=declared.replace(/\.(?:docx|doc|xlsx|xls|pdf|rtf|odt|ods|zip|xml)$/i,"")
      .replace(/[<>:"|?*\\/]/g," ").replace(/\s+/g," ").trim().slice(0,175);
    if(!base)base="Документ ЕИС "+downloadUid(item).slice(0,10);
    return base+detected;
  }
  async function handleRegistry(reg,searchDoc) {
    const {docs,source,landing}=await discoverFromOfficialCard(reg);
    const manifestBase={
      reg_number:reg,source_page:source,landing_page:SITE+landing,
      title:searchTitle(searchDoc,reg),customer:"",
      declared_attachment_count:docs.length
    };
    const knownExt=docs.filter(d=>EXT.test(d.name));
    const missingExt=docs.filter(d=>!EXT.test(d.name));
    log(reg,"ссылок на оригиналы:",docs.length,
        "из них без расширения в названии:",missingExt.length,
        "страница:",source.split("?")[0]);
    // Register all filenames known from the official HTML upfront.
    if(knownExt.length){
      await api("/api/v299/eis223/public-manifest","POST",{
        ...manifestBase,documents:knownExt.map(d=>({name:d.name,url:d.url}))
      });
    }
    const registered=await api("/api/v295/eis223/manifest/"+reg);
    const knownUid=new Map((registered.documents||[]).map(d=>[d.uid,d]));
    let uploadedNow=0,attempted=0,unresolved=0;

    for(const item of docs){
      if(!active||attempted>=MAX_FILES)break;
      const previous=knownUid.get(item.uid);
      if(previous?.received)continue;
      attempted++;
      try{
        const downloaded=await downloadOfficialFile(item);
        const detectedName=preparedFileName(item,downloaded);
        if(previous?.ext&&previous.ext!==downloaded.extension){
          throw new Error("Тип файла отличается от ранее зарегистрированного: "+
            previous.ext+" / "+downloaded.extension);
        }
        // Old EIS filenames often omit ".docx", ".pdf", etc. Register the
        // actual format only AFTER inspecting the official response bytes.
        if(!previous){
          await api("/api/v299/eis223/public-manifest","POST",{
            ...manifestBase,
            documents:[{name:detectedName,url:item.url}]
          });
        }
        if(await uploadOriginalData(reg,{...item,name:detectedName},downloaded.buffer))
          uploadedNow++;
      }catch(e){
        if(e.backoff)throw e;
        unresolved++;
        log(reg,item.name,"невозможно получить оригинал:",String(e.message).slice(0,170));
      }
    }
    const state=(await getKnown([reg]))[reg]||{};
    const complete=state.manifest_documents>=docs.length&&
      state.originals_received>=docs.length&&
      state.checked_documents>=docs.length;
    log(reg,"вложений на странице:",docs.length,
      "оригиналов на сервере:",state.originals_received||0,
      "новых оригиналов:",uploadedNow,
      "неудачных попыток:",unresolved,
      "полностью проверено:",complete);
    if(uploadedNow===0&&!complete)
      throw new Error("Новых оригиналов 0 из "+docs.length+
        "; неудачных попыток "+unresolved+". Проверка остаётся неполной.");
    return {discovered:docs.length,uploaded:uploadedNow,complete};
  }
  async function heartbeat(status,newPurchases,newOriginals,error="") {
    try {
      await api("/api/v299/eis223/heartbeat","POST",{
        status,new_purchases:newPurchases,originals_uploaded:newOriginals,
        error:String(error||"").slice(0,230)
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
    let status="ok",added=0,failures=0,lastError="",downloadedStart=uploaded,delay=INTERVAL;
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
          try{const result=await handleRegistry(n,search);if(result.uploaded>0)added++;}
          catch(e){
            if(e.backoff)throw e;
            failures++;
            lastError=String(e.message).slice(0,230);
            log(n,"не удалось обработать:",lastError);
          }
        }
      }
      if(added===0&&failures>0)status="no_downloadable_originals";
    } catch(e) {
      status=e.backoff?"rate_limited":"fetch_error";
      lastError=String(e.message).slice(0,230);
      if(e.backoff)delay=BACKOFF;
      log("Ошибка цикла:",lastError);
    } finally {
      busy=false;
      if(active){
        await heartbeat(status,added,uploaded-downloadedStart,lastError);
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
          const outcome=await handleRegistry(job.target,null);
          const accepted=outcome.uploaded>0;
          const updated=(await getKnown([job.target]))[job.target]||{};
          const fullyReceived=outcome.complete &&
            updated.manifest_documents>=outcome.discovered;
          report={
            status:fullyReceived?"done":"partial",
            detail:fullyReceived?
              "Все официальные оригиналы получены; юридическая проверка запускается":
              ("Получено "+(updated.originals_received||0)+" из "+
                 (updated.manifest_documents||0)+
                 " оригиналов; недостающие файлы остаются в приоритетной очереди"),
            accepted:uploaded-before,found:accepted?1:0
          };
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