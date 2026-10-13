"""Reliable, immediate customer metadata for 223-FZ procurements.

Do not infer customer INN or name from a manual search query, a procurement
organizer, an arbitrary file or a placeholder. Accept independently validated
official EIS procurement-card metadata as soon as its card is fetched, BEFORE
attempting to download attachments. Keeps evidence and name provenance.

Historical targeted repairs below are confirmed by public procurement register
mirrors. They are metadata ONLY: no invented original files/audit findings.
"""
from __future__ import annotations
import asyncio
from datetime import datetime,timezone
import json
import re
import sqlite3
from urllib.parse import urlsplit,parse_qs

from fastapi import HTTPException,Request
from v295_eis_originals import auth

REG=re.compile(r"^3\d{10}$")
INN=re.compile(r"^(?:\d{10}|\d{12})$")
CARD_PATHS=frozenset((
    "/223/purchase/public/purchase/info/common-info.html",
    "/epz/order/notice/notice223/common-info.html",
    "/223/purchase/public/purchase/info/documents.html",
    "/epz/order/notice/notice223/documents.html",
))

# Correct documented misattributions: manual search by INN 6658017389 must
# NEVER assign that buyer to unrelated 223-FZ registry numbers.
CONFIRMED={
    "32616280442":{
        "name":'Публичное акционерное общество энергетики и электрификации «Камчатскэнерго»',
        "inn":"4100000668",
        "source_url":"https://goszakupki.pro/tender-223/1123661",
    },
    "32616461370":{
        "name":'Федеральное бюджетное учреждение здравоохранения «Центр гигиены и эпидемиологии в Камчатском крае»',
        "inn":"4101100055",
        "source_url":"https://zakupki.kontur.ru/32616461370",
    },
    "32616417059":{
        "name":'Акционерное общество «Ресурсоснабжение»',
        "inn":"4100052419",
        "source_url":"https://zakupki.kontur.ru/32616417059",
    },
}

def _name(value):
    value=" ".join(str(value or "").replace("\x00"," ").split())
    if not 6<=len(value)<=400 or not re.search(r"[А-Яа-яЁёA-Za-z]",value):return ""
    if re.search(r"^(?:уточняем заказчика|заказчик уточняется|уточняется|не указан|нет данных|организация|заказчик|unknown)\b",value,re.I):return ""
    if re.fullmatch(r"[\d\W]+",value):return ""
    # EIS sometimes provides entire legal names in uppercase; preserve legal
    # entity abbreviations while rendering a normally cased company name.
    alpha=[c for c in value if c.isalpha()]
    if alpha and sum(c.isupper() for c in alpha)>=int(.85*len(alpha)):
        abbreviations=("ООО","ПАО","АО","ОАО","ЗАО","ФГУП","ФГБУ",
                       "ФБУЗ","ГБУ","МБУ","МУП","АНО","ФГБОУ")
        safe=value.lower()
        safe=safe[0].upper()+safe[1:]
        safe=re.sub(r"«([а-яё])",lambda m:"«"+m[1].upper(),safe)
        safe=re.sub(r"(?<![\w-])(?:"+ "|".join(x.lower() for x in abbreviations)+r")(?![\w-])",
                    lambda m:m[0].upper(),safe)
        value=safe
    return value

def _db(ns):
    db=ns["conn"]()
    db.row_factory=sqlite3.Row
    return db

