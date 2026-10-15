"""Resolve displayed EIS buyer names from verified local official metadata.

Never invent a company name, never use the procurement title as a buyer, and
never count missing attachments as checked. A confirmed buyer can be shown
independently of the (possibly unavailable) procurement file download.
"""
from __future__ import annotations
import asyncio
import json
import logging
import re
from pathlib import Path

from v295_eis_originals import root

logger=logging.getLogger(__name__)
INN=re.compile(r"^(?:\d{10}|\d{12})$")
PLACEHOLDER=re.compile(
    r"уточня|заказчик не найден|заказчик не указан|сведения отсутствуют|^неизвестно$",
    re.I)

def buyer(value):
    s=" ".join(str(value or "").split())[:400]
    if len(s)<5 or PLACEHOLDER.search(s) or s.isdigit():
        return ""
    return s

def legitimate_inn(value):
    value=str(value or "").strip()
    return value if INN.fullmatch(value) else ""

def card_from_storage(reg):
    folder=root()/"eis223_public_browser_intake"/reg
    for filename,source in [
        ("buyer.json","public_eis_223_browser_card"),
        ("manifest.json","public_eis_223_browser")]:
        try:
            r=json.loads((folder/filename).read_text(encoding="utf8"))
            if (r.get("reg_number")==reg and r.get("source")==source):
                name=buyer(r.get("customer"))
                inn=legitimate_inn(r.get("customer_inn"))
                if name or inn:return name,inn
        except (OSError,ValueError,TypeError,AttributeError):
            continue
    return "",""

def reconcile_buyers(ns):
    from v311_verified_customer import _name, read_verified
    from v299_browser_intake import _apply_customer
    from v342_identity import subject, valid_inn
    db=ns["conn"]()
    try:
        rows=db.execute("SELECT reg_number,customer,customer_inn,title FROM purchases "
                        "ORDER BY last_seen DESC LIMIT 20000").fetchall()
    finally:
        db.close()
    updated=reviewed=0
    for reg,current,old_inn,title in rows:
        if (_name(current) and _name(current)==current and valid_inn(old_inn) and subject(title)):
            continue
        reviewed+=1
        try:
            trusted=read_verified(ns,reg)
            name=_name(trusted.get("name"))
            inn=valid_inn(trusted.get("inn"))
            stored_title=""
            folder=root()/"eis223_public_browser_intake"/reg
            for filename,source in (("buyer.json","public_eis_223_browser_card"),
                                    ("manifest.json","public_eis_223_browser")):
                try:
                    value=json.loads((folder/filename).read_text(encoding="utf-8"))
                    if value.get("reg_number")!=reg or value.get("source")!=source:
                        continue
                    from v311_verified_customer import _official_source
                    if not _official_source(value.get("source_page"),reg):
                        continue
                    name=name or _name(value.get("customer"))
                    inn=inn or valid_inn(value.get("customer_inn"))
                    stored_title=stored_title or subject(value.get("title"))
                except (OSError,ValueError,TypeError,AttributeError):
                    continue
            updated+=bool(_apply_customer(ns,{"reg_number":reg,"customer":name,
                "customer_inn":inn,"title":stored_title}))
        except Exception:
            logger.exception("Customer reconciliation failed reg=%s",reg)
    print(f"V342 BUYER_RECONCILED corrected={updated} reviewed={reviewed}",flush=True)
    return {"reviewed":reviewed,"corrected":updated}


def install(ns):
    app=ns["app"]

    @app.on_event("startup")
    async def resolve_official_customer_names():
        await asyncio.to_thread(reconcile_buyers,ns)
        async def periodic_backfill():
            while True:
                await asyncio.sleep(300)
                await asyncio.to_thread(reconcile_buyers,ns)
        asyncio.create_task(periodic_backfill())

    @app.get("/api/v311/buyer-status")
    async def buyer_status():
        # Read-only support endpoint. Do not expose an editable company name.
        db=ns["conn"]()
        try:
            pending=db.execute("""
                SELECT COUNT(*) FROM purchases WHERE customer IS NULL
                  OR TRIM(customer)='' OR LOWER(customer) LIKE '%уточня%'
            """).fetchone()[0]
            return {"pending_official_name":int(pending),
                    "source":"verified ЕИС card/manifest or same verified INN",
                    "note":"Нельзя выдавать непроверенное название за официальный источник"}
        finally:db.close()
