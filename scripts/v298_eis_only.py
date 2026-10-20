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


def verified_xml_price(notice):
    """Read one documented lot price with its meaning, never arbitrary descendants.

    TFF 16.3 Appendix 2, pp. 21–22 distinguishes initialSum, commodityPrice,
    maxContractPrice and initialSumInfo. Unknown shapes and multiple lots are
    review-only, even when their numbers agree. This is not XSD validation.
    """
    if notice is None:return {}
    from v335_price_recovery import money
    from v324_official_price import UNIT_TOTAL_LABEL
    invalid_namespace=False
    def children(parent,name):
        nonlocal invalid_namespace
        nodes=[node for node in parent if tag(node)==name] if parent is not None else []
        if any(node.tag not in (name,"{http://zakupki.gov.ru/223fz/purchase/1}"+name) for node in nodes):
            invalid_namespace=True
        return nodes
    groups=children(notice,"lots")
    if len(groups)!=1:return {"price_kind":"unknown"}
    lots=children(groups[0],"lot")
    if len(lots)!=1:return {"price_kind":"unknown"}
    data=children(lots[0],"lotData")
    if len(data)!=1:return {"price_kind":"unknown"}
    data=data[0]
    if invalid_namespace:return {"price_kind":"unknown"}
    cancelled=children(lots[0],"cancelled")
    if invalid_namespace or len(cancelled)>1:return {"price_kind":"unknown"}
    if cancelled and (list(cancelled[0]) or (cancelled[0].text or "").strip().casefold() not in ("false","0")):
        return {"price_kind":"unknown"}
    names={"initialSum","commodityPrice","maxContractPrice","initialSumInfo","currency"}
    if any(tag(node) in names and node.tag not in (tag(node),
           "{http://zakupki.gov.ru/223fz/purchase/1}"+tag(node)) for node in data):
        return {"price_kind":"unknown"}
    currencies=children(data,"currency")
    if len(currencies)>1:return {"price_kind":"unknown"}
    if currencies:
        codes=[(node.text or "").strip().upper() for name in ("letterCode","digitalCode","code")
               for node in children(currencies[0],name) if not list(node)]
        if invalid_namespace or not codes or any(code not in ("RUB","RUR","643","810") for code in codes):
            return {"price_kind":"unknown"}
    infos=children(data,"initialSumInfo")
    if invalid_namespace or len(infos)>1:return {"price_kind":"unknown"}
    info=" ".join(" ".join(node.itertext()) for node in infos)
    # initialSumInfo is descriptive text in this supported format. Nested
    # fields are not a licence to search descendant names for a price.
    if any(list(node) for node in infos):return {"price_kind":"unknown"}
    records=[]
    for name,kind in (("initialSum","nmcd"),("commodityPrice","subject_cost"),
                      ("maxContractPrice","maximum_contract_value")):
        nodes=children(data,name)
        if len(nodes)>1:return {"price_kind":"unknown"}
        for node in nodes:
            parsed=money((node.text or "").strip())
            if list(node) or not parsed:return {"price_kind":"unknown"}
            records.append({"price":parsed[0],"price_num":parsed[1],
                            "price_kind":kind,"field_path":"lots/lot/lotData/"+name})
    if UNIT_TOTAL_LABEL.search(info):
        # The explicit unit-price qualification prevents an initialSum number
        # from being treated as contract NMCD, including inconsistent exports.
        return {**(records[0] if len(records)==1 else {}),"price_kind":"total_unit_prices"}
    kinds={item["price_kind"] for item in records}
    if "nmcd" in kinds and len(kinds)>1:return {"price_kind":"unknown"}
    if records:
        # A maximum value and a commodity price may coexist legitimately;
        # neither establishes the NMCD threshold. Retain the maximum for UI.
        preferred=[item for item in records if item["price_kind"]=="maximum_contract_value"] or records
        if len({(item["price"],item["price_kind"]) for item in preferred})!=1:
            return {"price_kind":"unknown"}
        return preferred[0]
    return {"price_kind":"unknown"}


def verified_xml_nmcd(notice):
    """Compatibility accessor: return only an explicit, unambiguous NMCD."""
    selected=verified_xml_price(notice)
    return selected.get("price","") if selected.get("price_kind")=="nmcd" else ""


