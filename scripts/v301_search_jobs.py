"""Persistent 223-FZ search queue; deployed EIS retrieval runs on the VPS.

The site validates delivered original bytes and performs local checks.
"""
from __future__ import annotations
import asyncio
import json
import os
import re
import secrets
import sqlite3
import time
from datetime import datetime,timezone
from pathlib import Path
from fastapi import HTTPException,Request
from v295_eis_originals import (auth,allowed_documents,saved_document,root,uid_from_url,EXT,
                                MAX_KNOWN_DOCUMENTS,MAX_SOURCE_MANIFEST_BYTES)
from v319_manual_inputs import migrate_legacy_inputs,record_input

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
        # Fence server leases from late reports sent by a browser collector.
        columns={r[1] for r in db.execute("PRAGMA table_info(v301_search_jobs)")}
        for name in ("lease_owner","lease_token"):
            if name not in columns:
                db.execute("ALTER TABLE v301_search_jobs ADD COLUMN "+name+" TEXT NOT NULL DEFAULT ''")
        # Repair the backlog produced by the obsolete /223/purchase/... URL.
        # These jobs are NOT regarded as successful checks. They wait for the
        # upgraded browser bridge to retrieve real original-file bytes.
        repaired=db.execute(
            "UPDATE v301_search_jobs SET status='queued',attempts=0,not_before=0,"
            "lease_until=0,updated_at=? "
            "WHERE status='partial' AND kind='reg' "
            "AND detail LIKE 'ЕИС HTTP 404 /223/purchase/%'",
            (_utc(),)).rowcount
        # One-time migration: prior bad URL construction caused some jobs to
        # be labeled done after retrieving only one of many EIS attachments.
        # Requeue known affected procurements without inventing new findings.
        db.execute("""CREATE TABLE IF NOT EXISTS v313_migrations(
                     marker TEXT PRIMARY KEY, migrated_at TEXT NOT NULL)""")
        applied=db.execute(
            "SELECT 1 FROM v313_migrations WHERE marker='repair_404_noticeId'"
        ).fetchone()
        if not applied:
            queued_rechecks=0
            for target in ("32616280442","32616461370","32616417059","32616467038"):
                existing=db.execute(
                    "SELECT 1 FROM v301_search_jobs "
                    "WHERE kind='reg' AND target=? "
                    "AND status IN ('queued','leased','deferred') LIMIT 1",
                    (target,)).fetchone()
                if existing:continue
                active=db.execute(
                    "SELECT COUNT(*) FROM v301_search_jobs "
                    "WHERE status IN ('queued','leased','deferred')"
                ).fetchone()[0]
                if active>=MAX_QUEUE:break
                db.execute(
                    "INSERT INTO v301_search_jobs "
                    "(id,kind,target,page,status,priority,created_at,updated_at) "
                    "VALUES (?,'reg',?,1,'queued',2,?,?)",
                    (secrets.token_hex(12),target,_utc(),_utc()))
                queued_rechecks+=1
            db.execute(
                "INSERT OR IGNORE INTO v313_migrations(marker,migrated_at)"
                " VALUES ('repair_404_noticeId',?)",(_utc(),))
            print(f"V313 NOTICE_ID_RECHECK_QUEUED count={queued_rechecks}",flush=True)
        migrated=migrate_legacy_inputs(db)
        db.commit()
        print(f"V319 MANUAL_INPUTS_READY migrated={migrated} discovery_is_manual=False",flush=True)
        if repaired:
            print(f"V308 RETRY_OLD_404_BACKLOG jobs={repaired}",flush=True)
    finally:db.close()

def _utc():return datetime.now(timezone.utc).isoformat()

def _local_results(ns,kind,target):
    db=_db(ns)
    try:
        if kind=="reg":
            rows=db.execute(
                "SELECT reg_number,title,customer,price,price_num,violations,documents_checked,documents_found "
                "FROM purchases WHERE reg_number=?",(target,)).fetchall()
        elif os.environ.get("EIS223_SOURCE_MODE")=="OFFICIAL_EIS_VPS_ONLY":
            # Search-page candidates and legacy associations are not evidence
            # of the buyer. Official XML intake supplies customer_inn.
            rows=db.execute(
                "SELECT p.reg_number,p.title,p.customer,p.price,p.price_num,p.violations,p.documents_checked,p.documents_found "
                "FROM purchases p WHERE p.customer_inn=? ORDER BY p.last_seen DESC LIMIT 250",
                (target,)).fetchall()
        else:
            rows=db.execute(
                "SELECT DISTINCT p.reg_number,p.title,p.customer,p.price,p.price_num,p.violations,p.documents_checked,p.documents_found "
                "FROM purchases p LEFT JOIN v301_inn_purchases m ON m.reg=p.reg_number "
                "WHERE p.customer_inn=? OR m.inn=? ORDER BY p.last_seen DESC LIMIT 250",
                (target,target)).fetchall()
        return [dict(r) for r in rows]
    finally:db.close()

