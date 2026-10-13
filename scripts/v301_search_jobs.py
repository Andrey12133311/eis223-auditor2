"""Persistent manual 223-FZ search queue for an EIS-origin browser bridge.

Only the browser at zakupki.gov.ru contacts EIS. Server checks validated original
bytes locally and never fabricates documents, searches or violation counts.
"""
from __future__ import annotations
import asyncio
import json
import re
import secrets
import sqlite3
import time
from datetime import datetime,timezone
from fastapi import HTTPException,Request
from v295_eis_originals import auth,allowed_documents,saved_document,root

REG=re.compile(r"^3[0-9]{10}$")
INN=re.compile(r"^(?:[0-9]{10}|[0-9]{12})$")
STATES=frozenset(("queued","leased","done","partial","error","deferred"))
MAX_QUEUE=350

def _db(ns):
    c=ns["conn"]()
    c.row_factory=sqlite3.Row
    return c

def _init(ns):
    db=_db(ns)
    try:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS v301_search_jobs(
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                target TEXT NOT NULL,
                page INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'queued',
                priority INTEGER NOT NULL DEFAULT 10,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                not_before INTEGER NOT NULL DEFAULT 0,
                lease_until INTEGER NOT NULL DEFAULT 0,
                attempts INTEGER NOT NULL DEFAULT 0,
                detail TEXT NOT NULL DEFAULT '',
                found INTEGER NOT NULL DEFAULT 0,
                accepted INTEGER NOT NULL DEFAULT 0,
                parent_id TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_v301_ready
              ON v301_search_jobs(status,not_before,priority,created_at);
            CREATE TABLE IF NOT EXISTS v301_inn_purchases(
                inn TEXT NOT NULL,
                reg TEXT NOT NULL,
                found_at TEXT NOT NULL,
                PRIMARY KEY(inn,reg)
            );
            CREATE TABLE IF NOT EXISTS v301_bridge_health(
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                seen_at INTEGER NOT NULL,
                last_action TEXT NOT NULL DEFAULT ''
            );
        """)
        # Repair the backlog produced by the obsolete /223/purchase/... URL.
        # These jobs are NOT regarded as successful checks. They wait for the
        # upgraded browser bridge to retrieve real original-file bytes.
        repaired=db.execute(
            "UPDATE v301_search_jobs SET status='queued',attempts=0,not_before=0,"
            "lease_until=0,updated_at=? "
            "WHERE status='partial' AND kind='reg' "
            "AND detail LIKE 'ЕИС HTTP 404 /223/purchase/%'",
            (_utc(),)).rowcount
        db.commit()
        if repaired:
            print(f"V308 RETRY_OLD_404_BACKLOG jobs={repaired}",flush=True)
    finally:db.close()

def _utc():return datetime.now(timezone.utc).isoformat()

def _local_results(ns,kind,target):
    db=_db(ns)
    try:
        if kind=="reg":
            rows=db.execute(
                "SELECT reg_number,title,customer,violations,documents_checked,documents_found "
                "FROM purchases WHERE reg_number=?",(target,)).fetchall()
        else:
            rows=db.execute(
                "SELECT DISTINCT p.reg_number,p.title,p.customer,p.violations,p.documents_checked,p.documents_found "
                "FROM purchases p LEFT JOIN v301_inn_purchases m ON m.reg=p.reg_number "
                "WHERE p.customer_inn=? OR m.inn=? ORDER BY p.last_seen DESC LIMIT 250",
                (target,target)).fetchall()
        return [dict(r) for r in rows]
    finally:db.close()

def _enqueue(ns,kind,target,page=1,priority=10,parent_id=""):
    now=_utc()
    db=_db(ns)
    try:
        db.execute("BEGIN IMMEDIATE")
        old=db.execute(
            "SELECT * FROM v301_search_jobs WHERE kind=? AND target=? AND page=? "
            "AND status IN ('queued','leased','deferred') ORDER BY created_at DESC LIMIT 1",
            (kind,target,page)).fetchone()
        if old:
            db.commit()
            return dict(old),False
        active=db.execute(
            "SELECT COUNT(*) FROM v301_search_jobs WHERE status IN ('queued','leased','deferred')"
        ).fetchone()[0]
        if active>=MAX_QUEUE:
            db.rollback()
            raise HTTPException(429,"Очередь ЕИС заполнена. Попробуйте позже.")
        identifier=secrets.token_hex(12)
        db.execute(
            "INSERT INTO v301_search_jobs "
            "(id,kind,target,page,status,priority,created_at,updated_at,parent_id) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (identifier,kind,target,page,"queued",priority,now,now,parent_id))
        if kind=="reg":
            # Retain independent manual procurement history.
            try:
                db.execute("INSERT OR IGNORE INTO v257_manual_registry(reg) VALUES(?)",(target,))
            except sqlite3.OperationalError:
                pass
        db.commit()
        row=db.execute("SELECT * FROM v301_search_jobs WHERE id=?",(identifier,)).fetchone()
        print(f"V301 SEARCH_QUEUED kind={kind} target={target} page={page}",flush=True)
        return dict(row),True
    except:
        if db.in_transaction:db.rollback()
        raise
    finally:db.close()

def _finish(ns,job_id,status,detail,found,accepted,defer=0,regs=None,more=False):
    if status not in ("done","partial","error","deferred"):status="error"
    db=_db(ns)
    try:
        db.execute("BEGIN IMMEDIATE")
        row=db.execute("SELECT * FROM v301_search_jobs WHERE id=?",(job_id,)).fetchone()
        if not row:
            raise HTTPException(404,"Неизвестный запрос")
        if row["status"] not in ("leased","queued","deferred"):
            db.commit()
            return dict(row)
        valid=[]
        for reg in (regs if isinstance(regs,list) else [])[:40]:
            if isinstance(reg,str) and REG.fullmatch(reg) and reg not in valid:valid.append(reg)
        if row["kind"]=="inn" and valid:
            db.executemany(
                "INSERT OR IGNORE INTO v301_inn_purchases(inn,reg,found_at) VALUES(?,?,?)",
                [(row["target"],r,_utc()) for r in valid])
            try:
                db.executemany(
                    "INSERT OR IGNORE INTO v257_manual_registry(reg) VALUES(?)",
                    [(r,) for r in valid])
            except sqlite3.OperationalError:
                pass
        now=_utc()
        nb=int(time.time())+min(1800,max(60,defer)) if status=="deferred" else 0
        effective="queued" if status=="deferred" else status
        attempts=int(row["attempts"])
        if row["kind"]=="reg" and status=="partial":
            # Retry without overloading EIS. Every genuinely imported original
            # is progress and renews the bounded retry budget. A procurement
            # with permanently inaccessible originals stops after 3 failures.
            if accepted>0:
                attempts=0
            if attempts<3:
                effective="queued"
                nb=int(time.time())+900
        db.execute(
            "UPDATE v301_search_jobs SET status=?,detail=?,found=?,accepted=?,"
            "not_before=?,lease_until=0,updated_at=?,attempts=? WHERE id=?",
            (effective,str(detail)[:300],max(0,min(1000,found)),max(0,min(1000,accepted)),
             nb,now,attempts,job_id))
        if row["kind"]=="inn":
            # Add actual official-search results to a bounded priority queue.
            for reg in valid[:30]:
                in_queue=db.execute(
                  "SELECT 1 FROM v301_search_jobs WHERE kind='reg' AND target=? "
                  "AND status IN ('queued','leased','deferred') LIMIT 1",(reg,)).fetchone()
                if not in_queue:
                    active=db.execute(
                        "SELECT COUNT(*) FROM v301_search_jobs WHERE status IN ('queued','leased','deferred')"
                    ).fetchone()[0]
                    if active>=MAX_QUEUE:break
                    db.execute(
                        "INSERT INTO v301_search_jobs"
                        "(id,kind,target,page,status,priority,created_at,updated_at,parent_id) "
                        "VALUES (?,?,?,1,'queued',?,?,?,?)",
                        (secrets.token_hex(12),"reg",reg,8,now,now,row["id"]))
            if more and row["page"]<10 and valid:
                next_page=row["page"]+1
                existing=db.execute(
                    "SELECT 1 FROM v301_search_jobs WHERE kind='inn' AND target=? AND page=?",
                    (row["target"],next_page)).fetchone()
                if not existing:
                    db.execute(
                        "INSERT INTO v301_search_jobs"
                        "(id,kind,target,page,status,priority,created_at,updated_at,not_before,parent_id) "
                        "VALUES (?,?,?,?,'queued',?,?,?,?,?)",
                        (secrets.token_hex(12),"inn",row["target"],next_page,12,
                         now,now,int(time.time())+75,row["id"]))
        db.commit()
        print(f"V301 SEARCH_REPORT kind={row['kind']} target={row['target']} status={status} "
              f"found={len(valid)} imported={accepted}",flush=True)
        return dict(db.execute("SELECT * FROM v301_search_jobs WHERE id=?",(job_id,)).fetchone())
    except:
        if db.in_transaction:db.rollback()
        raise
    finally:db.close()

def _claim(ns):
    db=_db(ns)
    now=int(time.time())
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "UPDATE v301_search_jobs SET status='queued',not_before=? "
            "WHERE status='leased' AND lease_until<? AND attempts<4",(now+30,now))
        db.execute(
            "UPDATE v301_search_jobs SET status='error',detail='Истёк лимит повторных попыток' "
            "WHERE status='leased' AND lease_until<? AND attempts>=4",(now,))
        row=db.execute(
            "SELECT * FROM v301_search_jobs WHERE status='queued' AND not_before<=? "
            "ORDER BY priority ASC,created_at ASC LIMIT 1",(now,)).fetchone()
        if row:
            db.execute(
                "UPDATE v301_search_jobs SET status='leased',lease_until=?,"
                "attempts=attempts+1,updated_at=? WHERE id=?",
                (now+420,_utc(),row["id"]))
        db.execute("INSERT OR REPLACE INTO v301_bridge_health(singleton,seen_at,last_action) "
                   "VALUES(1,?,?)",(now,"claimed" if row else "idle"))
        db.commit()
        return dict(db.execute("SELECT * FROM v301_search_jobs WHERE id=?",(row["id"],)).fetchone()) if row else None
    except:
        if db.in_transaction:db.rollback()
        raise
    finally:db.close()

def _bridge(ns):
    db=_db(ns)
    try:
        row=db.execute("SELECT seen_at,last_action FROM v301_bridge_health WHERE singleton=1").fetchone()
        active=bool(row and int(time.time())-row["seen_at"]<40)
        q=db.execute("SELECT COUNT(*) FROM v301_search_jobs WHERE status IN ('queued','leased')").fetchone()[0]
        return {"active":active,"last_seen":row["seen_at"] if row else None,
                "queued":q,"poll_seconds":8}
    finally:db.close()

def install(ns):
    _init(ns)
    app=ns["app"]

    @app.post("/api/v301/search")
    async def search(request:Request):
        origin=request.headers.get("origin")
        if origin and origin not in ("https://web-production-1c2ea.up.railway.app",):
            raise HTTPException(403,"Неверный источник запроса")
        raw=await request.body()
        if len(raw)>1000:raise HTTPException(413,"Слишком большой запрос")
        try:
            v=json.loads(raw)
            kind=v.get("kind")
            value=str(v.get("value","")).strip()
        except (ValueError,TypeError,AttributeError):
            raise HTTPException(400,"Некорректный запрос")
        if kind=="reg":
            if not REG.fullmatch(value):raise HTTPException(422,"Номер закупки 223-ФЗ должен содержать 11 цифр и начинаться с 3")
        elif kind=="inn":
            if not INN.fullmatch(value):raise HTTPException(422,"ИНН должен содержать 10 или 12 цифр")
        else:raise HTTPException(422,"Выберите поиск по номеру или ИНН")
        prior=await asyncio.to_thread(_local_results,ns,kind,value)
        local_recheck=False
        if kind=="reg" and prior and int(prior[0].get("documents_checked") or 0)>0:
            try:
                originals=allowed_documents(value)
                complete=bool(originals and all(saved_document(value,uid) for uid in originals))
                if complete:
                    fn=ns.get("_v295_enqueue_review")
                    if callable(fn):
                        ret=fn(value,force=True)
                        local_recheck=ret.get("status") in ("queued","running","completed")
                        print(f"V301 LOCAL_RECHECK reg={value} status={ret.get('status')}",flush=True)
            except Exception as exc:
                print(f"V301 RECHECK_FALLBACK reg={value} {type(exc).__name__}",flush=True)
        job,created=await asyncio.to_thread(_enqueue,ns,kind,value,1,0)
        if local_recheck:
            job=await asyncio.to_thread(
                _finish,ns,job["id"],"done",
                "Все подтверждённые оригиналы уже получены. Запущена локальная правовая перепроверка.",1,0)
        return {"id":job["id"],"status":job["status"],"kind":kind,
                "target":value,"new_request":created,
                "local_recheck":local_recheck,
                "existing":prior[:50],"bridge":await asyncio.to_thread(_bridge,ns)}

    @app.get("/api/v301/job/{job_id}")
    async def job(job_id:str):
        if not re.fullmatch(r"[0-9a-f]{24}",job_id):
            raise HTTPException(400,"Неверный номер задания")
        db=_db(ns)
        try:
            row=db.execute("SELECT * FROM v301_search_jobs WHERE id=?",(job_id,)).fetchone()
            if not row:raise HTTPException(404,"Задание не найдено")
            job=dict(row)
        finally:db.close()
        existing=await asyncio.to_thread(_local_results,ns,job["kind"],job["target"])
        return {"job":{k:v for k,v in job.items() if k!="lease_until"},
                "existing":existing[:100],"total_existing":len(existing),
                "bridge":await asyncio.to_thread(_bridge,ns)}

    @app.get("/api/v301/bridge/status")
    async def bridge_status():
        return await asyncio.to_thread(_bridge,ns)

    @app.get("/api/v301/bridge/next")
    async def next_job(request:Request):
        auth(request)
        if request.headers.get("origin")!="https://zakupki.gov.ru":
            raise HTTPException(403,"Доступно только официальной вкладке ЕИС")
        # Old browser code always requests /223/purchase/... (HTTP 404) and
        # would immediately exhaust the repaired queue. Require hot restart
        # with version 308 before handing out any new work.
        try:
            version=int(request.headers.get("x-eis223-collector-version") or "0")
        except ValueError:
            version=0
        if version<308:
            raise HTTPException(426,"Обновлён загрузчик ЕИС. Перезапустите код моста (v308) в браузере.")
        job=await asyncio.to_thread(_claim,ns)
        return {"job":{k:job[k] for k in ("id","kind","target","page","attempts")} if job else None,
                "poll_seconds":8}

    @app.post("/api/v301/bridge/report/{job_id}")
    async def report(job_id:str,request:Request):
        auth(request)
        if request.headers.get("origin")!="https://zakupki.gov.ru":
            raise HTTPException(403,"Доступно только официальной вкладке ЕИС")
        raw=await request.body()
        if len(raw)>2400:raise HTTPException(413,"Слишком много данных")
        try:
            data=json.loads(raw)
            status=str(data.get("status") or "")
            found=int(data.get("found") or 0)
            accepted=int(data.get("accepted") or 0)
            defer=int(data.get("defer") or 0)
            regs=data.get("regs",[])
            more=bool(data.get("more",False))
            detail=str(data.get("detail") or "")
        except (ValueError,TypeError,AttributeError):
            raise HTTPException(422,"Некорректный отчёт")
        job=await asyncio.to_thread(_finish,ns,job_id,status,detail,found,accepted,defer,regs,more)
        return {"received":True,"status":job["status"],"id":job["id"]}

    print("V308 QUEUE_READY modern_notice223=True stale_bridge_requires_reload=True",flush=True)
