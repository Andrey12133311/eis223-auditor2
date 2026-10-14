"""EIS-only mode: official browser-uploaded RI223 XML and verified originals.

No Gosplan / DaMIA / TenderGuru / Rostender / Roseltorg / ETP polling.
No direct calls to inaccessible EIS from Railway. Historical records retained.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
from fastapi.responses import JSONResponse

from v295_eis_originals import (
    allowed_documents, saved_document, root as data_root,
)
from v310_archive_scan import ARCHIVE_EXT,extract_members

REG=re.compile(r"[0-9]{11,20}$")


def tag(el):
    return el.tag.rsplit("}",1)[-1]


def direct(parent,name):
    if parent is None:return None
    return next((x for x in parent if tag(x)==name),None)


def field(parent,name):
    el=direct(parent,name)
    return (el.text or "").strip() if el is not None else ""


def official_notice(reg):
    """Read XML only from a validated, persisted official EIS SOAP archive."""
    folder=data_root()/"eis223_official_archive_inbox"/reg
    if not folder.is_dir():return None
    for receipt_path in sorted(folder.glob("*.json"),reverse=True)[:100]:
        try:
            receipt=json.loads(receipt_path.read_text(encoding="utf-8"))
            if receipt.get("reg_number")!=reg or receipt.get("source")!="official_eis_soap_archive":
                continue
            sha=receipt["archive_sha256"]
            if not re.fullmatch("[0-9a-f]{64}",sha):continue
            archive_path=folder/(sha+".zip")
            if not archive_path.is_file() or archive_path.stat().st_size>15*1024*1024:
                continue
            with zipfile.ZipFile(archive_path) as z:
                for notice in receipt.get("notices",[])[:50]:
                    if notice.get("reg_number")!=reg:continue
                    name=notice.get("xml_filename","")
                    if name not in z.namelist():continue
                    info=z.getinfo(name)
                    if info.file_size>15*1024*1024:continue
                    xml=z.read(name)
                    if hashlib.sha256(xml).hexdigest()!=notice.get("xml_sha256"):continue
                    if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():continue
                    root=ET.fromstring(xml)
                    if tag(root)!="purchaseNotice":continue
                    data=next((x for x in root.iter() if tag(x)=="purchaseNoticeData"),None)
                    if data is not None and field(data,"registrationNumber")==reg:
                        return data
        except (OSError,ValueError,KeyError,TypeError,zipfile.BadZipFile,ET.ParseError):
            continue
    return None


def stored_purchase(ns,reg):
    db=ns["conn"]()
    try:
        row=db.execute("SELECT * FROM purchases WHERE reg_number=?",(reg,)).fetchone()
        return dict(row) if row else {}
    finally:db.close()


async def offline_audit(ns,reg):
    """Use only local official XML and saved original bytes, no remote I/O."""
    if not REG.fullmatch(reg):raise ValueError("Invalid official registry number")
    allowed=allowed_documents(reg)
    if not allowed:raise ValueError("Verified EIS XML has no documents")
    notice=await asyncio.to_thread(official_notice,reg)
    public={}
    if notice is None:
        # Public search and the document page are visited by the user's browser;
        # no cross-origin requests from Railway are made here.
        try:
            path=data_root()/"eis223_public_browser_intake"/reg/"manifest.json"
            public=json.loads(path.read_text(encoding="utf-8"))
            if (not isinstance(public,dict) or
                public.get("source")!="public_eis_223_browser" or
                public.get("reg_number")!=reg or
                not public.get("documents")):
                raise ValueError("Invalid official-browser manifest")
        except (OSError,ValueError,TypeError) as exc:
            raise ValueError("Neither EIS SOAP XML nor public EIS manifest available") from exc

    previous=await asyncio.to_thread(stored_purchase,ns,reg)
    lot=direct(direct(notice,"lots"),"lot")
    lot_data=direct(lot,"lotData")
    cust=direct(direct(notice,"customer"),"mainInfo")
    reg_name=field(notice,"name") or public.get("title") or previous.get("title") or ("Закупка № "+reg)
    # Verified official buyer must win over old manual-INN search records;
    # those previously mislabeled unrelated Kamchatka procurements.
    from v311_verified_customer import read_verified
    trusted=await asyncio.to_thread(read_verified,ns,reg)
    customer=(field(cust,"fullName") or field(cust,"shortName")
              or trusted.get("name") or public.get("customer")
              or previous.get("customer") or "")
    customer_inn=(field(cust,"inn") or trusted.get("inn")
                  or public.get("customer_inn") or previous.get("customer_inn") or "")
    start=field(notice,"applSubmisionStartDate")[:10] or public.get("submission_start","")[:10]
    end=field(notice,"submissionCloseDateTime")[:10] or public.get("submission_end","")[:10]
    published=field(notice,"publicationDateTime") or public.get("published","")
    price=field(lot_data,"initialSum") or public.get("price") or str(previous.get("price") or "")
    num=ns.get("price_num",lambda x:None)(price)
    p={"reg":reg,
       "title":reg_name,
       "customer":customer,
       "customer_inn":customer_inn,
       "method":field(notice,"purchaseCodeName") or previous.get("method") or "",
       "price":price,
       "price_num":num if num is not None else previous.get("price_num"),
       "published":published or previous.get("published_at") or "",
       "submission_start":start or previous.get("submission_start") or "",
       "submission_end":end or previous.get("submission_end") or "",
       "deadline":end or previous.get("deadline") or "",
       "url":ns.get("old_eis_url",lambda r:"https://zakupki.gov.ru/223/purchase/public/purchase/info/common-info.html?regNumber="+r)(reg)}

    extractor=ns.get("extract_text")
    if extractor is None:raise RuntimeError("Existing original-document text extractor unavailable")
    docs=[]
    dedup_by_sha={}
    for uid,item in allowed.items():
        stored=saved_document(reg,uid)
        if stored is None:continue
        path,receipt=stored
        with path.open("rb") as f:
            sha=hashlib.file_digest(f,"sha256").hexdigest()
        if sha!=receipt["sha256"]:continue
        if sha in dedup_by_sha:
            existing=docs[dedup_by_sha[sha]]
            existing.setdefault("meta",{}).setdefault("identical_sources",[]).append(
                {"url":item["url"],"name":item["name"],"uid":uid})
            continue
        dedup_by_sha[sha]=len(docs)
        ext=path.suffix.lower()
        if ext in ARCHIVE_EXT:
            # Keep the binary original as source evidence but NEVER count
            # the archive itself as a checked legal document.
            members,warnings=await asyncio.wait_for(
                asyncio.to_thread(extract_members,path,reg,uid,data_root(),extractor),
                timeout=160)
            docs.append({
                "name":item["name"],"url":item["url"],"type":"archive",
                "published":"","meta":{"source":"official_eis_archive",
                    "uid":uid,"sha256":sha,"source_url":item["url"],
                    "archive_members_checked":len(members),
                    "archive_warnings":warnings},
                "text":"","local":str(path),"annotated":None,
                "status":"archive_processed" if members else "archive_unreadable",
                "error":"; ".join(warnings[:4]) if warnings else
                    ("В архиве нет читаемых документов" if not members else None),
                "final_url":item["url"],
            })
            for member in members:
                part_id=hashlib.sha256(member["name"].encode("utf-8")).hexdigest()[:24]
                name=item["name"]+" / "+member["name"]
                docs.append({
                    "name":name,
                    # A fragment refers to a specific member of the real
                    # downloaded EIS archive; it is NOT a separate EIS URL.
                    "url":item["url"]+"#archive-member="+part_id,
                    "type":ns.get("doc_type",lambda n,d="":"other")(member["name"]),
                    "published":"",
                    "meta":{"source":"official_eis_archive_member","uid":uid,
                        "sha256":member["sha256"],
                        "archive_name":item["name"],
                        "archive_member":member["name"],
                        "archive_sha256":sha,"source_url":item["url"]},
                    "text":member["text"],"local":str(member["path"]),
                    "annotated":None,"status":"checked","error":None,
                    "final_url":item["url"],
                })
            print(f"V310 ARCHIVE_EXTRACTED reg={reg} uid={uid} members_checked={len(members)} "
                  f"warnings={len(warnings)}",flush=True)
            continue
        text=await asyncio.wait_for(asyncio.to_thread(extractor,path),timeout=45)
        text=str(text or "")
        valid=len(text.strip())>=3
        docs.append({
            "name":item["name"],
            "url":item["url"],
            "type":ns.get("doc_type",lambda n,d="":"other")(item["name"]),
            "published":"",
            "meta":{"source":"official_eis_original","uid":uid,"sha256":sha,
                    "source_url":item["url"]},
            "text":text,
            "local":str(path),
            "annotated":None,
            "status":"checked" if valid else "downloaded_no_text",
            "error":None if valid else "Оригинал сохранён; текст не извлечён",
            "final_url":item["url"],
        })

    # Original URLs without downloaded content are deliberately NOT retried.
    # Show incomplete intake as waiting, never as an audited original.
    if not docs:raise ValueError("No readable original files have been uploaded from EIS")

    # Public EIS manifests from older collectors may contain only a status
    # instead of the subject and no buyer. Read explicit buyer fields from
    # the verified originals before analysis and the first database save.
    from v314_eis_metadata import from_documents, subject
    from v311_verified_customer import _name
    metadata=await asyncio.to_thread(from_documents,reg,docs)
    if metadata:
        if not _name(p["customer"]):p["customer"]=metadata.get("customer") or ""
        if not p["customer_inn"]:p["customer_inn"]=metadata.get("customer_inn") or ""
        if not subject(p["title"]):p["title"]=metadata.get("title") or p["title"]

    card={
       "status":"partial",
       "error":"Публичная карточка не перечитана с сервера; использованы официальный XML ЕИС и оригиналы",
       "pairs":[("registrationNumber",reg),("purchaseMethod",p["method"])],
       "visible_text":"",
       "pages":{},
       "summary":{"title":p["title"],"customer":p["customer"],
                  "method":p["method"],"published":p["published"],
                  "submission_start":p["submission_start"],
                  "submission_end":p["submission_end"],"price":p["price"],
                  "price_num":p["price_num"]},
       "discovery":"getDocsIP, RI223, official-browser-bridge",
    }
    if metadata:card["metadata_evidence"]={"source":"official_eis_document",**metadata}
    detail={
       "registrationNumber":reg,
       "purchaseName":p["title"],
       "purchaseMethodName":p["method"],
       "customer":{"name":p["customer"]},
       "publicationDate":p["published"],
       "initialSum":p["price"],
       "submissionStart":p["submission_start"],
       "submissionEnd":p["submission_end"],
       "source":"official_eis_getDocsIP",
    }
    analyze=ns.get("analyze")
    build_checks=ns.get("build_checks")
    save=ns.get("save")
    if not all(callable(x) for x in (analyze,build_checks,save)):
        raise RuntimeError("Existing 223-FZ legal analysis functions are unavailable")
    findings=await asyncio.to_thread(analyze,p,docs)
    # Only substantiated violations enter the NEW incoming audit results.
    # Weak heuristic "risk" matches do not become violations and are not
    # presented as a performance indicator. Historical data are retained.
    findings=[f for f in findings if isinstance(f,dict) and f.get("kind")=="violation"]
    checks=await asyncio.to_thread(build_checks,p,detail,card,docs)
    checks=[c for c in checks if isinstance(c,dict) and c.get("status")!="risk"]
    # Explicit wording in a verified original is a review signal, not
    # an automatically established legal violation.
    for doc in docs:
        if doc.get("status")!="checked":continue
        body=" ".join(str(doc.get("text") or "").split())
        for phrase in ("эквивалент не допускается","аналоги не допускаются"):
            offset=body.casefold().find(phrase)
            if offset<0:continue
            checks.append({"code":"equivalence_restriction_review",
                           "status":"check",
                           "title":"Проверить ограничение эквивалентности товара",
                           "document":doc.get("name") or "",
                           "evidence":body[max(0,offset-90):offset+len(phrase)+90],
                           "legal_basis":"223-ФЗ, статья 3, часть 1, пункт 2",
                           "reason":"Требуется оценить предмет закупки и исключения"})

    # Cross-check original evidence against EIS dates; uncertain or missing
    # information is a review item, never an automatic violation.
    from datetime import date
    def as_day(value):
        try:return date.fromisoformat(str(value or "")[:10])
        except ValueError:return None
    opening=as_day(p.get("submission_start"))
    closing=as_day(p.get("submission_end"))
    if opening and closing and closing<opening:
        checks.append({"code":"submission_dates_conflict","status":"check",
                       "title":"Несоответствие дат подачи заявок",
                       "evidence":f"Начало: {opening}; окончание: {closing}",
                       "legal_basis":"223-ФЗ, статья 4, часть 9",
                       "reason":"Сверить исходное извещение и изменения ЕИС"})
    if closing and as_day(p.get("published")) and closing<as_day(p.get("published")):
        checks.append({"code":"deadline_before_publication","status":"check",
                       "title":"Окончание подачи заявок ранее публикации",
                       "evidence":f"Публикация: {p.get('published')}; окончание: {closing}",
                       "legal_basis":"223-ФЗ, статья 4, часть 9",
                       "reason":"Проверить даты и версии извещения ЕИС"})
    # Keep bid security and performance security strictly distinct.
    for doc in docs:
        if doc.get("status")!="checked":continue
        body=" ".join(str(doc.get("text") or "").split())
        for phrase,code,title in (
            ("обеспечение заявки не требуется","bid_security_exemption","Обеспечение заявки не требуется"),
            ("обеспечение исполнения договора","contract_security_only","Упомянуто обеспечение договора, не заявки"),
        ):
            offset=body.casefold().find(phrase)
            if offset>=0:
                checks.append({"code":code,"status":"check",
                    "title":title,"document":doc.get("name") or "",
                    "evidence":body[max(0,offset-65):offset+len(phrase)+65],
                    "legal_basis":"223-ФЗ, статья 3.2, часть 25",
                    "reason":"Не квалифицировать как установленное обеспечение заявки без прямого условия"})
    # Article 4(8): only flag discrepancies when the original expressly
    # labels its own submission deadline. Never infer from arbitrary dates.
    import re as _legal_re
    for doc in docs:
        if doc.get("status")!="checked":continue
        body=" ".join(str(doc.get("text") or "").split())
        if not body or not closing:continue
        matches=list(_legal_re.finditer(
            r"(?:окончани[ея] срока подачи заявок|прием заявок заканчивается)"
            r"[^\\n.;]{0,100}?(\\d{2}[.]\\d{2}[.]\\d{4})",body,_legal_re.I))
        for match in matches[:2]:
            try:
                from datetime import datetime as _legal_datetime
                doc_day=_legal_datetime.strptime(match.group(1),"%d.%m.%Y").date()
            except ValueError:continue
            if doc_day==closing:continue
            checks.append({"code":"notice_document_deadline_mismatch",
                           "status":"check",
                           "title":"Несоответствие срока подачи заявок в документации и извещении",
                           "document":doc.get("name") or "",
                           "evidence":match.group(0),
                           "notice_date":str(closing),
                           "document_date":str(doc_day),
                           "legal_basis":"223-ФЗ, статья 4, часть 8",
                           "reason":"Проверить действующую редакцию документации и изменения ЕИС"})
    # National-regime flags in the official notice are evidence requiring
    # product-code and exception analysis, not proof of infringement.
    if notice is not None:
        flags=[]
        for node in notice.iter():
            key=tag(node).casefold()
            if key in ("prohibitionnationalregime","restrictionnationalregime",
                       "preferencenationalregime") and str(node.text or "").strip().lower()=="true":
                flags.append(key)
        if flags:
            checks.append({"code":"national_regime_eis_flags","status":"check",
                           "title":"Проверить условия национального режима",
                           "document":"Официальное извещение ЕИС (XML)",
                           "evidence":", ".join(sorted(set(flags))),
                           "legal_basis":"223-ФЗ, статья 3.1-4",
                           "reason":"Сверить коды товаров, действующий нормативный акт и исключения"})
    # Structured clarification/cancellation events: compare calendar dates
    # only. Holiday-sensitive statutory conclusions require an authoritative
    # production calendar and are deliberately held for legal review.
    from datetime import timedelta
    def event_day(value):
        if isinstance(value,dict):
            for key in ("date","created","request_date","requestDate",
                        "publicationDate","published","published_at"):
                if value.get(key):return as_day(value[key])
            return None
        return as_day(value)
    for event in (public.get("clarifications") or []) if isinstance(public.get("clarifications"),list) else []:
        if not isinstance(event,dict):continue
        request_day=event_day(event.get("request_date") or event.get("requestDate"))
        answer_day=event_day(event.get("publication_date") or event.get("publicationDate"))
        if not request_day or not answer_day or not closing:continue
        if request_day>closing:continue
        weekdays_before=sum((request_day+timedelta(days=n)).weekday()<5
                            for n in range(1,(closing-request_day).days+1))
        if weekdays_before<3:continue
        elapsed_weekdays=sum((request_day+timedelta(days=n)).weekday()<5
                             for n in range(1,(answer_day-request_day).days+1))
        if elapsed_weekdays>3:
            checks.append({"code":"clarification_three_workdays_review","status":"check",
                "title":"Возможное нарушение срока размещения разъяснения",
                "document":"Сведения ЕИС об изменениях и разъяснениях",
                "evidence":f"Запрос: {request_day}; публикация: {answer_day}; рабочих дней без праздников: {elapsed_weekdays}",
                "legal_basis":"223-ФЗ, статья 3.2, часть 3",
                "reason":"Проверить производственный календарь, дату получения запроса и действующую редакцию извещения"})
    for event in (public.get("cancellations") or []) if isinstance(public.get("cancellations"),list) else []:
        if not isinstance(event,dict):continue
        decision=event_day(event.get("decision_date") or event.get("decisionDate"))
        published_day=event_day(event.get("publication_date") or event.get("publicationDate"))
        if decision and published_day and published_day!=decision:
            checks.append({"code":"cancellation_publication_review","status":"check",
                "title":"Возможное несвоевременное размещение решения об отмене",
                "document":"Протокол отмены / сведения ЕИС",
                "evidence":f"Решение: {decision}; размещение: {published_day}",
                "legal_basis":"223-ФЗ, статья 3.2, часть 6",
                "reason":"Сверить официальный протокол, время и основания отмены"})
    annotate=ns.get("annotate")
    if callable(annotate):
        await asyncio.to_thread(annotate,reg,docs,findings,checks)
    payload={
       **card,
       "status":"partial",
       "origin":"official_eis_getDocsIP" if notice is not None else "public_eis_223_browser",
       "source_diagnostic":"Official EIS browser intake and verified original bytes",
       "source_document_count":len(allowed),
       "attached_originals":len(docs),
       "api_fields":list(detail.items()),
    }
    await asyncio.to_thread(save,p,docs,findings,checks,payload)
    if metadata:
        # Older save implementations omit customer_inn; retain the verified
        # value and source after saving without replacing docs or findings.
        from v314_eis_metadata import apply_metadata
        await asyncio.to_thread(apply_metadata,ns,reg,metadata)
    checked=sum(d.get("status")=="checked" for d in docs)
    print(f"V298 OFFLINE_AUDIT reg={reg} documents={len(docs)} checked={checked} findings={len(findings)} checks={len(checks)} remote_requests=0",flush=True)
    return {"reg":reg,"documents":len(docs),"checked_documents":checked,
            "findings":len(findings),"checks":len(checks),
            "source":"official_eis_getDocsIP" if notice is not None else "public_eis_223_browser",
            "remote_requests":0}


def install(ns):
    """Switch off all historic background collectors and source-triggering routes."""
    os.environ["EIS223_SOURCE_MODE"]="OFFICIAL_EIS_BROWSER_ONLY"
    app=ns["app"]

    # Core Python module initializes its DB on import. Non-EIS start hooks launch
    # legacy scanners, retries, scrapers and timers; none may run in EIS-only mode.
    before=list(app.router.on_startup)
    kept=[]
    disabled=[]
    for fn in before:
        name=getattr(fn,"__name__",type(fn).__name__)
        if name in ("resume_official_uploads","reconcile"):
            kept.append(fn)
        else:
            disabled.append(name)
    app.router.on_startup[:]=kept

    async def no_legacy_cycle(*args,**kwargs):
        return {"started":False,"source":"ЕИС","reason":"Legacy discovery disabled"}
    ns["cycle"]=no_legacy_cycle
    ns["loop"]=no_legacy_cycle
    ns["_v290_schedule_roseltorg"]=lambda *a,**kw:None
    ns["_v290_roseltorg_attach"]=lambda *a,**kw:None
    ns["_v298_offline_audit"]=lambda reg: offline_audit(ns,reg)
    if isinstance(ns.get("state"),dict):
        ns["state"].update(running=False,current_reg=None,last_error=None)

    @app.middleware("http")
    async def eis_only_gateway(request,call_next):
        path=request.url.path.lower()
        method=request.method.upper()
        # Public historical dashboards, existing saved documents, PDF/ODT
        # output and official EIS bridge remain available.
        forbidden=(
          "/api/v278/", "/api/v279/", "/api/v280/", "/api/v281/",
          "/api/v282/", "/api/v288/", "/api/v289/", "/api/v290/",
          "/api/tenderguru", "/api/damia", "/api/rostender",
          "/api/roseltorg", "/api/gosplan",
        )
        if any(path.startswith(p) for p in forbidden):
            return JSONResponse({"source":"ЕИС","status":"disabled",
                                 "detail":"Источник отключён. Работает только официальный ЕИС через браузер."},
                                status_code=410)
        if (method in ("POST","PUT","PATCH") and
              not path.startswith(("/api/v294/eis223/","/api/v295/eis223/")) and
              (path=="/api/scan-now"
               or re.search(r"/(?:scan|retry|recheck|rescan|re-download|download-originals)(?:/|$)",path)
               or (path.startswith("/api/purchases/") and path.endswith("/recheck")))):
            return JSONResponse({"started":False,"source":"ЕИС","status":"disabled",
                                 "detail":"Старая очередь остановлена. Загружайте официальные XML и оригиналы ЕИС."})
        return await call_next(request)

    @app.get("/api/v298/eis-only/status")
    async def mode_status():
        inbox=data_root()/"eis223_official_archive_inbox"
        regs=0
        if inbox.is_dir():
            regs=sum(bool(p.is_dir() and REG.fullmatch(p.name)) for p in inbox.iterdir())
        return {"mode":"official_eis_browser_only",
                "sources":["ЕИС getDocsIP (браузерное подключение)"],
                "legacy_background_workers":0,
                "legacy_failed_requests_scheduled":0,
                "official_imported_procurements":regs,
                "existing_data_preserved":True,
                "direct_eis_server_requests":False}

    print("V298 EIS_ONLY_ACTIVE "+json.dumps({
        "remaining_hooks":[getattr(x,"__name__","?") for x in kept],
        "disabled_start_hooks":disabled,
        "legacy_sources_enabled":0,
        "offline_document_audit":True,
        "historical_data_preserved":True,
    },ensure_ascii=False),flush=True)
