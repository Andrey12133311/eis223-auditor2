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
    db=ns["conn"]()
    updated=0
    try:
        db.execute("BEGIN IMMEDIATE")
        rows=db.execute("""
            SELECT reg_number,customer,customer_inn
            FROM purchases
            WHERE customer IS NULL OR TRIM(customer)=''
              OR LOWER(customer) LIKE '%уточня%'
              OR LOWER(customer) LIKE '%не найден%'
              OR LOWER(customer) LIKE '%не указан%'
              OR LOWER(customer) LIKE 'наименование (полное и сокращ%'
              OR customer_inn IS NULL OR TRIM(customer_inn)=''
            LIMIT 20000
        """).fetchall()
        for reg,current,old_inn in rows:
            verified=db.execute("SELECT name,inn FROM v311_verified_buyers WHERE reg=?",(reg,)).fetchone()
            if verified and buyer(verified[0]):
                name,source_inn=buyer(verified[0]),legitimate_inn(verified[1])
            else:
                name,source_inn=card_from_storage(reg)
            inn=source_inn or legitimate_inn(old_inn)
            if not name and inn:
                row=db.execute("""
                    SELECT customer FROM purchases
                    WHERE customer_inn=? AND LENGTH(TRIM(customer))>6
                      AND LOWER(customer) NOT LIKE '%уточня%'
                      AND LOWER(customer) NOT LIKE '%не найден%'
                      AND LOWER(customer) NOT LIKE '%не указан%'
                      AND reg_number<>?
                    ORDER BY last_seen DESC LIMIT 1
                """,(inn,reg)).fetchone()
                if row:name=buyer(row[0])
            if not name:continue
            if buyer(current) and not PLACEHOLDER.search(str(current)) and not str(current).lower().startswith('наименование (полное и сокращ'):
                name=buyer(current)
            db.execute("""
                UPDATE purchases SET customer=?,
                    customer_inn=CASE WHEN ?<>'' THEN ? ELSE customer_inn END
                WHERE reg_number=?
            """,(name,inn,inn,reg))
            updated+=1
        db.commit()
        print(f"V311 BUYER_RECONCILED corrected={updated} reviewed={len(rows)}",
              flush=True)
        return {"reviewed":len(rows),"corrected":updated}
    except Exception:
        if db.in_transaction:db.rollback()
        logger.exception("Customer name reconciliation failed")
        return {"reviewed":0,"corrected":0}
    finally:db.close()

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
