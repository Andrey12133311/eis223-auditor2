"""Conservative five-minute PUBLIC EIS browser-origin 223-FZ intake.

No server-side polling of zakupki.gov.ru, no risky third-party sources.
Validated EIS public registry numbers and file UID URLs enter the existing
verified-originals pipeline. No metadata alone is counted as audited.
"""
from __future__ import annotations
import asyncio
import fcntl
import json
import os
import re
from datetime import datetime,timezone,timedelta
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
from fastapi import HTTPException,Request
from fastapi.responses import JSONResponse,PlainTextResponse

from v295_eis_originals import (
    REG,EXT,uid_from_url,root,auth,save_atomic,
    allowed_documents,saved_document,_manifest_source_evidence,MAX_SOURCE_MANIFEST_BYTES,
)
from v342_identity import subject as _valid_subject, valid_inn

MAX_MANIFEST_BYTES=65536
MAX_DOCS=100
MAX_STORED_DOCS=10000
MAX_REFERENCE_HISTORY=1000
MAX_MANIFESTS=20000

# Amendment history is supplied by the authenticated official-browser caller.
# These checks establish provenance/identity, not verification of original bytes.
MAX_CHANGES=80
MAX_CHANGE_BYTES=32768
_NOTICE_PREFIXES=("/epz/order/notice/notice223/", "/223/purchase/public/purchase/info/")
_CHANGE_TYPES=re.compile(
    r"(?:amendment|change|changes|revision|внесение изменений|"
    r"изменени[ея](?: (?:извещения|документации|закупочной документации))?|"
    r"(?:новая )?редакция(?: (?:извещения|документации))?)",re.I)


class AmendmentHistoryConflict(ValueError):
    """An existing event cannot be replaced without resolving its evidence."""


def _change_text(value,label,limit,required=False):
    if value is None and not required:return ""
    if (not isinstance(value,str) or len(value)>limit
            or any(ord(c)<32 and c not in "\n\r\t" for c in value)):
        raise ValueError("Invalid amendment "+label)
    if label in ("source page","attachment URL") and any(c.isspace() for c in value):
        raise ValueError("Invalid amendment "+label)
    value=value.strip()
    if required and not value:raise ValueError("Missing amendment "+label)
    return value


def _change_day(value,label,required=False):
    raw=_change_text(value,label,40,required)
    if not raw:return ""
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}",raw):
            return datetime.strptime(raw,"%Y-%m-%d").date().isoformat()
        if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}",raw):
            return datetime.strptime(raw,"%d.%m.%Y").date().isoformat()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})?",raw):
            return datetime.fromisoformat(raw.replace("Z","+00:00")).date().isoformat()
    except ValueError:pass
    raise ValueError("Invalid amendment "+label)


def _notice_identity(url,reg):
    """Return only unambiguous purchase identifiers from official notice pages."""
    raw=_change_text(url,"source page",1000,True)
    try:
        p=urlsplit(raw)
        if (p.scheme!="https" or p.hostname!="zakupki.gov.ru" or p.port not in (None,443)
                or p.username or p.password or p.fragment or "\\" in raw
                or any(c.isspace() for c in raw)
                or not any(p.path.startswith(prefix) and re.fullmatch(
                    r"[a-zA-Z0-9_-]+\.html",p.path[len(prefix):]) for prefix in _NOTICE_PREFIXES)):
            raise ValueError("Invalid official amendment page")
        q=parse_qs(p.query,keep_blank_values=True,strict_parsing=True,max_num_fields=20)
        regs=q.get("regNumber",[])+q.get("purchaseNoticeNumber",[])
        ids=[(name,v) for name in ("noticeId","noticeInfoId") for v in q.get(name,[])]
        if (regs and regs!=[reg]) or len(ids)>1 or any(
                not re.fullmatch(r"[0-9]{1,15}",v) for _,v in ids):
            raise ValueError("Conflicting amendment purchase identity")
        if not regs and not ids:raise ValueError("Unbound amendment source page")
        return bool(regs),set(ids)
    except (ValueError,TypeError) as exc:
        raise ValueError("Invalid same-purchase amendment provenance") from exc


