"""Verified EIS card price intake and bounded backfill of missing prices.

The browser bridge reads an actual EIS 223 card (never a tender aggregator).
Distinct card values are typed, not labelled as NMCD indiscriminately.
Historical amounts and findings are retained. No background web scraping.
"""
from __future__ import annotations
import asyncio
import json
import re
import sqlite3
from datetime import datetime,timezone
from fastapi import HTTPException, Request

from v295_eis_originals import auth
from v311_verified_customer import REG,_official_source,_verified_money
from v335_price_recovery import money,update_price

KINDS={
    "nmcd":re.compile(r"^(?:начальн\w*\s*\(?максимальн\w*\)?\s*цен\w*(?:\s+договора)?|начальн\w*\s+цен\w*\s+договора|нмцд|начальная\s*\(максимальная\)\s*цена)(?:\s*\(цена\s+лота\))?$",re.I),
    "maximum_contract_value":re.compile(r"^максимальн\w*\s+значени\w*\s+цен\w*\s+договора$",re.I),
    "subject_cost":re.compile(r"^(?:стоимост\w*\s+предмет\w*\s+(?:закупк\w*|договора)|стоимост\w*\s+закупк\w*|сумма\s+закупк\w*)$",re.I),
}
LABELS={"nmcd":"НМЦД","maximum_contract_value":"Максимальная цена договора",
        "subject_cost":"Стоимость предмета закупки"}
from v341_eis_facts import KINDS, LABELS

def _now():return datetime.now(timezone.utc).isoformat()

