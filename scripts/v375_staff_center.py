"""Employee action center: actual assigned work, authenticated findings and search.

All records are filtered by the current account. Workstation decisions are
separate from source EIS violations and do not alter evidence or totals.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from uuid import UUID
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from scripts.account_api_v1 import connect, current_account, require_origin
from scripts.account_session_policy_v1 import no_store_headers

router = APIRouter(prefix="/me/center", tags=["employee-action-center"])
REG = re.compile(r"^3[0-9]{10}$")
HASH = re.compile(r"^[0-9a-f]{32}$")


def own(request):
    return current_account(request)[0]


@router.get("/dashboard")
def dashboard(request: Request, response: Response):
    uid = own(request)
    response.headers.update(no_store_headers())
    with connect() as db:
        person = db.execute(
            "SELECT last_name,first_name,patronymic FROM account_users WHERE id=%s",
            (uid,),
        ).fetchone()
        tasks = db.execute(
            """SELECT id,reg_number,title,status,due_date,updated_at,
                      COUNT(*) FILTER (WHERE status IN
                        ('assigned','returned','in_progress','submitted')) OVER ()
               FROM account_manager_tasks WHERE user_id=%s
               ORDER BY CASE WHEN status IN ('assigned','returned') THEN 0
                             WHEN status='in_progress' THEN 1 ELSE 2 END,
                        updated_at DESC LIMIT 70""", (uid,),
        ).fetchall()
        campaigns = db.execute(
            """SELECT id,title,status,target_violations,due_date,batch_id,created_at,
                      COUNT(*) FILTER (WHERE status IN ('active','submitted')) OVER ()
               FROM account_manager_campaign_targets WHERE user_id=%s
               ORDER BY CASE WHEN status='active' THEN 0 ELSE 1 END,
                        created_at DESC LIMIT 60""", (uid,),
        ).fetchall()
        alerts = db.execute(
            "SELECT COUNT(*) FROM account_alert_events WHERE user_id=%s AND read_at IS NULL",
            (uid,),
        ).fetchone()[0]
        memo_review = db.execute(
            """SELECT status,COUNT(*) FROM account_manager_memo_reviews
               WHERE owner_id=%s GROUP BY status""", (uid,),
        ).fetchall()
        decisions = db.execute(
            """SELECT decision,COUNT(*) FROM account_staff_finding_decisions
               WHERE user_id=%s GROUP BY decision""", (uid,),
        ).fetchall()
        manual_count = db.execute(
            "SELECT COUNT(*) FROM account_manual_checks WHERE user_id=%s", (uid,),
        ).fetchone()[0]
    task_data = [
        {"id": str(x[0]), "reg_number": x[1], "title": x[2],
         "status": x[3], "due_date": x[4].isoformat() if x[4] else None,
         "updated_at": x[5].isoformat()}
        for x in tasks
    ]
    campaign_data = [
        {"id": str(x[0]), "title": x[1], "status": x[2],
         "target_violations": x[3],
         "due_date": x[4].isoformat() if x[4] else None,
         "batch_id": x[5]}
        for x in campaigns
    ]
    return {
        "full_name": " ".join(x for x in (person or ()) if x).strip(),
        "tasks": task_data, "campaigns": campaign_data,
        "task_active": int(tasks[0][6]) if tasks else 0,
        "campaign_active": int(campaigns[0][7]) if campaigns else 0,
        "unread_notifications": int(alerts), "manual_checks": int(manual_count),
        "memo_reviews": dict(memo_review), "finding_decisions": dict(decisions),
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/progress")
def progress(request: Request, response: Response):
    uid = own(request)
    response.headers.update(no_store_headers())
    with connect() as db:
        rows = db.execute(
            """SELECT reg_number,job_id,created_at,last_started_at
               FROM account_manual_checks WHERE user_id=%s
               ORDER BY last_started_at DESC LIMIT 60""", (uid,),
        ).fetchall()
    checks = [
        {"reg_number": x[0], "job_id": x[1],
         "created_at": x[2], "last_started_at": x[3]}
        for x in rows
    ]
    ns = getattr(request.app.state, "eis223_manual_ns", None)
    if not ns:
        return {"available": False, "checks": [],
                "note": "Серверный загрузчик ЕИС временно недоступен"}
    if not checks:
        return {"available": True, "checks": [], "note": "Проверки ещё не запускались"}
    from scripts.v366_account_manual import _read_only_status
    try:
        results = _read_only_status(ns, checks)
    except Exception:
        return {"available": False, "checks": [],
                "note": "Не удалось получить актуальное состояние ЕИС"}
    for item in results:
        documents = int(item.get("documents_checked") or 0)
        loaded = int(item.get("documents_found") or 0)
        legal_state = item.get("legal_review_state")
        if legal_state in ("pending", "queued"):
            step = "Юридическая проверка в очереди"
        elif legal_state in ("running", "processing"):
            step = "Идёт юридическая проверка"
        elif legal_state in ("failed", "error"):
            step = "Ошибка юридической проверки"
        elif legal_state == "no_readable_documents":
            step = "Не удалось прочитать документы для юридической проверки"
        elif legal_state == "completed":
            step = ("Юридическая проверка завершена" if int(item.get("legal_review_documents") or 0) > 0
                    else "Юридическая проверка не завершена: нет проверенных документов")
        elif loaded and documents:
            step = "Юридическая проверка ещё не завершена"
        elif loaded:
            step = "Документы получены"
        elif item.get("card_available"):
            step = "Получена карточка ЕИС"
        elif item.get("job_status") in ("failed", "error"):
            step = "Ошибка загрузки"
        else:
            step = "Ожидается получение сведений"
        item["stage"] = step
        item["previous_results_note"] = (
            "Показаны сохранённые результаты предыдущей проверки. Новая проверка ещё не завершена."
            if legal_state in ("pending", "queued", "running", "processing", "failed", "error")
            and (documents > 0 or int(item.get("violations") or 0) > 0) else "")
    return {"available": True, "checks": results,
            "note": "Этапы формируются из сохранённых фактов; нет документов — нет подтверждённой проверки"}


class FindingDecision(BaseModel):
    reg_number: str = Field(pattern=r"^3[0-9]{10}$")
    decision: str = Field(pattern=r"^(confirmed|rejected|recheck|unreviewed)$")
    comment: str = Field(default="", max_length=1000)


@router.post("/findings/{key}/decision")
def finding_decision(key: str, data: FindingDecision, request: Request):
    require_origin(request)
    uid = own(request)
    if not HASH.fullmatch(key):
        raise HTTPException(422, "Неверный идентификатор нарушения")
    # Validate the key against live, owner-scoped thematic audit findings.
    from scripts.v370_manager_campaigns import staff_campaign_violations
    current = staff_campaign_violations(request, Response())
    if not any(x.get("finding_id") == key and x["reg_number"] == data.reg_number
               for x in current.get("findings", ())):
        raise HTTPException(404, "Нарушение не найдено среди ваших проверок")
    with connect() as db:
        if data.decision == "unreviewed":
            db.execute(
                """DELETE FROM account_staff_finding_decisions
                   WHERE user_id=%s AND finding_key=%s""", (uid, key),
            )
        else:
            db.execute(
                """INSERT INTO account_staff_finding_decisions
                   (user_id,finding_key,reg_number,decision,comment,updated_at)
                   VALUES (%s,%s,%s,%s,%s,CURRENT_TIMESTAMP)
                   ON CONFLICT(user_id,finding_key) DO UPDATE
                   SET decision=EXCLUDED.decision,comment=EXCLUDED.comment,
                       updated_at=CURRENT_TIMESTAMP""",
                (uid, key, data.reg_number, data.decision, data.comment.strip()),
            )
        db.execute(
            """INSERT INTO account_manager_actions
               (actor_id,target_user_id,entity_type,entity_id,action,details)
               VALUES(%s,%s,'finding_decision',%s,%s,%s)""",
            (uid, uid, key, data.decision, data.reg_number),
        )
    return {"saved": True, "finding_id": key, "decision": data.decision,
            "note": "Решение сохранено отдельно от исходных результатов ЕИС"}


class DraftUpdate(BaseModel):
    body: str = Field(default="", max_length=3000)


@router.get("/draft/{reg}")
def read_draft(reg: str, request: Request, response: Response):
    from scripts.v370_staff_workbench import case_owner
    uid = case_owner(request, reg)
    response.headers.update(no_store_headers())
    with connect() as db:
        row = db.execute(
            "SELECT body,updated_at FROM account_staff_drafts WHERE user_id=%s AND reg_number=%s",
            (uid, reg),
        ).fetchone()
    return {"body": row[0] if row else "",
            "updated_at": row[1].isoformat() if row else None}


@router.put("/draft/{reg}")
def save_draft(reg: str, data: DraftUpdate, request: Request):
    require_origin(request)
    from scripts.v370_staff_workbench import case_owner
    uid = case_owner(request, reg)
    with connect() as db:
        db.execute(
            """INSERT INTO account_staff_drafts (user_id,reg_number,body)
               VALUES(%s,%s,%s)
               ON CONFLICT(user_id,reg_number) DO UPDATE
               SET body=EXCLUDED.body,updated_at=CURRENT_TIMESTAMP""",
            (uid, reg, data.body),
        )
    return {"saved": True}


class SavedSearch(BaseModel):
    phrase: str = Field(min_length=2, max_length=80)


@router.get("/saved-searches")
def saved_searches(request: Request, response: Response):
    uid = own(request)
    response.headers.update(no_store_headers())
    with connect() as db:
        rows = db.execute(
            """SELECT id,phrase FROM account_staff_saved_searches
               WHERE user_id=%s ORDER BY created_at DESC LIMIT 15""", (uid,),
        ).fetchall()
    return [{"id": str(x[0]), "phrase": x[1]} for x in rows]


@router.post("/saved-searches", status_code=201)
def save_search(data: SavedSearch, request: Request):
    require_origin(request)
    from uuid import uuid4
    uid = own(request)
    phrase = " ".join(data.phrase.split())
    if len(phrase) < 2:
        raise HTTPException(422, "Введите поисковую фразу")
    with connect() as db:
        db.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (str(uid),))
        existing = db.execute(
            """SELECT id FROM account_staff_saved_searches
               WHERE user_id=%s AND phrase=%s""", (uid, phrase),
        ).fetchone()
        if existing:
            return {"saved": True, "id": str(existing[0]), "existing": True}
        count = db.execute(
            "SELECT COUNT(*) FROM account_staff_saved_searches WHERE user_id=%s",
            (uid,),
        ).fetchone()[0]
        if count >= 15:
            raise HTTPException(409, "Можно сохранить не более 15 поисковых запросов")
        identity = uuid4()
        db.execute(
            """INSERT INTO account_staff_saved_searches(id,user_id,phrase)
               VALUES(%s,%s,%s)""", (identity, uid, phrase),
        )
    return {"saved": True, "id": str(identity), "existing": False}


@router.delete("/saved-searches/{identifier}")
def delete_search(identifier: UUID, request: Request):
    require_origin(request)
    uid = own(request)
    with connect() as db:
        db.execute(
            "DELETE FROM account_staff_saved_searches WHERE user_id=%s AND id=%s",
            (uid, identifier),
        )
    return {"deleted": True}


@router.get("/search")
def search(request: Request, response: Response, q: str):
    uid = own(request)
    response.headers.update(no_store_headers())
    query = " ".join(str(q).split())[:80]
    if len(query) < 2:
        raise HTTPException(422, "Введите не менее двух символов")
    literal = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = "%" + literal + "%"
    with connect() as db:
        manual = db.execute(
            """SELECT reg_number,last_started_at FROM account_manual_checks
               WHERE user_id=%s AND reg_number ILIKE %s ESCAPE '\\'
               ORDER BY last_started_at DESC LIMIT 25""", (uid, pattern),
        ).fetchall()
        tasks = db.execute(
            """SELECT reg_number,title,status FROM account_manager_tasks
               WHERE user_id=%s AND (reg_number ILIKE %s ESCAPE '\\'
                                    OR title ILIKE %s ESCAPE '\\')
               ORDER BY updated_at DESC LIMIT 25""", (uid, pattern, pattern),
        ).fetchall()
        cases = db.execute(
            """SELECT reg_number,title,status FROM account_staff_manual_findings
               WHERE user_id=%s AND (reg_number ILIKE %s ESCAPE '\\'
                                    OR title ILIKE %s ESCAPE '\\')
               ORDER BY created_at DESC LIMIT 25""", (uid, pattern, pattern),
        ).fetchall()
        campaigns = db.execute(
            """SELECT title,batch_id,status FROM account_manager_campaign_targets
               WHERE user_id=%s AND (title ILIKE %s ESCAPE '\\'
                                    OR batch_id ILIKE %s ESCAPE '\\')
               ORDER BY created_at DESC LIMIT 25""", (uid, pattern, pattern),
        ).fetchall()
    rows = []
    seen = set()
    for reg, date in manual:
        key = ("check", reg)
        if key not in seen:
            rows.append({"kind": "check", "title": "Самостоятельная закупка № " + reg,
                         "reg_number": reg, "status": "Сохранена"})
            seen.add(key)
    for reg, title, status in tasks:
        key = ("task", reg, title)
        if key not in seen:
            rows.append({"kind": "task", "title": title, "reg_number": reg, "status": status})
            seen.add(key)
    for reg, title, status in cases:
        key = ("finding", reg, title)
        if key not in seen:
            rows.append({"kind": "finding", "title": title, "reg_number": reg, "status": status})
            seen.add(key)
    for title, batch, status in campaigns:
        rows.append({"kind": "campaign", "title": title, "batch_id": batch, "status": status})
    return {"results": rows[:60], "scope": "Только ваши сохранённые закупки, поручения и предложения",
            "truncated": len(rows) > 60}