def _merge_changes(*groups):
    """Exact duplicates collapse; competing evidence for one event is rejected."""
    merged=[];seen=set();identities={}
    for group in groups:
        for change in group:
            fingerprint=json.dumps(change,ensure_ascii=False,sort_keys=True,separators=(",",":"))
            if fingerprint in seen:continue
            keys=[]
            if change.get("event_id"):keys.append(("event",change["event_id"]))
            if change.get("source_url"):keys.append(("attachment",uid_from_url(change["source_url"])))
            if not keys:
                # Without a stable event/original ID, same-day history entries
                # are ambiguous; a filename is not proof of a distinct event.
                keys.append(("history-day",change["publication_date"]))
            if any(key in identities and identities[key]!=fingerprint for key in keys):
                raise AmendmentHistoryConflict("Conflicting amendment history; previous evidence was not replaced")
            for key in keys:identities[key]=fingerprint
            seen.add(fingerprint);merged.append(change)
    if len(merged)>MAX_CHANGES or len(json.dumps(merged,ensure_ascii=False).encode("utf-8"))>MAX_CHANGE_BYTES:
        raise AmendmentHistoryConflict("Amendment history exceeds limit; previous evidence was not replaced")
    return merged


def _parse_changes(value,reg,source_page,landing_page,documents,retained=False):
    if value is None:raise ValueError("Expected amendment changes array")
    if not isinstance(value,list) or len(value)>MAX_CHANGES:
        raise ValueError("Expected at most 80 amendment events")
    if not value:return []
    if not isinstance(documents,list):raise ValueError("Invalid amendment original-file references")
    bound_uids={uid_from_url(item.get("url") or "") for item in documents
                if isinstance(item,dict) and item.get("uid")
                and item.get("uid")==uid_from_url(item.get("url") or "")}
    out=[]
    for item in value:
        if not isinstance(item,dict):raise ValueError("Expected amendment event object")
        if "reg_number" in item and item["reg_number"]!=reg:
            raise ValueError("Conflicting amendment registry number")
        # Persist the original page/landing pair to retain its established
        # notice identity when a later document-only capture changes pages.
        # Incoming caller-provided provenance never overrides current context.
        context={"source_page":source_page,"landing_page":landing_page or ""}
        if retained and "provenance" in item:
            context=item["provenance"]
            if not isinstance(context,dict) or set(context)!={"source_page","landing_page"}:
                raise ValueError("Invalid retained amendment provenance")
        _,notice_ids=_notice_identity(context["source_page"],reg)
        if context["landing_page"]:
            _,landing_ids=_notice_identity(context["landing_page"],reg)
            if notice_ids and landing_ids and notice_ids!=landing_ids:
                raise ValueError("Conflicting amendment notice identity")
            notice_ids|=landing_ids
        kind=_change_text(item.get("type"),"type",100,True)
        if not _CHANGE_TYPES.fullmatch(kind):raise ValueError("Expected explicit amendment event")
        page=_change_text(item.get("source_page"),"source page",1000,True)
        has_reg,event_ids=_notice_identity(page,reg)
        if event_ids and not event_ids.issubset(notice_ids):
            raise ValueError("Amendment source belongs to a different or unverified notice")
        if not has_reg and not event_ids:raise ValueError("Unbound amendment source page")
        source_url=_change_text(item.get("source_url"),"attachment URL",1000)
        if source_url:
            uid=uid_from_url(source_url)
            if not uid or uid not in bound_uids or any(c.isspace() for c in source_url):
                raise ValueError("Unbound amendment attachment URL")
            # Do not allow a correct UID to hide a different purchase identity.
            attachment=urlsplit(source_url)
            q=parse_qs(attachment.query,keep_blank_values=True,strict_parsing=True,max_num_fields=20)
            allowed={"uid","download"} if "filestore" in attachment.path else {"id"}
            if set(q)-allowed or ("download" in q and q["download"] not in (["0"],["1"])):
                raise ValueError("Invalid amendment attachment URL parameters")
        publication=_change_day(item.get("publication_date"),"publication date")
        published_at=_change_day(item.get("published_at"),"publication timestamp")
        if publication and published_at and publication!=published_at:
            raise ValueError("Conflicting amendment publication dates")
        publication=publication or published_at
        if not publication:raise ValueError("Missing explicit amendment publication date")
        publication_evidence=_change_text(item.get("publication_evidence"),"publication evidence",1000,True)
        decision=_change_day(item.get("decision_date"),"decision date")
        decision_evidence=_change_text(item.get("decision_evidence"),"decision evidence",1000,bool(decision))
        event_id=_change_text(item.get("event_id"),"event ID",80)
        if event_id and not re.fullmatch(r"[0-9A-Za-zА-Яа-яЁё][0-9A-Za-zА-Яа-яЁё./_-]{0,79}",event_id):
            raise ValueError("Invalid amendment event ID")
        change={"type":kind,"source_page":page,"publication_date":publication,
                "publication_evidence":publication_evidence,"provenance":context}
        for key,val in (("source_url",source_url),("event_id",event_id),
                        ("document_name",_change_text(item.get("document_name"),"document name",255)),
                        ("decision_date",decision),("decision_evidence",decision_evidence),
                        ("submission_end",_change_day(item.get("submission_end"),"submission deadline"))):
            if val:change[key]=val
        out.append(change)
    return _merge_changes(out)