def official_xml_documents(reg):
    """Read all supported XML identities from byte/hash-verified SOAP archives.

    Protocols remain protocol records; they never replace the procurement's
    current notice. Deduplicate identical XML across overlapping SOAP replies.
    The original ZIP, every filename and every XML revision remain on disk.
    """
    from eis223_soap_xml import parse_document
    if not REG.fullmatch(reg):return []
    folder=data_root()/"eis223_official_archive_inbox"/reg
    if not folder.is_dir():return []
    records={}
    for receipt_path in sorted(folder.glob("*.json")):
        try:
            receipt=json.loads(receipt_path.read_text(encoding="utf-8"))
            if receipt.get("reg_number")!=reg or receipt.get("source")!="official_eis_soap_archive":
                continue
            sha=receipt.get("archive_sha256","")
            if not re.fullmatch("[0-9a-f]{64}",sha):continue
            archive_path=folder/(sha+".zip")
            if not archive_path.is_file() or archive_path.stat().st_size>15*1024*1024:continue
            raw=archive_path.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=sha:continue
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                members=[i for i in archive.infolist() if not i.is_dir()]
                if (len(members)>100 or len({i.filename for i in members})!=len(members)
                        or sum(i.file_size for i in members)>50*1024*1024):continue
                names={i.filename:i for i in members}
                for entry in receipt.get("notices",[])[:100]:
                    if entry.get("reg_number")!=reg:continue
                    name=entry.get("xml_filename","")
                    info=names.get(name)
                    if info is None or info.file_size>15*1024*1024 or info.flag_bits&1:continue
                    xml=archive.read(info)
                    digest=hashlib.sha256(xml).hexdigest()
                    if digest!=entry.get("xml_sha256") or digest in records:continue
                    try:record,data=parse_document(xml,name)
                    except ValueError:continue
                    if record["reg_number"]==reg:
                        # Legal timing adapters need the full verified chain,
                        # not publication dates copied from receipt metadata.
                        record["archive_sha256"]=sha
                        records[digest]=(record,data)
        except (OSError,ValueError,KeyError,TypeError,AttributeError,zipfile.BadZipFile,
                RuntimeError,NotImplementedError):
            continue
    return list(records.values())


def official_notice_record(reg):
    """Keep the exact verified revision alongside its explicit legal context."""
    from eis223_soap_xml import revision_key
    notices=[item for item in official_xml_documents(reg) if item[0]["xml_family"]=="notice"]
    return max(notices,key=lambda item:revision_key(item[0])) if notices else (None,None)


def official_notice(reg):
    """Use the newest explicit notice revision, never ZIP/hash listing order."""
    return official_notice_record(reg)[1]


def official_notice_revisions(reg):
    """Read every distinct verified notice revision, without remote calls."""
    from eis223_soap_xml import revision_key
    notices=[item for item in official_xml_documents(reg) if item[0]["xml_family"]=="notice"]
    return [{"sha256":record["xml_sha256"],"version":record["version"],
             "xml_type":record["xml_type"],
             "publication":field(data,"publicationDateTime"),
             "submission_end":field(data,"submissionCloseDateTime"),
             "submission_start":field(data,"applSubmisionStartDate")}
            for record,data in sorted(notices,key=lambda item:revision_key(item[0]))]


