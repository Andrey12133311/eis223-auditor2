"""Immediate, source-grounded customer-name backfill for saved 223-FZ audits.

Never infer an organization's name from the procurement subject or INN alone.
Use exact EIS XML, previously saved official browser manifests, and historical
purchases with the same validated INN; do not overwrite known organization names.
"""
from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from pathlib import Path

from v295_eis_originals import root

INN=re.compile(r"(?:[0-9]{10}|[0-9]{12})$")

def real_name(value):
    name=" ".join(str(value or "").split())
    if len(name)<5 or len(name)>400 or name.isdigit():return ""
    if re.search(r"^(?:уточняем заказчика|заказчик уточняется|не указано|нет данных|сведения отсутствуют)$",
                 name,re.I):return ""
    return name

def from_manifest(reg):
    try:
        p=root()/"eis223_public_browser_intake"/reg/"manifest.json"
        data=json.loads(p.read_text(encoding="utf8"))
        if data.get("source")=="public_eis_223_browser" and data.get("reg_number")==reg:
            return real_name(data.get("customer")),str(data.get("customer_inn") or "")
    except (OSError,ValueError,TypeError,AttributeError):pass
    return "",""

def from_official_archive(reg):
    try:
        from v298_eis_only import official_notice,direct,field
        notice=official_notice(reg)
        if notice is not None:
            customer=direct(direct(notice,"customer"),"mainInfo")
            return real_name(field(customer,"fullName") or field(customer,"shortName"))
    except Exception:pass
    return ""

def rebuild(ns):
    db=ns["conn"]()
    db.row_factory=sqlite3.Row
    fixed=0
    missing=0
    try:
        rows=db.execute("""
            SELECT reg_number,customer,customer_inn,title
              FROM purchases
             WHERE customer IS NULL OR LENGTH(TRIM(customer))<5
                 OR LOWER(TRIM(customer)) LIKE 'уточняем%'
                 OR LOWER(TRIM(customer)) LIKE '%уточняется%'
        """).fetchall()
        for r in rows[:3500]:
            reg=str(r["reg_number"])
            name,from_inn=from_manifest(reg)
            if not name:name=from_official_archive(reg)
            inn=str(r["customer_inn"] or "").strip()
            if not INN.fullmatch(inn) and INN.fullmatch(from_inn):
                inn=from_inn
            if not INN.fullmatch(inn):
                # Only a unique official customer-INN search match qualifies.
                try:
                    ids=db.execute("SELECT DISTINCT inn FROM v301_inn_purchases WHERE reg=? LIMIT 2",
                                   (reg,)).fetchall()
                    if len(ids)==1 and INN.fullmatch(ids[0][0]):inn=ids[0][0]
                except sqlite3.OperationalError:
                    pass
            if not name and INN.fullmatch(inn):
                known=db.execute("""
                    SELECT customer FROM purchases
                     WHERE customer_inn=? AND customer IS NOT NULL
                        AND LENGTH(TRIM(customer))>=5
                        AND LOWER(customer) NOT LIKE '%уточня%'
                     ORDER BY last_seen DESC LIMIT 1
                """,(inn,)).fetchone()
                if known:name=real_name(known[0])
            if name:
                db.execute("""
                    UPDATE purchases
                       SET customer=?,customer_inn=CASE WHEN ?<>'' THEN ? ELSE customer_inn END
                     WHERE reg_number=? AND
                      (customer IS NULL OR LENGTH(TRIM(customer))<5
                       OR LOWER(customer) LIKE '%уточня%')
                """,(name,inn,inn,reg))
                fixed+=1
            else:
                missing+=1
                # Persist an INN found from the user's validated EIS query,
                # even when there is no verified name yet.
                if INN.fullmatch(inn):
                    db.execute("""UPDATE purchases SET customer_inn=?
                        WHERE reg_number=? AND (customer_inn IS NULL OR customer_inn='')""",
                        (inn,reg))
        db.commit()
    except Exception:
        if db.in_transaction:db.rollback()
        raise
    finally:db.close()
    print(f"V311 CUSTOMER_BACKFILL saved_names={fixed} source_unavailable={missing}",flush=True)
    return fixed,missing

def install(ns):
    app=ns["app"]

    @app.on_event("startup")
    async def fill_verified_customer_names():
        try:
            await asyncio.to_thread(rebuild,ns)
        except Exception as exc:
            print("V311 CUSTOMER_BACKFILL_FAILED "+type(exc).__name__,flush=True)

    print("V311 CUSTOMER_NAME_RESOLUTION_READY sources=eis_manifest,eis_xml,cached_inn",flush=True)