def _safe_string(value,max_length=255):
    return str(value or "").replace("\x00","").strip()[:max_length]

def _parse_manifest(payload):
    if not isinstance(payload,dict):raise ValueError("Expected manifest object")
    reg=_safe_string(payload.get("reg_number"),20)
    if not REG.fullmatch(reg) or not (reg.startswith("3") and len(reg)==11):
        raise ValueError("Not a valid 223-FZ procurement registry number")
    source_page=_safe_string(payload.get("source_page"),1000)
    landing_page=_safe_string(payload.get("landing_page"),1000)
    try:
        source=urlsplit(source_page)
        q=parse_qs(source.query)
        source_ok=(source.scheme=="https" and source.hostname=="zakupki.gov.ru"
            and source.port is None and not source.username and not source.password
            and source.path in (
                "/epz/order/notice/notice223/documents.html",
                "/223/purchase/public/purchase/info/documents.html",
                "/epz/order/notice/notice223/common-info.html",
                "/223/purchase/public/purchase/info/common-info.html",
                "/223/purchase/public/purchase/info/commoninfo.html"))
        supplied=q.get("regNumber",[])+q.get("purchaseNoticeNumber",[])
        if supplied and (len(supplied)!=1 or supplied[0]!=reg):source_ok=False
        has_reg=bool(supplied and supplied[0]==reg)
        ids=[(name,value) for name in ("noticeId","noticeInfoId")
             for value in q.get(name,[])]
        has_notice_id=(len(ids)==1 and bool(re.fullmatch(r"[0-9]{1,15}",ids[0][1])))
        landing=urlsplit(landing_page)
        lq=parse_qs(landing.query)
        landing_ok=(landing.scheme=="https" and landing.hostname=="zakupki.gov.ru"
             and landing.port is None and not landing.username and not landing.password
             and landing.path in (
                 "/epz/order/notice/notice223/common-info.html",
                 "/223/purchase/public/purchase/info/common-info.html",
                 "/223/purchase/public/purchase/info/commoninfo.html")
             and lq.get("regNumber")==[reg])
        landing_by_notice_id=(
            has_notice_id and landing.scheme=="https" and
            landing.hostname=="zakupki.gov.ru" and
            landing.port is None and not landing.username and not landing.password and
            landing.path in (
                "/epz/order/notice/notice223/common-info.html",
                "/223/purchase/public/purchase/info/common-info.html",
                "/223/purchase/public/purchase/info/commoninfo.html")
            and lq.get(ids[0][0])==[ids[0][1]]
            and not lq.get("regNumber"))
        if not source_ok or not (has_reg or (has_notice_id and
                                          (landing_ok or landing_by_notice_id))):
            raise ValueError("Invalid official EIS notice223 provenance")
    except ValueError as exc:
        raise ValueError("Invalid official EIS document-page provenance") from exc
    docs=payload.get("documents")
    if not isinstance(docs,list) or not 1<=len(docs)<=MAX_DOCS:
        raise ValueError("Expected 1–100 EIS original-file references")
    out=[];seen=set()
    for item in docs:
        if not isinstance(item,dict):continue
        url=_safe_string(item.get("url"),1000)
        uid=uid_from_url(url)
        if not uid or uid in seen:continue
        name=_safe_string(item.get("name"),255)
        if name in (".","..") or "/" in name or "\\" in name:continue
        ext=Path(name).suffix.lower()
        if ext not in EXT:continue
        seen.add(uid)
        out.append({"uid":uid,"url":url,"name":name,"ext":ext})
    if not out:raise ValueError("No recognized official EIS attachment URLs")
    raw_count=payload.get("declared_attachment_count",len(out))
    try:attachment_count=int(raw_count)
    except (ValueError,TypeError):raise ValueError("Invalid attachment count")
    if attachment_count<len(out) or attachment_count>MAX_DOCS:
        raise ValueError("Invalid attachment count")
    return {
        "source":"public_eis_223_browser",
        "reg_number":reg,
        "source_page":source_page,
        "landing_page":landing_page,
        "title":_safe_string(payload.get("title"),800),
        "customer":_safe_string(payload.get("customer"),400),
        "customer_inn":_safe_string(payload.get("customer_inn"),12),
        "published":_safe_string(payload.get("published"),40),
        "submission_start":_safe_string(payload.get("submission_start"),40),
        "submission_end":_safe_string(payload.get("submission_end"),40),
        "price":_safe_string(payload.get("price"),60),
        "documents":out,
        "changes":_parse_changes(payload.get("changes",[]),reg,source_page,landing_page,out),
        # EIS attachments lacking extensions enter this count immediately.
        # Registration of their original-file URL is deferred until the
        # browser has fetched and validated the actual binary file format.
        "declared_attachment_count":attachment_count,
        "verified_originals":0,
    }