def import_soap_metadata(ns,reg):
    """Persist the current XML card without invoking or clearing any audit.

    New rows are shared procurement metadata. This function never creates
    manual/account membership, updates result counters, or touches findings,
    documents, queue ownership or the user's saved report fields.
    """
    import sqlite3
    from datetime import datetime,timezone
    from urllib.parse import urlsplit
    from eis223_soap_xml import revision_key
    from v342_identity import valid_inn,subject
    from v311_verified_customer import _name
    from v335_price_recovery import money
    notices=[item for item in official_xml_documents(reg) if item[0]["xml_family"]=="notice"]
    if not notices:return {"stored":False,"reason":"notice_not_received"}
    record,notice=max(notices,key=lambda item:revision_key(item[0]))
    buyer=direct(direct(notice,"customer"),"mainInfo")
    values={"title":subject(field(notice,"name")),
            "customer":_name(field(buyer,"fullName") or field(buyer,"shortName")),
            "customer_inn":valid_inn(field(buyer,"inn")),
            "method":field(notice,"purchaseCodeName"),
            "published_at":field(notice,"publicationDateTime"),
            "submission_start":field(notice,"applSubmisionStartDate")[:10],
            "submission_end":field(notice,"submissionCloseDateTime")[:10]}
    values={key:value for key,value in values.items() if value}
    if values.get("submission_end"):values["deadline"]=values["submission_end"]
    if values.get("customer") and callable(ns.get("org_form")):
        values["org_form"]=ns["org_form"](values["customer"])
    xml_price=verified_xml_price(notice)
    parsed=money(xml_price.get("price"))
    if parsed:values.update(price=parsed[0],price_num=parsed[1])
    source_url=field(notice,"urlEIS")
    try:
        source=urlsplit(source_url)
        if (source.scheme=="https" and source.hostname=="zakupki.gov.ru"
                and source.port in (None,443) and not source.username and not source.password):
            values["url"]=source_url
    except ValueError:pass
    db=ns["conn"]();db.row_factory=sqlite3.Row
    try:
        db.execute("BEGIN IMMEDIATE")
        columns={row[1] for row in db.execute("PRAGMA table_info(purchases)")}
        row=db.execute("SELECT * FROM purchases WHERE reg_number=?",(reg,)).fetchone()
        previous=dict(row) if row is not None else {}
        try:card=json.loads(previous.get("card_fields_json") or "{}")
        except (ValueError,TypeError):card={}
        if not isinstance(card,dict):card={}
        prior=card.get("soap_metadata")
        if isinstance(prior,dict) and revision_key(prior)>=revision_key(record):
            db.commit()
            return {"stored":True,"changed":False,"reg_number":reg}
        card["soap_metadata"]={key:record[key] for key in (
            "xml_type","xml_sha256","xml_filename","xml_guid","version",
            "publication","created","modified")}
        card["soap_metadata"]["source"]="official_eis_getDocsIP"
        summary=card.get("summary") if isinstance(card.get("summary"),dict) else {}
        summary.update({key:value for key,value in values.items()
                        if key in ("title","customer","method","price","price_num",
                                   "submission_start","submission_end")})
        if xml_price:
            summary["price_kind"]=xml_price["price_kind"]
            card["soap_metadata"]["price"]=xml_price
        card["summary"]=summary
        values["card_fields_json"]=json.dumps(card,ensure_ascii=False)
        if row is None:
            values.update(reg_number=reg,source="official_eis_getDocsIP",
                          last_seen=datetime.now(timezone.utc).isoformat(),
                          card_source_status="metadata_only")
            values={key:value for key,value in values.items() if key in columns}
            db.execute("INSERT INTO purchases ("+",".join(values)+") VALUES ("+
                       ",".join("?" for _ in values)+")",tuple(values.values()))
        else:
            values={key:value for key,value in values.items() if key in columns}
            db.execute("UPDATE purchases SET "+",".join(key+"=?" for key in values)+
                       " WHERE reg_number=?",(*values.values(),reg))
        db.commit()
        return {"stored":True,"changed":True,"reg_number":reg}
    except Exception:
        if db.in_transaction:db.rollback()
        raise
    finally:db.close()


# Federal five-day working calendar based on Labour Code art. 112 and
# Government transfer decrees 1314 (2024), 1335 (2025), 1466 (2026).
# Regional non-working days are outside this federal calendar.
def federal_workday(day):
    from datetime import date, timedelta
    if day.year not in (2024,2025,2026):
        return None
    holiday={(1,d) for d in range(1,9)}
    holiday.update({(2,23),(3,8),(5,1),(5,9),(6,12),(11,4)})
    closed={
        2024:{"2024-04-29","2024-04-30","2024-05-10","2024-12-30","2024-12-31"},
        2025:{"2025-05-02","2025-05-08","2025-06-13","2025-11-03","2025-12-31"},
        2026:{"2026-01-09","2026-12-31"},
    }
    working_saturdays={
        2024:{"2024-04-27","2024-11-02","2024-12-28"},
        2025:{"2025-11-01"},
        2026:set(),
    }
    iso=day.isoformat()
    if iso in working_saturdays[day.year]:return True
    if iso in closed[day.year] or (day.month,day.day) in holiday:return False
    if day.weekday()>=5:return False
    # Holiday on a weekend moves to the next weekday, except January 1-8.
    for month,daynum in ((2,23),(3,8),(5,1),(5,9),(6,12),(11,4)):
        holiday_day=date(day.year,month,daynum)
        if holiday_day.weekday()<5:continue
        if day.year==2025 and (month,daynum) in ((2,23),(3,8)):continue
        replacement=holiday_day+timedelta(days=1)
        while replacement.weekday()>=5 or (replacement.month,replacement.day) in holiday:
            replacement+=timedelta(days=1)
        if day==replacement:return False
    return True