def _enqueue(ns,kind,target,page=1,priority=10,parent_id="",user_input=False):
    now=_utc()
    db=_db(ns)
    try:
        db.execute("BEGIN IMMEDIATE")
        old=db.execute(
            "SELECT * FROM v301_search_jobs WHERE kind=? AND target=? AND page=? "
            "AND status IN ('queued','leased','deferred') ORDER BY created_at DESC LIMIT 1",
            (kind,target,page)).fetchone()
        if old:
            # The user may enter a number already queued by an INN search.
            # Reuse the download work, but retain this explicit submission.
            if user_input and kind=="reg":
                record_input(db,target,job_id=old["id"],requested_at=now)
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
        if user_input and kind=="reg":
            record_input(db,target,job_id=identifier,requested_at=now)
        db.commit()
        row=db.execute("SELECT * FROM v301_search_jobs WHERE id=?",(identifier,)).fetchone()
        print(f"V301 SEARCH_QUEUED kind={kind} target={target} page={page}",flush=True)
        return dict(row),True
    except:
        if db.in_transaction:db.rollback()
        raise
    finally:db.close()

def _stored_download_complete(reg):
    """Confirm originals locally, independently of browser report counters."""
    if not REG.fullmatch(reg):return False
    path=root()/"eis223_public_browser_intake"/reg/"manifest.json"
    try:
        with path.open("rb") as stream:snapshot=stream.read(MAX_SOURCE_MANIFEST_BYTES+1)
        if len(snapshot)>MAX_SOURCE_MANIFEST_BYTES:return False
        manifest=json.loads(snapshot)
        if (not isinstance(manifest,dict) or manifest.get("reg_number")!=reg
                or manifest.get("source")!="public_eis_223_browser"):
            return False
        declared=int(manifest.get("declared_attachment_count") or 0)
        documents=manifest.get("documents")
        if not isinstance(documents,list) or len(documents)>MAX_KNOWN_DOCUMENTS:return False
        refs=allowed_documents(reg)
        browser_refs=set()
        for item in documents:
            if not isinstance(item,dict):continue
            uid=uid_from_url(item.get("url",""))
            name=Path(item.get("name") or "").name
            ext=Path(name).suffix.lower()
            if (uid and name and len(name)<=255 and ext in EXT
                    and uid in refs and refs[uid]["ext"]==ext):
                browser_refs.add(uid)
        # Historical SOAP references also enter allowed_documents. They
        # cannot stand in for as-yet unregistered browser-page attachments.
        if not 0<declared<=len(browser_refs):return False
        if not all(saved_document(reg,uid) for uid in refs):return False
        # Do not finish against a manifest that changed during receipt checks.
        with path.open("rb") as stream:return stream.read(MAX_SOURCE_MANIFEST_BYTES+1)==snapshot
    except (OSError,ValueError,TypeError,AttributeError,HTTPException):
        return False

