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
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit,parse_qs

from fastapi import HTTPException,Request
from v295_eis_originals import auth
from v342_identity import customer_label, valid_inn

REG=re.compile(r"^3\d{10}$")
INN=re.compile(r"^(?:\d{10}|\d{12})$")
CARD_PATHS=frozenset((
    "/223/purchase/public/purchase/info/common-info.html",
    "/223/purchase/public/purchase/info/commoninfo.html",
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
    value=customer_label(value)
    if not 6<=len(value)<=400 or not re.search(r"[А-Яа-яЁёA-Za-z]",value):return ""
    if re.search(r"^(?:уточняем заказчика|заказчик уточняется|уточняется|не указан|нет данных|организация|заказчик|unknown)\b",value,re.I):return ""
    if re.fullmatch(r"[\d\W]+",value):return ""
    # EIS sometimes provides entire legal names in uppercase; preserve legal
    # entity abbreviations while rendering a normally cased company name.
    alpha=[c for c in value if c.isalpha()]
    if alpha and sum(c.isupper() for c in alpha)>=int(.85*len(alpha)):
        abbreviations=("ООО","ПАО","АО","ОАО","ЗАО","ФГУП","ФГБУ",
                       "ФБУЗ","ГБУ","МБУ","МУП","АНО","ФГБОУ",
                       "ГАУЗ","ГАУ","КГАУ","ГБУЗ","ГУП")
        # An abbreviated legal name is already correctly cased (e.g.
        # ГАУЗ АО «АОКБ»); sentence casing would corrupt both abbreviations.
        if len(value)<80 and re.match(r"^(?:"+"|".join(abbreviations)+r")\b",value):
            return value
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
        CREATE TABLE IF NOT EXISTS v322_verified_prices(
          reg TEXT PRIMARY KEY,
          price TEXT NOT NULL,
          price_num REAL NOT NULL,
          evidence_page TEXT NOT NULL,
          captured_at TEXT NOT NULL
        );
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
        if q.get("regNumber")==[reg] or q.get("purchaseNoticeNumber")==[reg]:
            return True
        # The EIS search index may link to a verified noticeInfoId instead
        # of a regNumber URL. Only authorized browser-origin card metadata is
        # accepted, never an arbitrary external website or procurement query.
        ids=q.get("noticeInfoId",[])+q.get("noticeId",[])
        return (not q.get("regNumber") and not q.get("purchaseNoticeNumber") and
                len(ids)==1 and bool(re.fullmatch(r"[0-9]{1,15}",ids[0])))
    except (TypeError,ValueError):
        return False

def _apply(ns,reg,name,inn,source):
    db=_db(ns)
    try:
        db.execute("BEGIN IMMEDIATE")
        prior=db.execute(
            "SELECT name,inn FROM v311_verified_buyers WHERE reg=?",(reg,)
        ).fetchone()
        if prior:
            prior_inn=str(prior["inn"] or "").strip()
            if prior_inn and inn and inn!=prior_inn:
                raise ValueError("ИНН не совпадает с ранее проверенным заказчиком")
            if prior_inn and not inn:
                normalized=lambda x:re.sub(r"[^а-яёa-z0-9]","",str(x or "").casefold())
                if normalized(prior["name"])!=normalized(name):
                    raise ValueError("Без ИНН нельзя заменять ранее подтверждённое имя заказчика")
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

def _verified_money(value):
    """Accept an unambiguous amount, never a guess from procurement title."""
    if isinstance(value,bool):raise ValueError("НМЦД не является денежной суммой")
    raw=" ".join(str(value if value is not None else "").split())
    if not raw or len(raw)>110:
        raise ValueError("НМЦД отсутствует или слишком длинная")
    if re.search(r"(?:не установ|формула|диапазон|за единиц|не определ)",raw,re.I):
        raise ValueError("Отсутствует конкретная НМЦД")
    pattern=r"([0-9][0-9 ]{0,18}(?:[.,][0-9]{1,2})?)"
    match=re.fullmatch(r"\s*"+pattern+r"\s*(?:₽|руб\.?|рублей|рубля|рубль|RUB|Российский рубль)?\s*",raw,re.I)
    if not match:
        raise ValueError("НМЦД не является одной конкретной суммой")
    normalized=match.group(1).replace(" ","").replace(",",".")
    try:amount=Decimal(normalized)
    except InvalidOperation as exc:raise ValueError("НМЦД не распознана") from exc
    if not amount.is_finite() or amount<0 or amount>Decimal("1000000000000000"):
        raise ValueError("НМЦД вне допустимого диапазона")
    return normalized,float(amount)


def read_verified_price(ns,reg):
    if not REG.fullmatch(str(reg)):return {}
    db=_db(ns)
    try:
        row=db.execute("SELECT price,price_num,evidence_page FROM v322_verified_prices WHERE reg=?",(reg,)).fetchone()
        return dict(row) if row else {}
    except sqlite3.OperationalError:
        return {}
    finally:db.close()


def _save_verified_price(ns,reg,price,amount,source):
    db=_db(ns)
    try:
        db.execute("BEGIN IMMEDIATE")
        stamp=datetime.now(timezone.utc).isoformat()
        db.execute("""
          INSERT INTO v322_verified_prices(reg,price,price_num,evidence_page,captured_at)
          VALUES(?,?,?,?,?)
          ON CONFLICT(reg) DO UPDATE SET
            price=excluded.price,price_num=excluded.price_num,
            evidence_page=excluded.evidence_page,captured_at=excluded.captured_at
        """,(reg,price,amount,source,stamp))
        # No procurement row is created merely from a price. If a verified
        # purchase already exists, fill only missing price fields.
        from v335_price_recovery import update_price
        updated=update_price(db,reg,{"price":price,"price_num":amount,
            "source":"official_eis_card","source_url":source})
        db.commit()
        print(f"V322 VERIFIED_NMCD reg={reg} stored=True row_updated={bool(updated)}",flush=True)
        return bool(updated)
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
        if version<313:raise HTTPException(426,"Требуется мост ЕИС v313")
        raw=await request.body()
        if len(raw)>2200:raise HTTPException(413,"Слишком большой ответ")
        try:
            data=json.loads(raw)
            reg=str(data["reg_number"]).strip()
            name=_name(data.get("customer"))
            raw_inn=str(data.get("customer_inn") or "").strip()
            inn=valid_inn(raw_inn)
            source=str(data["source_page"]).strip()
            title=" ".join(str(data.get("title") or "").split())[:800]
        except (ValueError,TypeError,KeyError,AttributeError) as exc:
            raise HTTPException(422,"Неверные данные заказчика") from exc
        from v342_identity import subject
        title=subject(title)
        if not REG.fullmatch(reg) or not (name or inn or title) or not _official_source(source,reg):
            raise HTTPException(422,"Нет проверенных реквизитов из карточки ЕИС")
        if raw_inn and not inn:
            raise HTTPException(422,"Неверный ИНН заказчика")
        try:
            updated=await asyncio.to_thread(_apply,ns,reg,name,inn,source) if name else False
            from v299_browser_intake import _apply_customer
            changed=await asyncio.to_thread(_apply_customer,ns,{"reg_number":reg,
                "customer":name,"customer_inn":inn,"title":title})
            updated=updated or changed
        except ValueError as exc:
            raise HTTPException(409,str(exc)) from exc
        return {"accepted":True,"customer_verified":bool(name),"customer":name,
                "updated":updated,"metadata_source":"official_eis_card"}

    @app.post("/api/v322/eis223/price")
    async def verified_eis_price(request:Request):
        auth(request)
        if request.headers.get("origin")!="https://zakupki.gov.ru":
            raise HTTPException(403,"Нужна официальная вкладка ЕИС")
        try:version=int(request.headers.get("x-eis223-collector-version") or 0)
        except ValueError:version=0
        if version<323:
            raise HTTPException(426,"Для передачи НМЦД требуется браузерный мост v323")
        raw=await request.body()
        if len(raw)>1300:raise HTTPException(413,"Слишком большой ответ")
        try:
            data=json.loads(raw)
            reg=str(data["reg_number"]).strip()
            source=str(data["source_page"]).strip()
            price,amount=_verified_money(data.get("price"))
        except (ValueError,TypeError,KeyError,AttributeError) as exc:
            raise HTTPException(422,"Неверные сведения о НМЦД из ЕИС") from exc
        if not REG.fullmatch(reg) or not _official_source(source,reg):
            raise HTTPException(422,"Источник НМЦД не является подтверждённой карточкой ЕИС")
        updated=await asyncio.to_thread(_save_verified_price,ns,reg,price,amount,source)
        return {"accepted":True,"reg_number":reg,"price":price,
                "updated_existing":updated,"source":"official_eis_card"}

    @app.get("/api/v311/customer/{reg}")
    async def verified_status(reg:str):
        if not REG.fullmatch(reg):raise HTTPException(400,"Неверный номер")
        value=await asyncio.to_thread(read_verified,ns,reg)
        if not value:raise HTTPException(404,"Заказчик ещё не подтверждён")
        return {"reg_number":reg,**value}

    print("V311 CUSTOMER_EIS_UPDATE_READY trusted_card_only=True",flush=True)