def federal_workdays_between(start,end):
    from datetime import timedelta
    if start.year not in (2024,2025,2026) or end.year not in (2024,2025,2026):
        return None
    if end<start or (end-start).days>3660:return None
    return sum(federal_workday(start+timedelta(days=i))
               for i in range(1,(end-start).days+1))

def stored_purchase(ns,reg):
    db=ns["conn"]()
    try:
        row=db.execute("SELECT * FROM purchases WHERE reg_number=?",(reg,)).fetchone()
        return dict(row) if row else {}
    finally:db.close()


def _read_original(reg,uid):
    """Read/hash saved bytes outside the event loop under the shared cap."""
    stored=saved_document(reg,uid)
    if stored is None:return None
    path,receipt=stored
    with path.open("rb") as original:
        digest=hashlib.file_digest(original,"sha256").hexdigest()
    if digest!=receipt["sha256"]:return None
    return path,receipt,digest


async def saved_review_context(ns,reg,docs):
    """Refresh source metadata while reusing receipt-authenticated documents."""
    return await offline_audit(ns,reg,collect_only=True,_collected_docs=docs)


async def offline_audit(ns,reg,collect_only=False,*,_collected_docs=None):
    """Use only local official XML and saved original bytes, no remote I/O.

    collect_only=True is for the comprehensive local-document legal sweep:
    return verified source documents without saving, removing, or replacing
    any existing database rows, findings, or original bytes. The internal
    _collected_docs path reuses documents already authenticated by stored_docs;
    it refreshes the same source context without extracting those bytes twice.
    """
    if _collected_docs is not None and not collect_only:
        raise ValueError("Precollected documents are only available for local review")
    from v381_audit_scheduling import run_file_io
    if not REG.fullmatch(reg):raise ValueError("Invalid official registry number")
    allowed=await run_file_io(allowed_documents,reg)
    if not allowed:raise ValueError("Verified EIS XML has no documents")
    notice_record,notice=await asyncio.to_thread(official_notice_record,reg)
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

    # A verified SOAP notice may coexist with browser-captured clarification
    # and cancellation events. Validate its registry number before merging.
    if notice is not None:
        try:
            manifest_path=data_root()/"eis223_public_browser_intake"/reg/"manifest.json"
            manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
            if (isinstance(manifest,dict) and
                manifest.get("source")=="public_eis_223_browser" and
                manifest.get("reg_number")==reg):
                public={key:manifest[key] for key in ("clarifications","cancellations")
                        if isinstance(manifest.get(key),list)}
        except (OSError,ValueError,TypeError):
            pass
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
    # Official-card price is supplementary verified evidence, used only when
    # official SOAP lot, validated manifest and stored purchase lack NMCD.
    from v311_verified_customer import read_verified_price
    verified_price=await asyncio.to_thread(read_verified_price,ns,reg)
    from v335_price_recovery import money
    xml_price=verified_xml_price(notice)
    try:saved_card=json.loads(previous.get("card_fields_json") or "{}")
    except (ValueError,TypeError):saved_card={}
    summary=(saved_card.get("summary") or {}) if isinstance(saved_card,dict) else {}
    if not isinstance(summary,dict):summary={}
    from v341_eis_facts import saved_price_kind
    saved_kind=saved_price_kind(saved_card,reg) or previous.get("price_kind")
    candidates=((xml_price.get("price"),xml_price.get("price_kind")),
                (public.get("price"),public.get("price_kind")),
                (previous.get("price"),saved_kind),(previous.get("price_num"),saved_kind),
                (verified_price.get("price"),verified_price.get("price_kind")))
    selected=next(((money(value),kind) for value,kind in candidates if money(value)),(None,None))
    parsed,price_kind=selected
    # An explicit current SOAP meaning survives a missing amount or stale DB
    # fallback. Equal original numbers never promote it to another price kind.
    if xml_price:price_kind=xml_price.get("price_kind")
    price,num=parsed if parsed else ("",None)
    p={"reg":reg,
       "title":reg_name,
       "customer":customer,
       "customer_inn":customer_inn,
       "method":field(notice,"purchaseCodeName") or previous.get("method") or "",
       "price":price,
       "price_kind":price_kind,
       "price_num":num if num is not None else (previous.get("price_num")
                     if previous.get("price_num") is not None else verified_price.get("price_num")),
       "published":published or previous.get("published_at") or "",
       "submission_start":start or previous.get("submission_start") or "",
       "submission_end":end or previous.get("submission_end") or "",
       "deadline":end or previous.get("deadline") or "",
       "url":ns.get("old_eis_url",lambda r:"https://zakupki.gov.ru/223/purchase/public/purchase/info/common-info.html?regNumber="+r)(reg)}

    if notice_record is not None:
        # This in-memory marker comes only from the current hash-verified XML,
        # never from stored card JSON. Later wrappers must not replace these
        # facts with an older card or silently resolve conflicting revisions.
        fields={"method":field(notice,"purchaseCodeName"),
                "published":field(notice,"publicationDateTime"),
                "submission_start":field(notice,"applSubmisionStartDate")[:10],
                "submission_end":field(notice,"submissionCloseDateTime")[:10]}
        p["_current_soap_context"]={"record":notice_record,
            "fields":{key:value for key,value in fields.items() if value},
            "price":xml_price}

    extractor=ns.get("extract_text")
    if extractor is None and _collected_docs is None:
        raise RuntimeError("Existing original-document text extractor unavailable")
    # The incoming and repeated audit must see the same evidence order.
    # The legacy DOCX extractor appends every table after all paragraphs,
    # breaking dated request/reply pairs and dropping otherwise readable
    # bodies when an unrelated styles/image part is missing.
    from v314_eis_metadata import document_text
    def extract_original(path):
        return document_text(path,extractor)
    docs=list(_collected_docs) if _collected_docs is not None else []
    dedup_by_sha={}
    for uid,item in (allowed.items() if _collected_docs is None else ()):
        stored=await run_file_io(_read_original,reg,uid)
        if stored is None:continue
        path,receipt,sha=stored
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
            members,warnings=await run_file_io(
                extract_members,path,reg,uid,data_root(),extract_original,timeout=160)
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
        extraction_error=None
        try:
            text=await run_file_io(extract_original,path,timeout=45)
        except Exception as exc:
            # One unreadable original must not prevent the remaining saved
            # documents from reaching the legal engine. Cancellation still
            # propagates; failed text never receives status='checked'.
            text=""
            extraction_error="Не удалось извлечь текст оригинала ("+type(exc).__name__+")"
            print(f"V298 ORIGINAL_TEXT_UNREADABLE reg={reg} uid={uid} "
                  f"type={type(exc).__name__}",flush=True)
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
            "error":None if valid else extraction_error or "Оригинал сохранён; текст не извлечён",
            "final_url":item["url"],
        })

    # Original URLs without downloaded content are deliberately NOT retried.
    # Show incomplete intake as waiting, never as an audited original.
    if not docs:raise ValueError("No readable original files have been uploaded from EIS")

    # Preserve protocol publication facts from the exact verified SOAP XML.
    # Keep legacy `published` blank: a general notice/archive timestamp must
    # never silently activate unrelated old metadata-based legal rules.
    from official_publication_evidence import annotate_protocol_publications
    await asyncio.to_thread(annotate_protocol_publications,reg,docs)

    # Public EIS manifests from older collectors may contain only a status
    # instead of the subject and no buyer. Read explicit buyer fields from
    # the verified originals before analysis and the first database save.
    from v314_eis_metadata import from_documents, subject
    from v311_verified_customer import _name
    metadata=await asyncio.to_thread(from_documents,reg,docs,_name(p.get("customer")))
    if metadata:
        if not _name(p["customer"]):p["customer"]=metadata.get("customer") or ""
        if not p["customer_inn"]:p["customer_inn"]=metadata.get("customer_inn") or ""
        if not subject(p["title"]):p["title"]=metadata.get("title") or p["title"]
        # Before the audit is saved, recover the missing numeric NMCD from a
        # hash-verified original notice. Never infer it from the winner's price.
        if (not str(p.get("price") or "").strip() or
            str(p.get("price")).strip() in ("—","-","нет данных","не установлена")):
            if metadata.get("price_num") is not None:
                p["price"]=metadata["price"]
                p["price_num"]=metadata["price_num"]
                if not p.get("price_kind"):
                    p["price_kind"]=metadata.get("price_kind") or "unknown"
        elif p.get("price_num") is None and metadata.get("price_num") is not None:
            try:
                from v311_verified_customer import _verified_money
                if _verified_money(p["price"])[1]==metadata["price_num"]:
                    p["price_num"]=metadata["price_num"]
            except ValueError:
                pass

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
                  "price_num":p["price_num"],"price_kind":p.get("price_kind")},
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
    if collect_only:
        return {"p":p,"docs":docs,"card":card,"detail":detail,
                "originals_expected":len(allowed),
                "originals_received":sum(bool(saved_document(reg,uid))
                                         for uid in allowed),
                "source":"verified_official_eis_locals"}
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
    def safe_day(value):
        result=as_day(value)
        return result if result and 2000<=result.year<=2100 else None
    opening=safe_day(p.get("submission_start"))
    closing=safe_day(p.get("submission_end"))
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
            r"[^.;]{0,100}?(\d{2}[.]\d{2}[.]\d{4})",body,_legal_re.I))
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
    # Extract national-regime evidence from checked originals as well as XML.
    # This is not a statutory violation without product-code classification.
    for doc in docs:
        if doc.get("status")!="checked":continue
        body=" ".join(str(doc.get("text") or "").split())
        for phrase in ("запрет допуска товаров иностранного происхождения",
                       "ограничение допуска товаров иностранного происхождения",
                       "преимущество товарам российского происхождения"):
            pos=body.casefold().find(phrase)
            if pos<0:continue
            checks.append({"code":"national_regime_document_review",
                           "status":"check",
                           "title":"Проверить национальный режим в документации",
                           "document":doc.get("name") or "",
                           "evidence":body[max(0,pos-80):pos+len(phrase)+100],
                           "legal_basis":"223-ФЗ, статья 3.1-4",
                           "reason":"Сопоставить с XML ЕИС, кодом ОКПД2, датой и исключениями"})
    # Revisions are source-backed and deduplicated by actual XML SHA256.
    revisions=await asyncio.to_thread(official_notice_revisions,reg)
    if len(revisions)>1:
        deadlines={v["submission_end"][:10] for v in revisions if v["submission_end"]}
        if len(deadlines)>1:
            checks.append({"code":"eis_notice_revision_deadline_change",
                           "status":"check",
                           "title":"Срок подачи заявок менялся в редакциях ЕИС",
                           "document":"Официальные XML-редакции извещения",
                           "evidence":"; ".join(sorted(deadlines)),
                           "legal_basis":"223-ФЗ, статья 4",
                           "reason":"Сопоставить действующую редакцию и даты публикации изменений"})
    # National regime: compare the same explicit clause across the notice
    # and verified originals. Absence alone is not an infringement.
    if notice is not None:
        xml_flags=set()
        for node in notice.iter():
            key=tag(node).casefold()
            if key in ("prohibitionnationalregime","restrictionnationalregime",
                       "preferencenationalregime") and str(node.text or "").strip().lower()=="true":
                xml_flags.add(key)
        for doc in docs:
            if doc.get("status")!="checked":continue
            body=" ".join(str(doc.get("text") or "").lower().split())
            pairs=(("prohibitionnationalregime","запрет допуска товаров иностранного происхождения"),
                   ("restrictionnationalregime","ограничение допуска товаров иностранного происхождения"))
            for flag,phrase in pairs:
                if phrase not in body or flag in xml_flags:continue
                checks.append({"code":"national_regime_notice_document_discrepancy",
                               "status":"check",
                               "title":"Условие национального режима есть в документе, но не отмечено в XML",
                               "document":doc.get("name") or "",
                               "evidence":phrase,
                               "legal_basis":"223-ФЗ, статья 4, часть 8",
                               "reason":"Проверить применимость режима и действующую редакцию извещения"})
    if notice is not None:
        regime_flags=[tag(node) for node in notice.iter()
                      if tag(node).casefold() in ("prohibitionnationalregime",
                      "restrictionnationalregime","preferencenationalregime")
                      and str(node.text or "").strip().lower()=="true"]
        if regime_flags:
            codes=sorted({str(node.text).strip() for node in notice.iter()
                          if tag(node).casefold() in ("okpd2","okpd2code","okpdcode")
                          and node.text})
            checks.append({"code":"national_regime_exception_qualification",
                           "status":"check",
                           "title":"Проверка исключений национального режима",
                           "document":"Официальное извещение ЕИС",
                           "evidence":"Признаки: "+", ".join(sorted(set(regime_flags)))+
                                      "; ОКПД2: "+(", ".join(codes[:20]) if codes else "не указан"),
                           "legal_basis":"223-ФЗ, статья 3.1-4",
                           "reason":"Сопоставить нормативный акт, код товара и применимые исключения"})
    # 1875 exception screening: positive documentary clues trigger review,
    # not automatic exemption. A claim alone is not proof of applicability.
    if notice is not None:
        exceptions=(
            ("prior_contract","исполнения договора, заключенного до 1 января 2025"),
            ("defence_repair","ремонта, модернизации, технического обслуживания вооружения"),
            ("special_investment","специального инвестиционного контракта"),
            ("no_domestic_equivalent","отсутствие на территории российской федерации производства"),
        )
        for doc in docs:
            if doc.get("status")!="checked":continue
            body=" ".join(str(doc.get("text") or "").casefold().split())
            for key,phrase in exceptions:
                pos=body.find(phrase)
                if pos<0:continue
                checks.append({"code":"national_regime_exception_"+key,
                               "status":"check",
                               "title":"Проверить заявленное исключение национального режима",
                               "document":doc.get("name") or "",
                               "evidence":body[max(0,pos-90):pos+len(phrase)+130],
                               "legal_basis":"Постановление Правительства РФ № 1875, пункт 4",
                               "reason":"Проверить документальное основание, применимость к конкретному лоту и редакцию акта на дату закупки"})
    # An exception is not automatically applicable when product identity,
    # procurement date or documentary proof is missing.
    if notice is not None:
        regime_nodes=[node for node in notice.iter()
                      if tag(node).casefold() in ("prohibitionnationalregime",
                        "restrictionnationalregime","preferencenationalregime")
                      and str(node.text or "").strip().lower()=="true"]
        if regime_nodes:
            product_codes=sorted({str(node.text).strip() for node in notice.iter()
                 if tag(node).casefold() in ("okpd2","okpd2code","okpdcode")
                 and node.text})
            if not product_codes:
                checks.append({"code":"national_regime_missing_product_classification",
                               "status":"check",
                               "title":"Нельзя установить применимость исключения без ОКПД2",
                               "document":"Официальный XML извещения",
                               "evidence":"В XML выявлены признаки национального режима, код ОКПД2 не извлечён",
                               "legal_basis":"Постановление Правительства РФ № 1875",
                               "reason":"Уточнить ОКПД2 по позициям лота и перечни на дату публикации"})
    # Decree 1875 p.5(a): absence of Russian production must be supported by
    # a Minpromtorg permit issued before procurement, not just a statement.
    for doc in docs:
        if doc.get("status")!="checked":continue
        body=" ".join(str(doc.get("text") or "").casefold().split())
        claim="отсутствие на территории российской федерации производства"
        if claim not in body:continue
        permit=bool(re.search(r"(?:разрешени[ея] минпромторга|разрешени[ея] на закупку иностранного товара)",body))
        checks.append({"code":"national_regime_minpromtorg_permit_review",
                       "status":"check",
                       "title":"Проверить разрешение Минпромторга для исключения",
                       "document":doc.get("name") or "",
                       "evidence":("Упоминание разрешения найдено" if permit else
                                   "В документе не обнаружено упоминание разрешения"),
                       "legal_basis":"Постановление Правительства РФ № 1875, пункт 5, подпункт а",
                       "reason":"Проверить оригинал разрешения, дату выдачи до начала закупки и позицию приложения № 1"})
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
        if request_day>closing or answer_day<request_day:continue
        if (closing-request_day).days>3660 or (answer_day-request_day).days>3660:continue
        weekdays_before=federal_workdays_between(request_day,closing)
        if weekdays_before is None:continue
        if weekdays_before<3:continue
        # Holiday calendars and legally transferred workdays may differ from weekdays.
        elapsed_weekdays=federal_workdays_between(request_day,answer_day)
        if elapsed_weekdays is None:continue
        if elapsed_weekdays>3:
            checks.append({"code":"clarification_three_workdays_review","status":"check",
                "title":"Возможное нарушение срока размещения разъяснения",
                "document":"Сведения ЕИС об изменениях и разъяснениях",
                "evidence":f"Запрос: {request_day}; публикация: {answer_day}; рабочих дней по федеральному календарю: {elapsed_weekdays}",
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
    from v381_audit_scheduling import notify_evidence
    notify_evidence(ns)
    if metadata:
        # Older save implementations omit customer_inn; retain the verified
        # value and source after saving without replacing docs or findings.
        from v314_eis_metadata import apply_metadata
        await asyncio.to_thread(apply_metadata,ns,reg,metadata)
    checked=sum(d.get("status")=="checked" for d in docs)
    # A confirmed checklist rule is a violation too. Preserve the existing
    # `findings` API field, but expose the reconciled legal event count so
    # a run with checklist violations cannot be misread as zero violations.
    from v352_dashboard_truth import collapse_legal_events
    checklist_violations=[{**c,"title":c.get("item") or c.get("title"),
                           "code":c.get("field_path") or c.get("code")}
                          for c in checks if c.get("status")=="violation"]
    confirmed=len(collapse_legal_events([*findings,*checklist_violations]))
    print(f"V298 OFFLINE_AUDIT reg={reg} documents={len(docs)} checked={checked} "
          f"findings={len(findings)} checks={len(checks)} "
          f"checklist_violations={len(checklist_violations)} confirmed_violations={confirmed} "
          "remote_requests=0",flush=True)
    return {"reg":reg,"documents":len(docs),"checked_documents":checked,
            "findings":len(findings),"checks":len(checks),
            "checklist_violations":len(checklist_violations),
            "confirmed_violations":confirmed,
            "source":"official_eis_getDocsIP" if notice is not None else "public_eis_223_browser",
            "remote_requests":0}


def install(ns):
    """Switch off all historic background collectors and source-triggering routes."""
    vps_only=os.environ.get("EIS223_SOURCE_MODE")=="OFFICIAL_EIS_VPS_ONLY"
    if not vps_only:
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
    ns["_v379_import_soap_metadata"]=lambda reg: import_soap_metadata(ns,reg)
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
                                 "detail":"Источник отключён. Документы ЕИС загружает VPS." if vps_only
                                          else "Источник отключён. Работает только официальный ЕИС через браузер."},
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
        collector={"active":False,"last_seen":None,"status":"not_connected"}
        try:
            from eis_server_worker import health
            collector=await asyncio.to_thread(health,ns)
        except (ImportError,OSError,RuntimeError):
            pass
        active=bool(collector.get("active"))
        return {"mode":"official_eis_vps_only" if vps_only else
                        ("official_eis_server_and_browser" if active else "official_eis_browser_only"),
                "sources":["ЕИС getDocsIP (VPS)"] if vps_only else
                          (["ЕИС getDocsIP (VPS)","ЕИС (браузерное подключение)"] if active
                           else ["ЕИС (браузерное подключение)"]),
                "browser_collector_enabled":not vps_only,
                "server_collector":collector,
                "server_collector_configured":len(os.environ.get("EIS223_UPLOAD_TOKEN", ""))>=24,
                "legacy_background_workers":0,
                "legacy_failed_requests_scheduled":0,
                "official_imported_procurements":regs,
                "existing_data_preserved":True,
                "direct_eis_server_requests":False,
                "vps_eis_requests":active}

    print("V298 EIS_ONLY_ACTIVE "+json.dumps({
        "remaining_hooks":[getattr(x,"__name__","?") for x in kept],
        "disabled_start_hooks":disabled,
        "legacy_sources_enabled":0,
        "offline_document_audit":True,
        "historical_data_preserved":True,
    },ensure_ascii=False),flush=True)