def _finish(ns,job_id,status,detail,found,accepted,defer=0,regs=None,more=False):
    if status not in ("done","partial","error","deferred"):status="error"
    db=_db(ns)
    try:
        row=db.execute("SELECT * FROM v301_search_jobs WHERE id=?",(job_id,)).fetchone()
        if not row:
            raise HTTPException(404,"Неизвестный запрос")
        if row["lease_owner"]:
            raise HTTPException(409,"Задание принадлежит серверному загрузчику")
        if row["status"] not in ("leased","queued","deferred"):
            return dict(row)
        # Existing v376 tabs report partial while the offline audit is still
        # running even after all files arrived. Check trusted server receipts
        # outside the SQLite write lock so they need no browser restart.
        target=row["target"]
        downloaded=(status=="partial" and row["kind"]=="reg"
                    and row["parent_id"]!="v341-card-facts"
                    and _stored_download_complete(target))
        db.execute("BEGIN IMMEDIATE")
        row=db.execute("SELECT * FROM v301_search_jobs WHERE id=?",(job_id,)).fetchone()
        if not row:
            raise HTTPException(404,"Неизвестный запрос")
        if row["lease_owner"]:
            raise HTTPException(409,"Задание принадлежит серверному загрузчику")
        if row["status"] not in ("leased","queued","deferred"):
            db.commit()
            return dict(row)
        if (downloaded and row["kind"]=="reg" and row["target"]==target
                and row["parent_id"]!="v341-card-facts"):
            status="done"
            detail="Все обнаруженные оригиналы ЕИС получены и переданы на проверку на сервере"
        valid=[]
        for reg in (regs if isinstance(regs,list) else [])[:40]:
            if isinstance(reg,str) and REG.fullmatch(reg) and reg not in valid:valid.append(reg)
        # INN results stay in the discovery/download queue. They never
        # enter the history of registry numbers submitted through the field.
        now=_utc()
        nb=int(time.time())+min(1800,max(60,defer)) if status=="deferred" else 0
        effective="queued" if status=="deferred" else status
        attempts=int(row["attempts"])
        # A deferred infrastructure/rate-limit attempt did not establish an
        # unavailable original. Refund only the active lease, so replaying a
        # report after a lost HTTP response cannot refund the budget twice.
        if status=="deferred" and row["status"]=="leased":
            attempts=max(0,attempts-1)
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
        print(f"V308 SEARCH_REPORT kind={row['kind']} target={row['target']} status={status} "
              f"next_state={effective} found={len(valid)} imported={accepted} "
              f"reason={str(detail)[:180]}",flush=True)
        return dict(db.execute("SELECT * FROM v301_search_jobs WHERE id=?",(job_id,)).fetchone())
    except:
        if db.in_transaction:db.rollback()
        raise
    finally:db.close()