def _manifest_path(reg):
    return root()/"eis223_public_browser_intake"/reg/"manifest.json"

def _merge_document_references(*groups):
    by_uid={}
    for group in groups:
        if not isinstance(group,list):raise ValueError("Invalid stored document references")
        for item in group:
            if not isinstance(item,dict):raise ValueError("Invalid stored document reference")
            uid=uid_from_url(item.get("url",""))
            name=item.get("name")
            if (not uid or item.get("uid")!=uid or not isinstance(name,str) or not name
                    or len(name)>255 or "/" in name or "\\" in name or Path(name).suffix.lower() not in EXT):
                raise ValueError("Invalid stored document identity")
            prior=by_uid.get(uid)
            if prior is not None and Path(prior["name"]).suffix.lower()!=Path(name).suffix.lower():
                raise ValueError("Conflicting original format for the same UID; previous reference was not replaced")
            history=[]
            for reference in ([prior] if prior else [])+[item]:
                inherited=reference.get("reference_history",[])
                if not isinstance(inherited,list):raise ValueError("Invalid stored reference history")
                history.extend(inherited)
                history.append({key:reference[key] for key in ("uid","name","url","ext") if key in reference})
            unique={}
            for reference in history:
                if (not isinstance(reference,dict) or reference.get("uid")!=uid or
                        uid_from_url(reference.get("url",""))!=uid):
                    raise ValueError("Invalid stored reference history identity")
                unique[json.dumps(reference,ensure_ascii=False,sort_keys=True)]=reference
            if len(unique)>MAX_REFERENCE_HISTORY:raise ValueError("Document reference history exceeds limit")
            merged=dict(item)
            if len(unique)>1:merged["reference_history"]=list(unique.values())
            by_uid[uid]=merged
            if len(by_uid)>MAX_STORED_DOCS:raise ValueError("Cumulative document history exceeds limit")
    return list(by_uid.values())


def _save_manifest(value,with_change=False):
    # asyncio.to_thread callers and separate application processes must hold
    # the same per-procurement lock from the first read through replacement.
    reg=value.get("reg_number")
    if not isinstance(reg,str) or not re.fullmatch(r"3[0-9]{10}",reg):
        raise ValueError("Invalid manifest registry number")
    path=_manifest_path(reg)
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.with_suffix(".lock").open("a+b") as lock:
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX)
        try:
            previous=None
            if with_change and path.is_file():
                if path.stat().st_size>MAX_SOURCE_MANIFEST_BYTES:
                    raise AmendmentHistoryConflict("Stored manifest exceeds limit; previous evidence was not replaced")
                try:previous=json.loads(path.read_text(encoding="utf-8"))
                except (OSError,ValueError,TypeError) as exc:
                    raise AmendmentHistoryConflict("Stored manifest is damaged; previous evidence was not replaced") from exc
            count=_save_manifest_locked(value,path)
            changed=previous is None or _manifest_source_evidence(previous)!=_manifest_source_evidence(value)
            return (count,changed) if with_change else count
        finally:fcntl.flock(lock.fileno(),fcntl.LOCK_UN)


