/* ЕИС 223-ФЗ → Railway. Запускается из консоли на zakupki.gov.ru.
 * Без расширений. Только официальная поисковая страница ЕИС.
 * ОДНА выдача поиска за 5 минут; до ДВУХ закупок за цикл; документы последовательно.
 * Работает только пока этот браузер и эта страница открыты.
 */
(async function () {
  "use strict";
  const SITE = "https://zakupki.gov.ru";
  const COLLECTOR_VERSION = "341"; // Cyrillic labels, typed prices and legal dates
  const RAIL = "https://web-production-1c2ea.up.railway.app";
  const INTERVAL = 300000; // не чаще одного поиска за пять минут
  const BACKOFF = 1200000; // 20 мин при блокировке ЕИС
  const MAX_BUYS = 2;
  const MAX_FILES = 20;
  const PAUSE_MS = 2500;
  const NUMBER = /^3[0-9]{10}$/;
  const EXT = /\.(docx|doc|xlsx|xls|pdf|rtf|odt|ods|zip|rar|7z|tar|gz|tgz|bz2|xz|tbz2|txz|cab|xml)$/i;

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
    const onStop=()=>ctrl.abort();
    controller.signal.addEventListener("abort",onStop,{once:true});
    try {
      if(!active||controller.signal.aborted)throw Error("Загрузчик остановлен");
      const opt = {...options, signal:ctrl.signal};
      return await fetch(url, opt);
    } finally {
      clearTimeout(timeout);
      controller.signal.removeEventListener("abort",onStop);
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
    const block=searchResultCard(doc,reg);
    return titleFromDocument(block,reg);
  }
  // Only certified public EIS file references are accepted. Reconstruct a
  // canonical URL from the exact 32-hex UID found in the official HTML.
  const FILE_PATH="/223/filestore/public/1.0/download/fz223/file.html";
  const LEGACY_PATH="/223/purchase/public/download/download.html";
  const EXT_NAME=/([^\\/:<>?"|*\r\n]{2,180}\.(?:docx|doc|xlsx|xls|pdf|rtf|odt|ods|zip|rar|7z|tar|gz|tgz|bz2|xz|tbz2|txz|cab|xml))(?=$|[\s,'"<>;)]|$)/i;
  // Public 223-FZ EIS supports two verified attachment link schemes.
  // Other URLs, signatures and untrusted domains are never downloaded.
  const EIS_FILE_LINK=/(?:https?:\/\/zakupki\.gov\.ru)?\/223\/(?:filestore\/public\/1\.0\/download\/fz223\/file\.html|purchase\/public\/download\/download\.html)\?[^"'<>\\\s)]{1,420}/gi;
  function maybeOfficialFile(text) {
    if(!text)return null;
    const raw=String(text).replace(/&amp;/gi,"&").replace(/\\u0026/gi,"&");
    const matches=raw.match(EIS_FILE_LINK)||[];
    for(const href of matches){
      try{
        const u=new URL(href,SITE);
        if(u.origin!==SITE||u.username||u.password||u.hash)continue;
        if(u.pathname===FILE_PATH){
          const uid=(u.searchParams.get("uid")||"").toUpperCase();
          if(!/^[0-9A-F]{32}$/.test(uid))continue;
          return {uid,url:SITE+FILE_PATH+"?uid="+uid};
        }
        if(u.pathname===LEGACY_PATH){
          const id=u.searchParams.getAll("id");
          if(id.length!==1||[...u.searchParams.keys()].some(k=>k!=="id")||
              !/^[0-9]{1,15}$/.test(id[0])||BigInt(id[0])===0n)continue;
          // Prefix F1 denotes an INTERNAL numeric-id reference, never a
          // claim that EIS supplied a 32-hex filestore UID.
          const uid="F1"+BigInt(id[0]).toString(16).toUpperCase().padStart(30,"0");
          return {uid,url:SITE+LEGACY_PATH+"?id="+String(BigInt(id[0]))};
        }
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
      let official=null;
      for(const key of ["href","onclick","data-href","data-url",
                        "data-download-url","data-file-url"]){
        official=maybeOfficialFile(element.getAttribute?.(key));
        if(official)break;
      }
      // Some EIS components store the download URL on a wrapper.
      let wrapper=element.parentElement;
      for(let j=0;!official&&wrapper&&j<3;j++,wrapper=wrapper.parentElement){
        for(const key of ["href","onclick","data-href","data-url","data-download-url"]){
          official=maybeOfficialFile(wrapper.getAttribute?.(key));
          if(official)break;
        }
      }
      if(!official||seen.has(official.uid))continue;
      const name=safeFilename(element);
      if(!name)continue;
      seen.add(official.uid);
      documents.push({name,url:official.url,uid:official.uid});
      if(documents.length>=100)break;
    }
    return documents;
  }
  const INFO_PAGES=new Set([
    "/223/purchase/public/purchase/info/common-info.html",
    "/223/purchase/public/purchase/info/commoninfo.html",
    "/epz/order/notice/notice223/common-info.html"
  ]);
  const DOC_PAGES=new Set([
    "/223/purchase/public/purchase/info/documents.html",
    "/epz/order/notice/notice223/documents.html"
  ]);
  const NOTICE_KEYS=["noticeId","noticeInfoId"];
  function officialNoticeId(url) {
    const entries=NOTICE_KEYS.flatMap(k=>url.searchParams.getAll(k).map(value=>({key:k,value})));
    if(entries.length!==1 || !/^[0-9]{1,15}$/.test(entries[0].value))return null;
    return entries[0];
  }
  function officialCardUrl(raw,reg,validPaths,associatedNoticeId=false,base=SITE){
    try{
      const u=new URL(String(raw||"").replace(/&amp;/gi,"&"),base);
      if(u.origin!==SITE||u.username||u.password||u.hash||!validPaths.has(u.pathname))
        return null;
      const regNums=[...u.searchParams.getAll("regNumber"),
                     ...u.searchParams.getAll("purchaseNoticeNumber")];
      if(regNums.length && (regNums.length!==1 || regNums[0]!==reg))return null;
      if(regNums.length===1)return u;
      // Real 223-FZ notice cards commonly use noticeId, not regNumber.
      // Numeric IDs are accepted only when the EIS search result / opened
      // card visibly contains the exact procurement registry number.
      if(associatedNoticeId&&officialNoticeId(u))return u;
      return null;
    }catch(e){return null}
  }
  function actualCardLinks(doc,reg){
    const output=[],seen=new Set();
    if(!doc)return output;
    const exact=new RegExp("(?:^|\\D)"+reg+"(?:\\D|$)");
    const docBase=doc.__eis223_source_url||location.href;
    for(const element of doc.querySelectorAll("a[href],[onclick],[data-href],[data-url]")){
      const block=element.closest?.(
        ".search-registry-entry-block,.registry-entry,article,tr,.registry-entry__form");
      const context=String(block?.textContent||element.textContent||"");
      const correlated=exact.test(context);
      for(const key of ["href","onclick","data-href","data-url"]){
        const raw=element.getAttribute(key)||"";
        let found=null;
        if(key==="href")found=officialCardUrl(raw,reg,INFO_PAGES,correlated,docBase);
        if(!found){
          const match=raw.match(/(?:https?:\/\/zakupki\.gov\.ru)?\/(?:223\/purchase\/public\/purchase\/info|epz\/order\/notice\/notice223)\/common-?info\.html\?[^"'<>\s)]+/i);
          if(match)found=officialCardUrl(match[0],reg,INFO_PAGES,correlated,docBase);
        }
        if(found&&!seen.has(found.href)){seen.add(found.href);output.push(found.href);}
      }
    }
    // EIS search markup may put the registry number and the noticeId link
    // in different descendants of the same result card.
    const card=searchResultCard(doc,reg);
    if(card){
      for(const element of card.querySelectorAll("a[href],[onclick],[data-href],[data-url]")){
        for(const key of ["href","onclick","data-href","data-url"]){
          const raw=element.getAttribute(key)||"";
          const match=raw.match(/(?:https?:\/\/zakupki\.gov\.ru)?\/(?:223\/purchase\/public\/purchase\/info|epz\/order\/notice\/notice223)\/common-?info\.html\?[^"'<>\s)]+/i);
          const found=officialCardUrl(match?match[0]:raw,reg,INFO_PAGES,true,docBase);
          if(found&&!seen.has(found.href)){seen.add(found.href);output.push(found.href);}
        }
      }
    }
    // Some EIS results link only to the documents tab, or store the URL in
    // a data attribute. Convert a VERIFIED noticeId to the common-info tab.
    // Never derive a card from regNumber alone (that route returns 404).
    const exactCard=searchResultCard(doc,reg);
    if(exactCard && new RegExp("(?:^|\\D)"+reg+"(?:\\D|$)").test(exactCard.textContent||"")){
      for(const element of exactCard.querySelectorAll("a[href],[onclick],[data-href],[data-url]")){
        for(const key of ["href","onclick","data-href","data-url"]){
          const raw=element.getAttribute(key)||"";
          const match=raw.match(/(?:https?:\/\/zakupki\.gov\.ru)?\/(?:223\/purchase\/public\/purchase\/info|epz\/order\/notice\/notice223)\/(?:documents|common-?info)\.html\?[^"'<>\s)]+/i);
          if(!match)continue;
          try{
            const candidate=new URL(match[0].replace(/&amp;/gi,"&"),docBase);
            if(candidate.origin!==SITE||!officialNoticeId(candidate))continue;
            candidate.pathname=candidate.pathname.replace(/\/(?:documents|commoninfo)\.html$/,"/common-info.html");
            const verified=officialCardUrl(candidate.href,reg,INFO_PAGES,true);
            if(verified&&!seen.has(verified.href)){
              seen.add(verified.href);output.push(verified.href);
            }
          }catch(e){}
        }
      }
    }
    return output;
  }
  function valueByLabel(root,patterns){
    if(!root)return "";
    const labeled=root.querySelectorAll(".common-text__title,.section__title,"+
      ".registry-entry__body-title,.registry-entry__body-item-title,.price-block__title,.data-block__title,dt,label,th,"+
      ".section__attrib,.attachment__text,.blockInfo__label,.common-text__label,"+
      "td,.title");
    for(const label of labeled){
      const title=(label.textContent||"").replace(/\s+/g," ").trim()
        .replace(/\s*:\s*$/,"").toLowerCase();
      if(title.length>180)continue;
      // EIS sometimes renders "ИНН: 1234567890" in the SAME node.
      const inline=(label.textContent||"").replace(/\s+/g," ").trim();
      const inlineMatch=inline.match(/^([^:：]{1,110})\s*[:：]\s*(.+)$/);
      if(!patterns.some(p=>p.test(title))&&
          !(inlineMatch&&patterns.some(p=>p.test(inlineMatch[1].trim().toLowerCase()))))continue;
      if(inlineMatch&&patterns.some(p=>p.test(inlineMatch[1].trim().toLowerCase()))){
        const v=inlineMatch[2].trim();
        if(v&&v.length<=800)return v;
      }
      const values=".common-text__value,.section__info,.registry-entry__body-value,"+
        ".registry-entry__body-item-value,.registry-entry__body-href,"+
        ".blockInfo__value,.price-block__value,.data-block__value,dd,td,.section__value,.value";
      const candidates=[label.nextElementSibling,
        ...label.parentElement?.querySelectorAll(values)||[]];
      for(const value of candidates){
        if(!value||value===label||value.contains(label)||label.contains(value))continue;
        const name=(value.textContent||"").replace(/\s+/g," ").trim();
        if(name&&name.toLowerCase()!==title&&name.length<=800)return name;
      }
    }
    return "";
  }
  function customerFromDocument(doc,reg){
    if(!doc)return {name:"",inn:""};
    let name="",inn="";
    // EIS pages may show BOTH buyer and procurement organiser. The organiser
    // is not necessarily the buyer (notably procurement 32616280442).
    for(const section of doc.querySelectorAll(
      "section,.common-text,.row,.blockInfo,.blockInfo__section,.section,fieldset")){
      const caption=(section.querySelector(".common-text__caption,.blockInfo__title,"+
        ".section__caption,.section__header,h2,h3,legend")?.textContent||"")
        .replace(/\s+/g," ").trim().toLowerCase();
      if(!/^(?:сведения о заказчике|информация о заказчике|заказчик|заказчики по лоту)(?:\s|$)/i.test(caption))
        continue;
      name=valueByLabel(section,[/^наименование организации/i,/^полное наименование/i,
             /^заказчик$/i,/^наименование заказчика/i])||name;
      inn=valueByLabel(section,[/^инн$/i,/^инн заказчика/i])||inn;
      if(!inn){
        const hits=(section.textContent||"").match(/(?:^|[^а-яёa-z])ИНН\s*[:：№]?\s*([0-9]{10}|[0-9]{12})(?![0-9])/gi)||[];
        if(hits.length===1)inn=(hits[0].match(/([0-9]{10}|[0-9]{12})/)||[])[1]||"";
      }
      if(name&&inn)break;
    }
    if(!name){
      name=valueByLabel(doc,[/^наименование заказчика/i,/^заказчик$/i]);
    }
    if(!inn){
      inn=valueByLabel(doc,[/^инн заказчика/i]);
    }
    // Some EIS templates put the buyer's INN in a nearby plain-text field.
    // Only use the unique INN inside an explicitly buyer-labelled section.
    if(!inn){
      const sections=[...doc.querySelectorAll("section,.blockInfo,.blockInfo__section,fieldset")];
      for(const section of sections){
        const heading=(section.querySelector("h2,h3,legend,.blockInfo__title,.section__header")?.textContent||"").trim();
        if(!/^(?:сведения|информация) о заказчике|^заказчик(?:и по лоту)?$/i.test(heading))continue;
        const matches=[...section.textContent.matchAll(/(?:^|[^а-яёa-z])ИНН\\s*[:：№]?\\s*([0-9]{10}|[0-9]{12})(?![0-9])/gi)];
        const unique=[...new Set(matches.map(m=>m[1]))];
        if(unique.length===1){inn=unique[0];break;}
      }
    }
    name=name.replace(/\s+/g," ").trim().slice(0,400);
    if(/^(?:уточняем|заказчик уточняется|не указан|не найден|сведения отсутствуют)/i.test(name))
      name="";
    if(!/[А-Яа-яЁёA-Za-z]/.test(name))name="";
    const match=String(inn).replace(/[^0-9]/g,"").match(/^(?:[0-9]{10}|[0-9]{12})$/);
    return {name,inn:match?match[0]:""};
  }
  function searchResultCard(doc,reg){
    if(!doc)return null;
    const exact=new RegExp("(?:^|\\D)"+reg+"(?:\\D|$)");
    // Modern results link by numeric noticeId. Correlate the visible registry
    // number inside ONE result block rather than requiring regNumber in href.
    for(const block of doc.querySelectorAll(
      ".search-registry-entry-block,.registry-entry,article,.registry-entry__form")){
      if(exact.test(block.textContent||""))return block;
    }
    for(const a of doc.querySelectorAll("a[href]")){
      const href=a.getAttribute("href")||"";
      if(!href.includes("regNumber="+reg)&&!exact.test(a.textContent||""))continue;
      return a.closest(".search-registry-entry-block,.registry-entry,article,tr,"+
        ".registry-entry__form,.search-registry-entry-block__form");
    }
    return null;
  }
  function verifiedSearchCustomer(doc,reg){
    const card=searchResultCard(doc,reg);
    return card?customerFromDocument(card,reg):{name:"",inn:""};
  }
  function titleFromDocument(doc,reg){
    if(!doc)return "";
    const title=valueByLabel(doc,[/^наименование закупки$/i,/^предмет закупки$/i,
                                  /^объект закупки$/i,/^наименование объекта закупки$/i]);
    if(title&&title.length>9&&!/^(?:подача заявок|извещение)/i.test(title))
      return title.slice(0,800);
    return "";
  }
  function documentPageCandidates(reg,landing){
    // Use real links and numeric notice identifiers from an EIS card.
    // Never fabricate documents.html?regNumber=... for 223-FZ.
    const candidates=[],seen=new Set();
    if(!landing)return candidates;
    const origin=landing.__eis223_source_url||"";
    const verifiedCard=officialCardUrl(origin,reg,INFO_PAGES,true);
    function add(raw){
      let url;
      try{url=new URL(String(raw).replace(/&amp;/gi,"&"),origin||SITE)}
      catch(e){return}
      if(url.origin!==SITE||url.username||url.password||url.hash ||
         !DOC_PAGES.has(url.pathname)||seen.has(url.href))return;
      const nums=[...url.searchParams.getAll("regNumber"),
                  ...url.searchParams.getAll("purchaseNoticeNumber")];
      if(nums.length && (nums.length!==1||nums[0]!==reg))return;
      const claim=officialNoticeId(url);
      const trusted=verifiedCard&&officialNoticeId(verifiedCard);
      if(trusted){
        // A validated noticeId/noticeInfoId must be preserved across tabs.
        // Even if EIS renders an older regNumber-only documents link, do not
        // follow it: it caused repeated false 404s.
        if(!claim||claim.key!==trusted.key||claim.value!==trusted.value)return;
      }else if(claim){
        // A verified regNumber-based official card may link to a noticeId
        // documents tab. Accept this explicit card link, never fabricate a
        // documents.html?regNumber URL (which produces EIS 404).
        const exact=new RegExp("(?:^|\\D)"+reg+"(?:\\D|$)");
        if(!verifiedCard||!exact.test(landing.body?.textContent||""))return;
      }
      if(!nums.length&&!claim)return;
      seen.add(url.href);candidates.push(url.href);
    }
    for(const el of landing.querySelectorAll("a[href],a[onclick],[data-href],[data-url]")){
      for(const field of ["href","onclick","data-href","data-url"]){
        const raw=el.getAttribute(field)||"";
        if(!raw.includes("documents.html"))continue;
        const m=raw.match(/(?:(?:https?:\/\/zakupki\.gov\.ru)?\/(?:223\/purchase\/public\/purchase\/info|epz\/order\/notice\/notice223)\/)?documents\.html\?[^"'<>\s)]+/i);
        if(m)add(m[0]);
      }
    }
    // Navigation links are sometimes absent from EIS server-side HTML,
    // but the validated common-info noticeId is sufficient to identify
    // the corresponding official documents tab.
    if(verifiedCard&&officialNoticeId(verifiedCard)){
      const sibling=new URL("documents.html",verifiedCard.href);
      sibling.search=verifiedCard.search;
      add(sibling.href);
    }
    return candidates;
  }
  // Never infer the contract's initial price from its award, unit cost,
  // spreadsheet page number or a different lot. Use labeled EIS-card fields.
  function cardPricesFromDocument(doc,reg){
    if(!doc||!new RegExp("(?:^|\\D)"+reg+"(?:\\D|$)").test(
        doc.body?.textContent||doc.textContent||""))return [];
    const currency=valueByLabel(doc,[/^валюта(?:\s+договора|\s+цены)?$/i]);
    if(currency&&!/^(?:RUB|RUR|643|₽|российск[а-яё]*\s+рубл[а-яё]*|рубл[а-яё]*)$/i.test(currency.trim()))
      return [];
    const groups=[
      // JavaScript \w excludes Cyrillic, including with the /u flag.
      {kind:"nmcd",pattern:/^(?:начальная\s*\(\s*максимальная\s*\)\s*цена(?:\s+договора|\s+контракта)?|начальная\s+максимальная\s+цена(?:\s+договора|\s+контракта)?|начальная\s+цена\s+(?:договора|контракта|лота)|нмц[дк])(?:\s*\(цена\s+лота\))?$/i},
      {kind:"maximum_contract_value",pattern:/^максимальн[а-яё]*\s+(?:значени[а-яё]*\s+)?цен[а-яё]*\s+договора$/i},
      {kind:"subject_cost",pattern:/^(?:стоимост[а-яё]*\s+(?:предмет[а-яё]*\s+)?закупк[а-яё]*|стоимост[а-яё]*\s+предмет[а-яё]*\s+договора|сумма\s+закупк[а-яё]*)$/i},
      {kind:"contract_price",pattern:/^(?:(?:общая\s+)?(?:цена|стоимость|сумма)\s+договора|цена\s+контракта)$/i},
      {kind:"lot_price",pattern:/^(?:цена|стоимость)\s+лота$/i},
      {kind:"total_unit_prices",pattern:/^(?:начальная\s*(?:\(\s*максимальная\s*\))?\s*)?сумма\s+цен\s+(?:всех\s+)?единиц(?:\s+(?:товар(?:а|ов)?|работ|услуг)(?:[,\s]+(?:работ|услуг))*)?$/i},
      {kind:"unit_price",pattern:/^(?:начальная\s*(?:\(\s*максимальная\s*\))?\s*)?(?:цена|стоимость)\s+(?:за\s+единицу|единицы)(?:\s+(?:товара|работы|услуги)(?:[,\s]+(?:работы|услуги))*)?$/i}
    ];
    const amount=raw=>{
      const clean=String(raw||"").replace(/[\u00a0\u202f]/g," ")
        .replace(/\s+/g," ").trim()
        .replace(/\s*(?:Российский рубль|Российских рублей)\s*$/i," ₽");
      // Text and table-cell layouts used by EIS may spell out roubles
      // and kopecks, or put a written-number parenthesis between them.
      const m=clean.match(/^(\d[\d ]{0,18}(?:[.,]\d{1,2})?)(?:\s*\([^)]{1,180}\))?\s*(?:₽|руб(?:ль|ля|лей|\.)?|RUB)?(?:\s*(\d{1,2})\s*коп(?:еек|ейки|ейка|\.))?$/i);
      if(!m)return "";
      const number=Number(m[1].replace(/\s/g,"").replace(",","."));
      if(!Number.isFinite(number)||number<0||number>1e15)return "";
      const final=number+(m[2]&&!/[.,]/.test(m[1])?Number(m[2])/100:0);
      if(!Number.isFinite(final)||final>1e15)return "";
      return final.toFixed(2);
    };
    const selectors=".common-text__title,.section__title,.registry-entry__body-title,"+
      ".registry-entry__body-item-title,.price-block__title,.data-block__title,"+
      ".blockInfo__label,.common-text__label,.section__attrib,.title,dt,label,th,td";
    const all=[];
    const labelText=s=>s.replace(/[:：]\s*$/,"").trim()
      .replace(/\s*\((?:руб\.?|рублей|RUB|с\s+НДС|без\s+НДС)\)\s*$/i,"").trim();
    for(const group of groups){
      const candidates=[];
      for(const node of doc.querySelectorAll(selectors)){
        const raw=(node.textContent||"").replace(/\s+/g," ").trim();
        if(raw.length>200)continue;
        const inline=raw.match(/^([^:：]{1,100})\s*[:：]\s*(.+)$/);
        const label=labelText(inline?inline[1]:raw);
        if(!group.pattern.test(label))continue;
        const hits=[];
        if(inline)hits.push(inline[2]);
        for(const n of [node.nextElementSibling,...(node.parentElement?.querySelectorAll(
          ".common-text__value,.section__info,.registry-entry__body-value,"+
          ".registry-entry__body-item-value,.blockInfo__value,.price-block__value,"+
          ".data-block__value,dd,td,.section__value,.value")||[])]){
          if(n&&n!==node&&!n.contains(node)&&!node.contains(n)){
            const t=(n.textContent||"").replace(/\s+/g," ").trim();
            if(t.length<=120)hits.push(t);
          }
        }
        // Nested labels sometimes contain the sole value as a sibling text node.
        if(!hits.length){
          const t=(node.parentElement?.textContent||"").replace(/\s+/g," ").trim();
          if(t.startsWith(raw)&&t.length<140)hits.push(t.slice(raw.length).trim().replace(/^[:：-]/,""));
        }
        for(const rawValue of hits){
          const value=amount(rawValue);
          if(value){candidates.push({price:value,price_kind:group.kind,price_label:label});break}
        }
      }
      // A sole explicit label with one unambiguous value may appear in EIS
      // plain-text mobile layouts (on a separate line, not a DOM <value>).
      if(!candidates.length){
        const lines=(doc.body?.innerText||"").split(/\n/).map(s=>s.trim()).filter(Boolean);
        for(let j=0;j<lines.length;j++){
          const inline=lines[j].match(/^([^:：]{1,100})\s*[:：]\s*(.+)$/);
          const label=labelText(inline?inline[1]:lines[j]);
          if(!group.pattern.test(label))continue;
          const val=amount(inline?inline[2]:(lines[j+1]||""));
          if(val)candidates.push({price:val,price_kind:group.kind,price_label:label});
          if(candidates.length>12)break;
        }
      }
      const unique=new Map(candidates.map(v=>[v.price,v]));
      all.push(...unique.values());
    }
    return all.slice(0,50);
  }
  function cardPriceFromDocument(doc,reg){
    const prices=cardPricesFromDocument(doc,reg);
    if(!prices.length)return null;
    const first=prices.filter(x=>x.price_kind===prices[0].price_kind);
    return first.length===1?first[0]:null;
  }
  function cardFactsFromDocument(doc,reg){
    const exact=new RegExp("(?:^|\\D)"+reg+"(?:\\D|$)");
    if(!exact.test(doc.body?.textContent||doc.textContent||""))return null;
    const fields={},field_evidence={};
    const patterns={
      method:/^(?:наименование\s+способа\s+закупки|способ(?:\s+осуществления|\s+проведения|\s+размещения)?\s+закупки|форма\s+торгов)$/i,
      submission_start:/^(?:дата\s+(?:и\s+время\s+)?начала\s+(?:срока\s+)?(?:подачи|при[её]ма)\s+заявок|начало\s+(?:срока\s+)?(?:подачи|при[её]ма)\s+заявок)$/i,
      submission_end:/^(?:дата\s+(?:и\s+время\s+)?окончания\s+(?:срока\s+)?(?:подачи|при[её]ма)\s+заявок|окончание\s+(?:срока\s+)?(?:подачи|при[её]ма)\s+заявок|срок\s+окончания\s+подачи\s+заявок)$/i,
      published:/^(?:дата\s+(?:и\s+время\s+)?(?:первоначального\s+)?(?:размещения|публикации)\s+извещения(?:\s+в\s+ЕИС)?|дата\s+публикации\s+закупки)$/i
    };
    for(const [key,pattern] of Object.entries(patterns)){
      const value=valueByLabel(doc,[pattern]);
      if(!value)continue;
      if(key==='method'){
        if(!/(?:конкурс|аукцион|запрос\s+(?:котировок|предложений)|единственного\s+поставщика)/i.test(value))continue;
        fields[key]=value.slice(0,220);
      }else{
        const d=value.match(/20\d{2}-\d{2}-\d{2}|\d{1,2}\.\d{1,2}\.20\d{2}/);
        if(!d)continue;
        const a=d[0].split('.');
        fields[key]=a.length===3?a[2]+'-'+a[1].padStart(2,'0')+'-'+a[0].padStart(2,'0'):d[0];
      }
      field_evidence[key]=key+': '+value.slice(0,650);
    }
    const sme=valueByLabel(doc,[/^участниками\s+закупки\s+могут\s+быть\s+только\s+субъекты\s+малого\s+и\s+среднего\s+предпринимательства$/i]);
    if(/^(?:да|нет)$/i.test(sme.trim())){
      fields.sme_only=/^да$/i.test(sme.trim());
      field_evidence.sme_only='Участниками закупки могут быть только субъекты малого и среднего предпринимательства: '+sme;
    }
    const price_formula=valueByLabel(doc,[/^формула\s+цены(?:\s+договора)?$/i]);
    return {prices:cardPricesFromDocument(doc,reg),fields,field_evidence,price_formula};
  }
  async function reportOfficialPrice(reg,source,doc){
    const item=cardFactsFromDocument(doc,reg);
    if(!item||(!item.prices.length&&!Object.keys(item.fields).length&&!item.price_formula))return;
    try{
      const result=await api("/api/v341/eis223/card-facts","POST",{
        reg_number:reg,source_page:source,...item
      });
      if(result.accepted)log(reg,"Поля карточки ЕИС сохранены:",
                           item.prices.length,"видов цены;",Object.keys(item.fields).length,"полей проверки");
    }catch(e){
      log(reg,"не удалось сохранить цену ЕИС:",String(e.message).slice(0,110));
    }
  }
  async function reportOfficialBuyer(reg,source,customer,title){
    // Buyer's verified official card metadata must not depend on document
    // availability. This call is intentionally separate from original upload.
    if(!customer?.name)return;
    try{
      const result=await api("/api/v311/eis223/customer","POST",{
        reg_number:reg,source_page:source,
        customer:customer?.name||"",customer_inn:customer?.inn||"",
        title:title||""
      });
      if(result.accepted)
        log(reg,"заказчик установлен из ЕИС:",(customer.name||"").slice(0,95));
    }catch(e){
      log(reg,"сведения о заказчике пока не сохранены:",String(e.message).slice(0,100));
    }
  }
  async function discoverFromOfficialCard(reg,searchDoc,metadataOnly=false){
    const problems=[];
    const searchCustomer=verifiedSearchCustomer(searchDoc,reg);
    const actualUrl=new URL(location.href);
    let liveUrl=officialCardUrl(location.href,reg,new Set([...INFO_PAGES,...DOC_PAGES]),
      new RegExp("(?:^|\\D)"+reg+"(?:\\D|$)").test(document.body?.textContent||""));
    let liveLanding=liveUrl?.href||"";
    if(liveUrl&&DOC_PAGES.has(liveUrl.pathname)&&officialNoticeId(liveUrl)){
      // A visible EIS documents tab is trusted when its page itself names
      // this exact procurement. The provenance landing is its sibling
      // common-info card with the SAME verified noticeId, not a docs page.
      const common=new URL("common-info.html",liveUrl.href);
      common.search=liveUrl.search;
      liveLanding=common.href;
    }
    if(!liveUrl&&actualUrl.origin===SITE&&DOC_PAGES.has(actualUrl.pathname)&&
        officialNoticeId(actualUrl)&&
        new RegExp("(?:^|\\D)"+reg+"(?:\\D|$)").test(document.body?.textContent||"")){
      const actualLinks=actualCardLinks(document,reg);
      if(actualLinks.length){
        liveUrl=actualUrl;
        liveLanding=actualLinks[0];
      }
    }
    if(liveUrl){
      const docs=discoverDocs(document,reg);
      if(docs.length){
        log(reg,"найдено на открытой официальной странице ЕИС:",docs.length);
        const meta=customerFromDocument(document,reg);
        const customer={name:meta.name||searchCustomer.name,inn:meta.inn||searchCustomer.inn};
        const title=titleFromDocument(document,reg)||searchTitle(searchDoc,reg);
        await reportOfficialBuyer(reg,liveLanding||liveUrl.href,customer,title);
        await reportOfficialPrice(reg,liveUrl.href,document);
        if(metadataOnly&&INFO_PAGES.has(liveUrl.pathname))
          return {docs:[],source:liveUrl.href,landing:liveLanding||liveUrl.href,customer,title};
        return {docs,source:liveUrl.href,landing:liveLanding||liveUrl.href,
                customer,title};
      }
    }
    // Prefer the actual common-info link from the search result. A 223-FZ
    // notice is NOT a 44-FZ notice223 page merely because the search screen
    // displays it; dynamically read the official card route instead.
    let verifiedSearchDoc=searchDoc;
    let validatedSearchLinks=actualCardLinks(verifiedSearchDoc,reg);
    let directLanding=null;
    if(!validatedSearchLinks.length){
      // Older closed notices may be outside the default public-search window.
      // Verify the *actual returned card* before trusting its regNumber route.
      const directPaths=[
        "/epz/order/notice/notice223/common-info.html?regNumber="+encodeURIComponent(reg),
        "/223/purchase/public/purchase/info/common-info.html?regNumber="+encodeURIComponent(reg)
      ];
      for(const directPath of directPaths){
        try{
          const page=await eisHtml(directPath);
          const url=officialCardUrl(page.__eis223_source_url||SITE+directPath,
            reg,INFO_PAGES,true);
          const exact=new RegExp("(?:^|\\D)"+reg+"(?:\\D|$)");
          if(url && exact.test(page.body?.textContent||"")){
            validatedSearchLinks=[url.href];
            directLanding=page;
            log(reg,"проверена архивная карточка ЕИС:",url.pathname);
            break;
          }
          problems.push("Карточка ЕИС не подтвердила точный номер");
        }catch(e){
          if(e.backoff)throw e;
          problems.push("Архивная карточка ЕИС: "+String(e.message).slice(0,100));
        }
      }
    }
    if(!validatedSearchLinks.length){
      // Manual jobs have no search-result page. Obtain an actual noticeId
      // by searching the EIS registry number, rather than guessing URLs.
      const targeted="/epz/order/extendedsearch/results.html?"+
        new URLSearchParams({searchString:reg,morphology:"off",fz223:"on",
          sortBy:"UPDATE_DATE",sortDirection:"false",
          pageNumber:"1",recordsPerPage:"_20",af:"on"});
      try{
        verifiedSearchDoc=await eisHtml(targeted);
        validatedSearchLinks=actualCardLinks(verifiedSearchDoc,reg);
        if(validatedSearchLinks.length)
          log(reg,"найдена действительная ссылка карточки ЕИС");
      }catch(e){
        if(e.backoff)throw e;
        problems.push("поиск ЕИС: "+String(e.message).slice(0,80));
      }
    }
    if(!validatedSearchLinks.length){
      throw new Error("Не найдена подтверждённая карточка ЕИС по "+reg+
        ". Проверены архивная карточка и поиск: "+
        problems.slice(-3).join(" | ").slice(0,140));
    }
    const verifiedLinkSet=new Set(validatedSearchLinks);
    const urls=validatedSearchLinks;
    const seen=new Set();
    for(const raw of urls){
      if(seen.has(raw))continue;seen.add(raw);
      const url=officialCardUrl(raw,reg,INFO_PAGES,verifiedLinkSet.has(raw));
      if(!url)continue;
      let landing;
      try{
        landing=directLanding&&directLanding.__eis223_source_url===url.href
          ? directLanding : await eisHtml(url.pathname+url.search);
      }
      catch(e){
        if(e.backoff)throw e;
        problems.push(url.pathname+": "+String(e.message).slice(0,75));
        continue;
      }
      let customer=customerFromDocument(landing,reg);
      customer={name:customer.name||searchCustomer.name,inn:customer.inn||searchCustomer.inn};
      const title=titleFromDocument(landing,reg)||searchTitle(searchDoc,reg);
      // Save the buyer NOW even if every document tab below returns HTTP 404.
      await reportOfficialBuyer(reg,url.href,customer,title);
      await reportOfficialPrice(reg,url.href,landing);
      if(metadataOnly)return {docs:[],source:url.href,landing:url.href,customer,title};
      const onCard=discoverDocs(landing,reg);
      if(onCard.length)return {docs:onCard,source:url.href,landing:url.href,customer,title};
      for(const target of documentPageCandidates(reg,landing)){
        try{
          const u=new URL(target);
          const page=await eisHtml(u.pathname+u.search);
          const docs=discoverDocs(page,reg);
          if(docs.length){
            const redirected=new URL(page.__eis223_source_url||target,SITE);
            if(redirected.origin!==SITE||!DOC_PAGES.has(redirected.pathname))
              throw Error("ЕИС вернула не страницу документов");
            const details=customerFromDocument(page,reg);
            if(!customer.name)customer=details;
            if(!customer.name)customer=searchCustomer;
            await reportOfficialPrice(reg,redirected.href,page);
            return {docs,source:redirected.href,landing:url.href,customer,title};
          }
          problems.push(u.pathname+" — на странице отсутствуют файлы");
        }catch(e){
          if(e.backoff)throw e;
          problems.push("ссылка вкладки ЕИС: "+String(e.message).slice(0,85));
        }
      }
    }
    throw new Error("ЕИС не предоставила страницу с оригиналами ("+
      problems.slice(0,3).join(" | ").slice(0,220)+")");
  }
  async function getKnown(numbers) {
    const path="/api/v299/eis223/known?regs="+encodeURIComponent(numbers.join(","));
    return (await api(path)).items || {};
  }

  function downloadUid(item) {
    const match=maybeOfficialFile(item.url);
    if(!match||match.url!==item.url)
      throw new Error("Недопустимая ссылка на оригинал ЕИС");
    return match.uid;
  }

  function sniffFileExtension(buffer,headers){
    const bytes=new Uint8Array(buffer);
    const mime=(headers.get("content-type")||"").split(";")[0].trim().toLowerCase();
    if(bytes.length<8)throw new Error("ЕИС вернула пустой файл");
    const prefix=new TextDecoder("latin1").decode(bytes.subarray(0,Math.min(80,bytes.length)));
    if(/^\s*<!doctype html|^\s*<html\b/i.test(prefix)||mime==="text/html")
      throw new Error("Вместо оригинала ЕИС вернула HTML");
    if(prefix.startsWith("%PDF"))return ".pdf";
    if(prefix.startsWith("Rar!\x1a\x07"))return ".rar";
    if(prefix.startsWith("7z\xbc\xaf\x27\x1c"))return ".7z";
    if(prefix.startsWith("MSCF"))return ".cab";
    if(bytes[0]===0x1f&&bytes[1]===0x8b)return ".gz";
    if(prefix.startsWith("BZh"))return ".bz2";
    if(bytes.length>=6&&bytes[0]===0xfd&&bytes[1]===0x37&&
       bytes[2]===0x7a&&bytes[3]===0x58&&bytes[4]===0x5a&&bytes[5]===0)return ".xz";
    if(bytes.length>=262&&String.fromCharCode(...bytes.slice(257,262))==="ustar")return ".tar";
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
    let base=declared.replace(/\.(?:docx|doc|xlsx|xls|pdf|rtf|odt|ods|zip|rar|7z|tar|gz|tgz|bz2|xz|tbz2|txz|cab|xml)$/i,"")
      .replace(/[<>:"|?*\\/]/g," ").replace(/\s+/g," ").trim().slice(0,175);
    if(!base)base="Документ ЕИС "+downloadUid(item).slice(0,10);
    return base+detected;
  }
  async function handleRegistry(reg,searchDoc) {
    const {docs,source,landing,customer,title}=await discoverFromOfficialCard(reg,searchDoc);
    const manifestBase={
      reg_number:reg,source_page:source,landing_page:landing,
      title:title||searchTitle(searchDoc,reg),
      customer:customer.name||"",
      customer_inn:customer.inn||"",
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
    if(uploadedNow===0&&!complete){
      const originalsPresent=state.manifest_documents>=docs.length&&
        state.originals_received>=docs.length;
      if(originalsPresent){
        log(reg,"все оригиналы уже находятся на Railway; ожидание юридической перепроверки.");
        return {discovered:docs.length,uploaded:0,complete:false};
      }
      throw new Error("Оригиналы ЕИС получены не полностью: "+
        (state.originals_received||0)+" из "+docs.length+
        ". Ошибок скачивания: "+unresolved+".");
    }
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
      // Every fourth cycle revisit a historical results page to repair buyer/INN/title.
      // Still exactly ONE EIS search request per cycle.
      const historicalPage=(cycles%4===0)?(2+Math.floor(cycles/4-1)%19):1;
      const searchUrl=new URL(SEARCH,SITE);
      searchUrl.searchParams.set("pageNumber",String(historicalPage));
      log("Страница поиска ЕИС:",historicalPage);
      const search=await eisHtml(searchUrl.pathname+searchUrl.search);
      const numbers=registryNumbers(search);
      if (!numbers.length) {
        log("Номера закупок на странице ЕИС не обнаружены. Ничего не добавлено.");
        status="empty";
      } else {
        const known=await getKnown(numbers);
        const fresh=numbers.filter(n=>!known[n]?.complete||known[n]?.needs_metadata);
        log("В выдаче",numbers.length,"номеров; ещё требуют загрузки",fresh.length);
        const picks=[];
        const previouslyComplete=numbers.filter(n=>known[n]?.complete&&!fresh.includes(n));
        // Every third cycle re-enumerate ONE previously "complete" notice.
        // Legacy incomplete manifests must not prevent discovery of newly
        // published originals, but fresh purchases retain priority.
        const verifyOlder=(cycles%3===0)&&previouslyComplete.length>0;
        const freshSlots=verifyOlder?Math.max(1,MAX_BUYS-1):MAX_BUYS;
        for(let i=0;i<Math.min(freshSlots,fresh.length);i++)
          picks.push(fresh[(rotatingIndex+i)%fresh.length]);
        if(verifyOlder){
          const candidate=previouslyComplete[rotatingIndex%previouslyComplete.length];
          if(!picks.includes(candidate))picks.push(candidate);
        }
        rotatingIndex+=Math.max(1,MAX_BUYS);
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
        if(job.metadata_only){
          await discoverFromOfficialCard(job.target,null,true);
          report={status:"done",found:1,accepted:0,
            detail:"Карточка ЕИС прочитана: виды стоимости, способ и сроки переданы для проверки"};
        }else{
        // A previously "complete" manifest can be partial: older bridge
        // releases only recognized files with an extension in their title.
        // Re-discover the official tab and its full file list every time
        // the user explicitly requests a check, without re-downloading
        // previously verified identical originals.
        const before=uploaded;
        const outcome=await handleRegistry(job.target,null);
        const updated=(await getKnown([job.target]))[job.target]||{};
        const fullyReceived=outcome.complete &&
          updated.manifest_documents>=outcome.discovered;
        report={
          status:fullyReceived?"done":"partial",
          detail:fullyReceived?
            "Все обнаруженные оригиналы ЕИС получены и проверены":
            ("Получено "+(updated.originals_received||0)+" из "+
               (updated.visible_attachments||outcome.discovered||0)+
               " вложений ЕИС; остальные остаются в очереди"),
          accepted:uploaded-before,found:outcome.discovered?1:0
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