def _claim(ns,collector_version=320):
    db=_db(ns)
    now=int(time.time())
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "UPDATE v301_search_jobs SET status='queued',not_before=?,lease_owner=CASE WHEN lease_owner<>'' THEN 'server-retry' ELSE '' END,lease_token='' "
            "WHERE status='leased' AND lease_until<? AND attempts<4",(now+30,now))
        db.execute(
            "UPDATE v301_search_jobs SET status='error',detail='Истёк лимит повторных попыток',lease_owner='',lease_token='' "
            "WHERE status='leased' AND lease_until<? AND attempts>=4",(now,))
        row=db.execute(
            "SELECT * FROM v301_search_jobs WHERE status='queued' AND not_before<=? "
            "AND (? >= 341 OR COALESCE(parent_id,'') <> 'v341-card-facts') "
            "ORDER BY priority ASC,created_at ASC LIMIT 1",(now,collector_version)).fetchone()
        if row:
            db.execute(
                "UPDATE v301_search_jobs SET status='leased',lease_until=?,"
                "attempts=attempts+1,updated_at=?,lease_owner='',lease_token='' WHERE id=?",
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
        vps_only=os.environ.get("EIS223_SOURCE_MODE")=="OFFICIAL_EIS_VPS_ONLY"
        active=bool(not vps_only and row and int(time.time())-row["seen_at"]<40)
        q=db.execute("SELECT COUNT(*) FROM v301_search_jobs WHERE status IN ('queued','leased')").fetchone()[0]
        # A VPS heartbeat is distinct from an official browser heartbeat.
        try:
            from eis_server_worker import health
            server=health(ns)
        except (sqlite3.Error,ImportError):
            server={"active":False,"last_seen":None}
        return {"active":active or server["active"],
                "browser_active":active,"server_active":server["active"],
                "browser_collector_enabled":not vps_only,
                "intake_mode":"vps_only" if vps_only else "server_and_browser",
                "server_last_seen":server.get("last_seen"),
                "server_status":server.get("status"),
                "inn_search_available":server.get("inn_search_available",False),
                "discovery_status":server.get("discovery_status"),
                "last_seen":max(row["seen_at"] if row and not vps_only else 0,
                                server.get("last_seen") or 0) or None,
                "queued":q,"poll_seconds":8}
    finally:db.close()

def install(ns):
    _init(ns)
    app=ns["app"]
    # Shared engine reference is used only by owner-scoped /me/manual-checks.
    # This is not a public service or a new background worker.
    app.state.eis223_manual_ns=ns

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
        if kind=="reg":
            # A previous failed legal review can leave saved originals but
            # purchases.documents_checked=0. Do not wait for the browser in
            # that case: requeue the genuinely saved files for local analysis.
            try:
                originals=allowed_documents(value)
                complete=bool(originals and all(saved_document(value,uid) for uid in originals))
                if complete:
                    fn=ns.get("_v295_enqueue_review")
                    if callable(fn):
                        ret=fn(value,force=True)
                        local_recheck=ret.get("status") in ("queued","running","completed")
                        print(f"V322 LOCAL_RECHECK reg={value} status={ret.get('status')}",flush=True)
            except Exception as exc:
                print(f"V322 RECHECK_FALLBACK reg={value} {type(exc).__name__}",flush=True)
        job,created=await asyncio.to_thread(_enqueue,ns,kind,value,1,0,"",kind=="reg")
        if local_recheck:
            job=await asyncio.to_thread(
                _finish,ns,job["id"],"done",
                "Сохранённые оригиналы найдены; локальная юридическая проверка запущена, результат ожидается.",1,0)
        # Explicit registry submissions from the existing manual-search UI are
        # associated with the actual signed-in owner. An INN lookup and every
        # background discovery job remain outside personal manual checks.
        owner_id=request.scope.get("eis223_account_id")
        if kind=="reg" and owner_id:
            from scripts.v366_account_manual import link_run
            try:
                await asyncio.to_thread(link_run,owner_id,value,job["id"])
            except Exception as exc:
                raise HTTPException(503,
                    "Запрос ЕИС принят, но привязка к кабинету недоступна. "
                    "Повторный запуск не дублирует ожидающее задание.") from exc
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
        audit=None
        search_progress={}
        if job["kind"]=="reg":
            from v257_features import manual_progress
            checked=int(existing[0].get("documents_checked") or 0) if existing else 0
            audit=await asyncio.to_thread(manual_progress,job["target"],checked,job["status"])
        elif os.environ.get("EIS223_SOURCE_MODE")=="OFFICIAL_EIS_VPS_ONLY":
            from eis_server_worker import inn_progress
            search_progress=await asyncio.to_thread(inn_progress,ns,job_id)
        return {"job":{k:v for k,v in job.items() if k not in ("lease_until","lease_owner","lease_token")},
                "existing":existing[:100],"total_existing":len(existing),
                **search_progress,
                "audit":audit,"bridge":await asyncio.to_thread(_bridge,ns)}

    @app.get("/api/v301/bridge/status")
    async def bridge_status():
        return await asyncio.to_thread(_bridge,ns)

    @app.get("/api/v301/bridge/next")
    async def next_job(request:Request):
        auth(request)
        if request.headers.get("origin")!="https://zakupki.gov.ru":
            raise HTTPException(403,"Доступно только официальной вкладке ЕИС")
        # Manual EIS lookup requires the v320 client: previous v318 code
        # shipped an invalid JavaScript regex and couldn't run correctly.
        # Keep the general browser intake untouched so existing collectors
        # can still download original files until they are restarted.
        try:
            version=int(request.headers.get("x-eis223-collector-version") or "0")
        except ValueError:
            version=0
        if version<320:
            raise HTTPException(426,"Для самостоятельного поиска нужен обновлённый браузерный мост v320. Перезапустите загрузчик во вкладке ЕИС.")
        job=await asyncio.to_thread(_claim,ns,version)
        result={k:job[k] for k in ("id","kind","target","page","attempts")} if job else None
        if result:
            result["metadata_only"]=job.get("parent_id")=="v341-card-facts"
        return {"job":result,
                "poll_seconds":8}

    @app.post("/api/v301/bridge/report/{job_id}")
    async def report(job_id:str,request:Request):
        auth(request)
        if request.headers.get("origin")!="https://zakupki.gov.ru":
            raise HTTPException(403,"Доступно только официальной вкладке ЕИС")
        try:
            version=int(request.headers.get("x-eis223-collector-version") or "0")
        except ValueError:
            version=0
        if version<320:
            raise HTTPException(426,"Отчёт старой версии моста отклонён; запустите v320")
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
        # A search report is NOT a finished legal audit. Whenever saved
        # originals are complete, ensure the existing offline review runs.
        if (job["kind"]=="reg" and job.get("parent_id")!="v341-card-facts"
                and (found>0 or accepted>0) and status in ("done","partial")):
            try:
                enqueue=ns.get("_v295_enqueue_review")
                if callable(enqueue):
                    review=enqueue(job["target"])
                    print(f"V322 AUDIT_RECONCILE reg={job['target']} state={review.get('status')}",flush=True)
            except Exception as exc:
                print(f"V322 AUDIT_RECONCILE_ERROR reg={job['target']} type={type(exc).__name__}",flush=True)
        return {"received":True,"status":job["status"],"id":job["id"]}

    print("V308 QUEUE_READY modern_notice223=True stale_bridge_requires_reload=True",flush=True)