def _initialize(ns):
    db=_db(ns)
    try:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS v311_verified_buyers(
          reg TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          inn TEXT NOT NULL DEFAULT '',
          evidence_page TEXT NOT NULL,
          evidence_source TEXT NOT NULL,
          captured_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_v311_verified_inn
          ON v311_verified_buyers(inn);
        """)
        stamp=datetime.now(timezone.utc).isoformat()
        for reg,item in CONFIRMED.items():
            # Prefer future exact-card EIS evidence over independently
            # checked historical public register metadata.
            db.execute("""
             INSERT INTO v311_verified_buyers(reg,name,inn,evidence_page,evidence_source,captured_at)
             VALUES(?,?,?,?,?,?)
             ON CONFLICT(reg) DO UPDATE SET
               name=excluded.name, inn=excluded.inn,
               evidence_page=excluded.evidence_page,
               evidence_source=excluded.evidence_source,captured_at=excluded.captured_at
             WHERE v311_verified_buyers.evidence_source != 'official_eis_card'
            """,(reg,item["name"],item["inn"],item["source_url"],
                 "public_registry_crosscheck",stamp))
            row=db.execute(
                "SELECT name,inn FROM v311_verified_buyers WHERE reg=?",(reg,)).fetchone()
            if row:
                db.execute(
                    "UPDATE purchases SET customer=?,customer_inn=? WHERE reg_number=?",
                    (row["name"],row["inn"],reg))
        # Legacy manual INN searches sometimes returned unrelated generic
        # EIS results. Remove ONLY mappings contradicting verified reg cards.
        exists=db.execute("""SELECT 1 FROM sqlite_master
                          WHERE type='table' AND name='v301_inn_purchases'""").fetchone()
        if exists:
            db.execute("""
              DELETE FROM v301_inn_purchases
              WHERE EXISTS(
                SELECT 1 FROM v311_verified_buyers b
                WHERE b.reg=v301_inn_purchases.reg AND b.inn<>''
                      AND v301_inn_purchases.inn<>b.inn)
            """)
            db.executemany("""INSERT OR IGNORE INTO v301_inn_purchases(inn,reg,found_at)
                              VALUES (?,?,?)""",[(x["inn"],reg,stamp)
                                                  for reg,x in CONFIRMED.items()])
        db.commit()
        print("V311 CUSTOMER_VERIFIED_INIT named="+str(len(CONFIRMED)),flush=True)
    finally:db.close()

def read_verified(ns,reg):
    if not REG.fullmatch(str(reg)):return {}
    db=_db(ns)
    try:
        row=db.execute(
            "SELECT name,inn,evidence_page,evidence_source FROM v311_verified_buyers WHERE reg=?",
            (reg,)).fetchone()
        return dict(row) if row else {}
    except sqlite3.OperationalError:
        return {}
    finally:db.close()

def _official_source(raw,reg):
    try:
        u=urlsplit(str(raw or ""))
        if (u.scheme!="https" or u.hostname!="zakupki.gov.ru" or
            u.port not in (None,443) or u.username or u.password or
            u.fragment or u.path not in CARD_PATHS):
            return False
        q=parse_qs(u.query)
        return (q.get("regNumber")==[reg] or q.get("purchaseNoticeNumber")==[reg])
    except (TypeError,ValueError):
        return False

def _apply(ns,reg,name,inn,source):
    db=_db(ns)
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""
          INSERT INTO v311_verified_buyers(reg,name,inn,evidence_page,evidence_source,captured_at)
          VALUES(?,?,?,?,?,?)
          ON CONFLICT(reg) DO UPDATE SET
            name=excluded.name,
            inn=CASE WHEN excluded.inn<>'' THEN excluded.inn ELSE v311_verified_buyers.inn END,
            evidence_page=excluded.evidence_page,evidence_source=excluded.evidence_source,
            captured_at=excluded.captured_at
        """,(reg,name,inn,source,"official_eis_card",datetime.now(timezone.utc).isoformat()))
        result=db.execute("""
          UPDATE purchases SET
            customer=?,
            customer_inn=CASE WHEN ?<>'' THEN ? ELSE customer_inn END
          WHERE reg_number=?
        """,(name,inn,inn,reg))
        if inn:
            has=db.execute("""SELECT 1 FROM sqlite_master
                               WHERE type='table' AND name='v301_inn_purchases'""").fetchone()
            if has:
                db.execute("DELETE FROM v301_inn_purchases WHERE reg=? AND inn<>?",(reg,inn))
                db.execute("""INSERT OR IGNORE INTO v301_inn_purchases(inn,reg,found_at)
                              VALUES(?,?,?)""",(inn,reg,datetime.now(timezone.utc).isoformat()))
        db.commit()
        print(f"V311 VERIFIED_CUSTOMER reg={reg} inn_present={bool(inn)} "
              f"purchase_updated={result.rowcount>0}",flush=True)
        return result.rowcount>0
    except:
        if db.in_transaction:db.rollback()
        raise
    finally:db.close()

def install(ns):
    app=ns["app"]

    @app.on_event("startup")
    async def bootstrap_v311_customers():
        await asyncio.to_thread(_initialize,ns)

    @app.post("/api/v311/eis223/customer")
    async def official_customer(request:Request):
        auth(request)
        if request.headers.get("origin")!="https://zakupki.gov.ru":
            raise HTTPException(403,"Нужна открытая официальная страница ЕИС")
        try:version=int(request.headers.get("x-eis223-collector-version") or 0)
        except ValueError:version=0
        if version<311:raise HTTPException(426,"Требуется мост ЕИС v311")
        raw=await request.body()
        if len(raw)>2200:raise HTTPException(413,"Слишком большой ответ")
        try:
            data=json.loads(raw)
            reg=str(data["reg_number"]).strip()
            name=_name(data.get("customer"))
            inn=str(data.get("customer_inn") or "").strip()
            source=str(data["source_page"]).strip()
        except (ValueError,TypeError,KeyError,AttributeError) as exc:
            raise HTTPException(422,"Неверные данные заказчика") from exc
        if not REG.fullmatch(reg) or not name or not _official_source(source,reg):
            raise HTTPException(422,"Нет проверенного имени заказчика из карточки ЕИС")
        if inn and not INN.fullmatch(inn):
            raise HTTPException(422,"Неверный ИНН заказчика")
        updated=await asyncio.to_thread(_apply,ns,reg,name,inn,source)
        return {"accepted":True,"customer_verified":True,"customer":name,
                "updated":updated,"metadata_source":"official_eis_card"}

    @app.get("/api/v311/customer/{reg}")
    async def verified_status(reg:str):
        if not REG.fullmatch(reg):raise HTTPException(400,"Неверный номер")
        value=await asyncio.to_thread(read_verified,ns,reg)
        if not value:raise HTTPException(404,"Заказчик ещё не подтверждён")
        return {"reg_number":reg,**value}

    print("V311 CUSTOMER_EIS_UPDATE_READY trusted_card_only=True",flush=True)