def _save_manifest_locked(value,path):
    changes=value.get("changes",[])
    if path.is_file():
        try:
            if path.stat().st_size>MAX_SOURCE_MANIFEST_BYTES:raise ValueError("Stored manifest exceeds limit")
            previous=json.loads(path.read_text(encoding="utf-8"))
            if (not isinstance(previous,dict) or previous.get("source")!=value["source"]
                    or previous.get("reg_number")!=value["reg_number"]):
                raise ValueError("Existing manifest identity is invalid")
            # Validate old evidence against its own captured source/notice, not
            # a later revision's page. A doc-only intake cannot erase history.
            old_changes=_parse_changes(previous.get("changes",[]),value["reg_number"],
                previous.get("source_page"),previous.get("landing_page"),previous.get("documents",[]),retained=True)
            changes=_merge_changes(old_changes,changes)
            value["documents"]=_merge_document_references(previous.get("documents",[]),value["documents"])
            value["declared_attachment_count"]=max(
                int(previous.get("declared_attachment_count") or 0),
                value["declared_attachment_count"],len(value["documents"]))
            for key in ("title","customer","customer_inn","published","submission_start","submission_end","price"):
                if not value.get(key):value[key]=previous.get(key,"")
        except (OSError,ValueError,KeyError,TypeError) as exc:
            # Do not swallow conflicts or damaged prior history and overwrite
            # the only retained evidence. The API reports a recoverable 409.
            raise AmendmentHistoryConflict("Cannot safely merge existing amendment history: "+str(exc)) from exc
    value["changes"]=changes
    value["received_at"]=datetime.now(timezone.utc).isoformat()
    encoded=json.dumps(value,ensure_ascii=False).encode("utf-8")
    if len(encoded)>MAX_SOURCE_MANIFEST_BYTES:
        raise AmendmentHistoryConflict("Stored manifest exceeds limit; previous evidence was not replaced")
    save_atomic(path,encoded)
    return len(value["documents"])

def _real_customer(value):
    from v311_verified_customer import _name
    return _name(value)

def _apply_customer(ns,value):
    name=_real_customer(value.get("customer"))
    inn=valid_inn(value.get("customer_inn"))
    db=ns["conn"]()
    try:
        db.execute("BEGIN IMMEDIATE")
        reg=value["reg_number"]
        row=db.execute("SELECT title,customer,customer_inn FROM purchases WHERE reg_number=?",(reg,)).fetchone()
        if row is None:
            db.commit()
            return False
        old_title,old_name,old_inn=row
        changes={}
        title=_valid_subject(value.get("title"))
        if title and not _valid_subject(old_title):changes["title"]=title
        clean_name=_real_customer(old_name)
        if name and not clean_name:changes["customer"]=name
        elif clean_name and clean_name!=old_name:changes["customer"]=clean_name
        if inn and not valid_inn(old_inn):changes["customer_inn"]=inn
        if changes:
            db.execute("UPDATE purchases SET "+",".join(k+"=?" for k in changes)+
                       " WHERE reg_number=?",(*changes.values(),reg))
        # A submitted search INN is NOT evidence that the retrieved buyer
        # has this INN. Do not copy buyer names between unrelated cards.
        db.commit()
        return bool(changes)
    except Exception:
        if db.in_transaction:db.rollback()
        raise
    finally:db.close()

def _save_official_card(value):
    """One verified EIS card per procurement, persisted for later offline audit."""
    path=root()/"eis223_public_browser_intake"/value["reg_number"]/"buyer.json"
    save_atomic(path,json.dumps({
      **value,"source":"public_eis_223_browser_card",
      "captured_at":datetime.now(timezone.utc).isoformat()
    },ensure_ascii=False).encode("utf-8"))

def _known(ns,regs):
    from v335_price_recovery import money
    db=ns["conn"]()
    try:
        existing={}
        if regs:
            pmarks=",".join("?" for _ in regs)
            rows=db.execute(
                "SELECT reg_number,documents_checked,customer,title,price,price_num,customer_inn FROM purchases WHERE reg_number IN ("+pmarks+")",
                regs).fetchall()
            existing={r[0]:{"checked":int(r[1] or 0),
                            "needs_metadata":not _real_customer(r[2])
                                or not _valid_subject(r[3])
                                or not valid_inn(r[6])
                                or not (money(r[5]) or money(r[4]))}
                      for r in rows}
        result={}
        for reg in regs:
            refs=allowed_documents(reg)
            saved=sum(bool(saved_document(reg,uid)) for uid in refs)
            expected=len(refs)
            declared=expected
            try:
                info=json.loads(_manifest_path(reg).read_text(encoding="utf8"))
                if info.get("reg_number")==reg and info.get("source")=="public_eis_223_browser":
                    declared=max(expected,int(info.get("declared_attachment_count") or 0))
            except (OSError,ValueError,TypeError,AttributeError):
                pass
            result[reg]={
                "manifest_documents":expected,
                "visible_attachments":declared,
                "unresolved_attachments":max(0,declared-expected),
                "originals_received":saved,
                "checked_documents":existing.get(reg,{}).get("checked",0),
                "needs_metadata":existing.get(reg,{}).get("needs_metadata",True),
                "complete":bool(expected and saved==expected and expected>=declared
                                and existing.get(reg,{}).get("checked",0)>=expected),
            }
        return result
    finally:db.close()