def initialize(ns):
    db=ns["conn"]()
    try:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS v338_card_price(
              reg TEXT PRIMARY KEY,price TEXT NOT NULL,price_num REAL NOT NULL,
              price_kind TEXT NOT NULL,price_label TEXT NOT NULL,
              source_page TEXT NOT NULL,captured_at TEXT NOT NULL,
              conflict INTEGER NOT NULL DEFAULT 0,alternative_price TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS v338_price_requests(
              reg TEXT PRIMARY KEY,created_at TEXT NOT NULL,
              attempts INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS v338_price_rechecks(
              reg TEXT PRIMARY KEY,state TEXT NOT NULL DEFAULT 'pending',
              changed_at TEXT NOT NULL, checked_at TEXT,
              result TEXT NOT NULL DEFAULT ''
            );
        """)
        # Existing production databases may predate the retry counter.
        cols={r[1] for r in db.execute('PRAGMA table_info(v338_price_requests)')}
        if 'attempts' not in cols:
            db.execute('ALTER TABLE v338_price_requests ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0')
        db.commit()
    finally:db.close()

def save_card_price(ns,reg,price,amount,kind,label,source):
    db=ns["conn"]();db.row_factory=sqlite3.Row
    try:
        db.execute("BEGIN IMMEDIATE")
        prev=db.execute("SELECT * FROM v338_card_price WHERE reg=?",(reg,)).fetchone()
        conflict=bool(prev and money(prev["price"]) and
                      (money(prev["price"])[1]!=amount or prev["price_kind"]!=kind))
        if conflict:
            db.execute("""UPDATE v338_card_price SET conflict=1,alternative_price=?
                      WHERE reg=?""",(f"{LABELS[kind]}: {price} ({source})"[:430],reg))
            db.commit()
            return {"updated":False,"conflict":True,"saved":False}
        db.execute("""INSERT INTO v338_card_price
          (reg,price,price_num,price_kind,price_label,source_page,captured_at)
          VALUES(?,?,?,?,?,?,?)
          ON CONFLICT(reg) DO UPDATE SET
           price=excluded.price,price_num=excluded.price_num,
           price_kind=excluded.price_kind,price_label=excluded.price_label,
           source_page=excluded.source_page,captured_at=excluded.captured_at
        """,(reg,price,amount,kind,label,source,_now()))
        existing=db.execute("SELECT price,price_num FROM purchases WHERE reg_number=?",(reg,)).fetchone()
        before=bool(existing and money(existing["price_num"]))
        # Fill only missing prices. A previously recorded, different amount
        # is a discrepancy to review, not a license to silently overwrite.
        if before and money(existing["price_num"])[1]!=amount:
            db.execute("""UPDATE v338_card_price SET conflict=1,alternative_price=?
                         WHERE reg=?""",(f"Ранее сохранено: {existing['price_num']}",reg))
            db.commit()
            return {"updated":False,"conflict":True,"saved":True}
        updated=update_price(db,reg,{"price":price,"price_num":amount,
                        "price_kind":kind,"source":"official_eis_card",
                        "source_url":source,"price_source_url":source,
                        "evidence_page":source})
        # Even when the amount already exists, persist price KIND in a card.
        if existing:
            row=db.execute("SELECT card_fields_json,price_num FROM purchases WHERE reg_number=?",(reg,)).fetchone()
            if row is not None and money(row["price_num"]) and money(row["price_num"])[1]==amount:
                try:card=json.loads(row["card_fields_json"] or "{}")
                except (ValueError,TypeError):card={}
                if not isinstance(card,dict):card={}
                summary=card.setdefault("summary",{})
                if not isinstance(summary,dict):summary={};card["summary"]=summary
                summary["price_kind"]=kind
                card["price_evidence"]={"price":price,"price_kind":kind,
                      "source":"official_eis_card","source_url":source,
                      "price_label":label,"evidence_page":source}
                db.execute("UPDATE purchases SET card_fields_json=? WHERE reg_number=?",
                           (json.dumps(card,ensure_ascii=False),reg))
        if existing:
            db.execute("""INSERT OR IGNORE INTO v338_price_rechecks(reg,state,changed_at)
                          VALUES(?,'pending',?)""",(reg,_now()))
        db.commit()
        return {"updated":bool(updated),"conflict":False,"saved":True}
    except:
        if db.in_transaction:db.rollback()
        raise
    finally:db.close()

def price_summary(ns):
    db=ns["conn"]();db.row_factory=sqlite3.Row
    try:
        total=db.execute("SELECT COUNT(*) FROM purchases").fetchone()[0]
        missing=db.execute("""SELECT COUNT(*) FROM purchases
           WHERE price_num IS NULL OR TRIM(COALESCE(price,''))=''""").fetchone()[0]
        known=db.execute("SELECT COUNT(*) FROM v338_card_price").fetchone()[0]
        conflicted=db.execute("SELECT COUNT(*) FROM v338_card_price WHERE conflict=1").fetchone()[0]
        requested=db.execute("SELECT COUNT(*) FROM v338_price_requests").fetchone()[0]
        pending=db.execute("SELECT COUNT(*) FROM v338_price_rechecks WHERE state='pending'").fetchone()[0]
        rechecked=db.execute("SELECT COUNT(*) FROM v338_price_rechecks WHERE state='completed'").fetchone()[0]
        automatic=db.execute("SELECT COUNT(*) FROM v338_price_rechecks WHERE state='automatic'").fetchone()[0]
        return {"purchases":total,"missing_price_fields":missing,
                "official_cards_captured":known,"price_conflicts_require_review":conflicted,
                "bridge_backfill_jobs_submitted":requested,
                "price_triggered_legal_rechecks_pending":pending,
                "price_triggered_legal_rechecks_completed":rechecked,
                "price_triggered_legal_rechecks_automatically_monitored":automatic}
    finally:db.close()

def handoff_card_rechecks(ns,limit=32):
    """Retire the parallel price-only analyzer in favor of durable queues.

    A handoff is not a completed legal analysis. Existing campaign membership
    stays unchanged; semantic source polling observes the actual authoritative
    card change and applies its usual ready-document/backpressure guards.
    """
    from v332_all_procurements_audit import CAMPAIGN
    from v381_audit_scheduling import notify_evidence
    notify_evidence(ns)
    db=ns["conn"]();db.row_factory=sqlite3.Row
    try:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='v332_audit_queue'").fetchone():
            return 0
        rows=db.execute("""SELECT r.reg,r.changed_at FROM v338_price_rechecks r
            WHERE r.state='pending' AND EXISTS(
                SELECT 1 FROM v332_audit_queue q WHERE q.reg=r.reg AND q.campaign=?)
            ORDER BY r.changed_at,r.reg LIMIT ?""",(CAMPAIGN,max(0,min(32,int(limit))))).fetchall()
        changed=0
        for row in rows:
            changed+=db.execute("""UPDATE v338_price_rechecks SET state='automatic',
                result=? WHERE reg=? AND state='pending' AND changed_at=?""",
                ("Передано автоматическому наблюдению основной очереди; юридическая проверка ещё не подтверждена",
                 row["reg"],row["changed_at"])).rowcount
        db.commit()
        return changed
    except BaseException:
        db.rollback()
        raise
    finally:db.close()


def enqueue_missing_prices(ns,limit=8):
    """Put EIS-card reads in the same browser queue, behind manual requests."""
    from v301_search_jobs import _enqueue
    db=ns["conn"]();db.row_factory=sqlite3.Row
    try:
        ready=db.execute("""SELECT COUNT(*) FROM v301_search_jobs
                       WHERE status IN ('queued','leased','deferred')""").fetchone()[0]
        if ready>=24:return 0
        from v332_all_procurements_audit import CAMPAIGN
        rows=db.execute("""SELECT p.reg_number FROM purchases p
                    JOIN v332_audit_queue aq ON aq.reg=p.reg_number AND aq.campaign=?
                    LEFT JOIN v338_price_requests r ON r.reg=p.reg_number
                    WHERE p.documents_checked>0
                    AND (p.price_num IS NULL OR TRIM(COALESCE(p.price,''))=''
                         OR TRIM(COALESCE(p.method,''))=''
                         OR TRIM(COALESCE(p.submission_end,''))='')
                    AND (r.reg IS NULL OR
                         (r.attempts<3 AND strftime('%s',r.created_at)<strftime('%s','now','-6 hours')))
                    ORDER BY p.last_seen DESC LIMIT ?""",(CAMPAIGN,min(8,limit))).fetchall()
    finally:db.close()
    queued=0
    for row in rows:
        reg=str(row["reg_number"])
        if not REG.fullmatch(reg):continue
        try:
            _enqueue(ns,"reg",reg,1,8,"v341-card-facts",False)
            db=ns["conn"]()
            try:
                db.execute('''INSERT INTO v338_price_requests(reg,created_at,attempts)
                    VALUES(?,?,1) ON CONFLICT(reg) DO UPDATE SET
                    created_at=excluded.created_at,attempts=attempts+1''',(reg,_now()))
                db.commit()
            finally:db.close()
            queued+=1
        except Exception as exc:
            print(f"V338 PRICE_BACKFILL_QUEUING_ERROR reg={reg} error={type(exc).__name__}",flush=True)
    return queued

def install(ns):
    app=ns["app"]
    @app.post("/api/v338/eis223/card-price")
    async def verified_card_price(request:Request):
        auth(request)
        if request.headers.get("origin")!="https://zakupki.gov.ru":
            raise HTTPException(403,"Сведения принимаются только из ЕИС")
        try:version=int(request.headers.get("x-eis223-collector-version") or "0")
        except ValueError:version=0
        if version<338:raise HTTPException(426,"Нужен браузерный мост v338")
        raw=await request.body()
        if len(raw)>1650:raise HTTPException(413,"Слишком большой ответ")
        try:
            data=json.loads(raw)
            reg=str(data["reg_number"])
            source=str(data["source_page"])
            kind=str(data["price_kind"])
            label=" ".join(str(data["price_label"]).split()).rstrip(": ")
            price,amount=_verified_money(data.get("price"))
        except (ValueError,TypeError,KeyError,AttributeError):
            raise HTTPException(422,"Неполные сведения о цене")
        if not REG.fullmatch(reg) or not _official_source(source,reg):
            raise HTTPException(422,"Нет подтверждённой карточки закупки ЕИС")
        if kind not in KINDS or not KINDS[kind].fullmatch(label):
            raise HTTPException(422,"Не подтверждён вид цены с карточки ЕИС")
        answer=await asyncio.to_thread(save_card_price,ns,reg,price,amount,kind,label,source)
        print(f"V338 EIS_CARD_PRICE reg={reg} kind={kind} amount={price} "
              f"saved={answer['saved']} conflict={answer['conflict']}",flush=True)
        return {"accepted":True,"reg_number":reg,"price":price,
                "price_kind":kind,**answer}

    @app.get("/api/v338/price/status")
    async def price_status():
        return await asyncio.to_thread(price_summary,ns)

    @app.on_event("startup")
    async def prepare():
        await asyncio.to_thread(initialize,ns)
        async def worker():
            await asyncio.sleep(55)
            while True:
                try:
                    queued=await asyncio.to_thread(enqueue_missing_prices,ns)
                    if queued:print(f"V338 CARD_PRICE_BACKFILL_QUEUED count={queued}",flush=True)
                except Exception as exc:
                    print("V338 CARD_PRICE_BACKFILL_FAILED "+type(exc).__name__,flush=True)
                await asyncio.sleep(300)
        ns["_v338_card_price_worker"]=asyncio.create_task(worker())
        async def recheck_after_card_price():
            # Card-only changes belong to the same bounded semantic queue as
            # originals and rule changes. Never wait for every procurement to
            # finish and never invoke a second, unscheduled legal analyzer.
            await asyncio.sleep(90)
            while True:
                try:
                    handed=await asyncio.to_thread(handoff_card_rechecks,ns)
                    if handed:
                        print(f"V338 PRICE_AUTOMATIC_HANDOFF count={handed}",flush=True)
                except Exception as exc:
                    print(f"V338 PRICE_LEGAL_RECHECK_ERROR {type(exc).__name__}: "
                          f"{str(exc)[:130]}",flush=True)
                await asyncio.sleep(60)
        ns["_v338_price_rechecks_worker"]=asyncio.create_task(recheck_after_card_price())
    print("V338 EIS_CARD_PRICE_READY typed_values=True official_browser_only=True "
          "backfill_behind_manual=True",flush=True)