def _scan_status():
    vps_only=os.environ.get("EIS223_SOURCE_MODE")=="OFFICIAL_EIS_VPS_ONLY"
    path=root()/"eis223_public_browser_intake"/"_monitor_status.json"
    try:
        info=json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(info,dict):info={}
    except (ValueError,OSError,TypeError):
        info={}
    at=info.get("last_scan")
    try:version=int(info.get("collector_version") or 0)
    except (ValueError,TypeError):version=0
    active=False
    if not vps_only and isinstance(at,str):
        try:
            date=datetime.fromisoformat(at)
            active=(version>=313 and (datetime.now(timezone.utc)-date)<timedelta(minutes=12))
        except (ValueError,TypeError):pass
    return {
        "browser_collector_active":active,
        "browser_collector_enabled":not vps_only,
        "intake_mode":"vps_only" if vps_only else "browser",
        "collector_version":version,
        "upgrade_required":not vps_only and version<342,
        "required_version":342,
        "card_facts_supported":version>=341,
        "last_scan":at,
        "last_cycle_status":"disabled" if vps_only else info.get("status","not_started"),
        "last_error":"" if vps_only else info.get("error",""),
        "last_new_purchases":info.get("new_purchases",0),
        "last_originals_uploaded":info.get("originals_uploaded",0),
        "poll_interval_seconds":300,
        "search_requests_per_cycle":1,
        "max_procurements_per_cycle":2,
        "eis_source":"official EIS getDocsIP, VPS only" if vps_only else "official public 223-FZ search, browser only",
        "server_eis_requests":0,
    }

def install(ns):
    app=ns["app"]

    @app.post("/api/v310/eis223/card-metadata")
    async def verified_eis_card_metadata(request:Request):
        auth(request)
        if request.headers.get("origin")!="https://zakupki.gov.ru":
            raise HTTPException(403,"Только официальная вкладка ЕИС")
        try:version=int(request.headers.get("x-eis223-collector-version") or "0")
        except ValueError:version=0
        if version<313:raise HTTPException(426,"Обновите мост ЕИС до v313")
        raw=await request.body()
        if len(raw)>2500:raise HTTPException(413,"Слишком большой запрос")
        try:
            data=json.loads(raw)
            reg=_safe_string(data.get("reg_number"),20)
            u=urlsplit(_safe_string(data.get("source_page"),1000))
            q=parse_qs(u.query)
            if (not re.fullmatch(r"3[0-9]{10}",reg) or
                u.scheme!="https" or u.hostname!="zakupki.gov.ru" or
                u.port is not None or u.username or u.password or u.fragment or
                u.path not in (
                    "/223/purchase/public/purchase/info/common-info.html",
                    "/223/purchase/public/purchase/info/commoninfo.html",
                    "/epz/order/notice/notice223/common-info.html") or
                q.get("regNumber")!=[reg]):
                raise ValueError("Недопустимое подтверждение источника ЕИС")
            value={"reg_number":reg,
                   "customer":_safe_string(data.get("customer"),400),
                   "customer_inn":_safe_string(data.get("customer_inn"),12),
                   "title":_safe_string(data.get("title"),800)}
            if not (_real_customer(value["customer"]) or
                    re.fullmatch(r"(?:[0-9]{10}|[0-9]{12})",value["customer_inn"]) or
                    len(value["title"])>12):
                raise ValueError("В карточке нет новых подтверждённых сведений")
        except (AttributeError,ValueError,TypeError) as exc:
            raise HTTPException(422,str(exc)) from exc
        await asyncio.to_thread(_apply_customer,ns,value)
        print(f"V310 CARD_METADATA reg={reg} customer_confirmed={bool(_real_customer(value['customer']))} "
              f"inn_confirmed={bool(re.fullmatch(r'(?:[0-9]{10}|[0-9]{12})',value['customer_inn']))}",
              flush=True)
        return {"accepted":True,"reg_number":reg,
                "customer_stored":bool(_real_customer(value["customer"])),
                "independent_of_document_download":True}

    @app.post("/api/v299/eis223/public-manifest")
    async def public_manifest(request:Request):
        auth(request)
        if request.headers.get("origin")!="https://zakupki.gov.ru":
            raise HTTPException(403,"Use official public EIS browser session")
        if request.headers.get("content-length","").isdigit() and int(request.headers["content-length"])>MAX_MANIFEST_BYTES:
            raise HTTPException(413,"Manifest exceeds limit")
        raw=await request.body()
        if len(raw)>MAX_MANIFEST_BYTES:raise HTTPException(413,"Manifest exceeds limit")
        try:
            value=_parse_manifest(json.loads(raw))
        except (ValueError,TypeError,UnicodeDecodeError) as exc:
            raise HTTPException(422,str(exc)) from exc
        try:
            qty,changed=await asyncio.to_thread(_save_manifest,value,True)
        except AmendmentHistoryConflict as exc:
            raise HTTPException(409,str(exc)) from exc
        await asyncio.to_thread(_apply_customer,ns,value)
        enqueue=ns.get("_v295_enqueue_review")
        if changed and callable(enqueue):enqueue(value["reg_number"],force=True)
        print(f"V299 PUBLIC_MANIFEST reg={value['reg_number']} docs={qty} status=metadata_only",flush=True)
        return {"accepted":True,"reg_number":value["reg_number"],
                "documents_discovered":qty,"source":"ЕИС",
                "originals_verified_increment":0}

    @app.get("/api/v299/eis223/known")
    async def known(request:Request,regs:str=""):
        auth(request)
        seen=[]
        for candidate in regs.split(",")[:40]:
            candidate=candidate.strip()
            if REG.fullmatch(candidate) and candidate not in seen:seen.append(candidate)
        result=await asyncio.to_thread(_known,ns,seen)
        return {"items":result}

    @app.post("/api/v299/eis223/heartbeat")
    async def heartbeat(request:Request):
        auth(request)
        if request.headers.get("origin")!="https://zakupki.gov.ru":
            raise HTTPException(403,"Expected EIS browser origin")
        try:version=int(request.headers.get("x-eis223-collector-version") or "0")
        except ValueError:version=0
        if version<313:
            print(f"V299 COLLECTOR_OUTDATED version={version} required=313 action=update_browser_collector",flush=True)
            raise HTTPException(426,"Требуется обновлённый загрузчик ЕИС v313")
        raw=await request.body()
        if len(raw)>1500:raise HTTPException(413,"Heartbeat too large")
        try:
            body=json.loads(raw)
            if not isinstance(body,dict):raise ValueError("Expected object")
        except (ValueError,TypeError) as exc:raise HTTPException(422,"Invalid heartbeat") from exc
        vals={}
        for key in ("new_purchases","originals_uploaded"):
            value=body.get(key,0)
            vals[key]=max(0,min(100,int(value))) if str(value).isdigit() else 0
        status=str(body.get("status") or "")[:32]
        if status not in ("ok","rate_limited","fetch_error","collector_started","empty",
                          "stopped","no_downloadable_originals"):
            status="fetch_error"
        error=_safe_string(body.get("error"),240).replace("\n"," ")
        record={"last_scan":datetime.now(timezone.utc).isoformat(),
                "status":status,"error":error,"collector_version":version,**vals}
        path=root()/"eis223_public_browser_intake"/"_monitor_status.json"
        await asyncio.to_thread(save_atomic,path,json.dumps(record).encode())
        return {"received":True,**_scan_status()}

    @app.get("/api/v299/eis223/monitor-status")
    async def monitor_status():
        return _scan_status()

    @app.get("/api/v299/eis223/collector.js")
    async def collector_script():
        path=Path(__file__).with_name("v299_browser_collector.js")
        return PlainTextResponse(path.read_text(encoding="utf-8"),media_type="text/javascript",
                                 headers={"Cache-Control":"no-store"})

    print("V299 BROWSER_DISCOVERY_READY interval=300 search_per_cycle=1 purchases_per_cycle=2 only_eis=True",flush=True)
