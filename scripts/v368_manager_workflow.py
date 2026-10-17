"""Manager access, delegated staff, assigned work and document approvals.

A manager can see ONLY employees linked to their team by an administrator.
Shared EIS findings and originals are never mutated. Every managerial decision
is recorded in the isolated PostgreSQL identity database with actor and time.
"""
from __future__ import annotations

from datetime import date
from uuid import UUID,uuid4
import re
import time

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from scripts.account_api_v1 import connect,current_account,require_origin
from scripts.account_session_policy_v1 import no_store_headers

router=APIRouter(prefix="/manager",tags=["manager-workflow"])
employee_router=APIRouter(prefix="/me/assignments",tags=["employee-assignments"])
REG=re.compile(r"^3[0-9]{10}$")

def supervisor(request):
    account=current_account(request)
    if account[1] not in ("manager","admin"):
        raise HTTPException(403,"Требуются права руководителя")
    return account[0],account[1]

def administrator(request):
    account=current_account(request,admin=True)
    return account[0]

def _uuid(identifier):
    try:return UUID(str(identifier))
    except (ValueError,TypeError) as exc:
        raise HTTPException(422,"Некорректная учётная запись") from exc

def _member(db,actor,role,user_id):
    user_id=_uuid(user_id)
    if role=="admin":
        row=db.execute(
            "SELECT 1 FROM account_users WHERE id=%s AND role<>'admin' AND status='approved'",
            (user_id,)).fetchone()
    else:
        row=db.execute(
            """SELECT 1 FROM account_manager_teams t
               JOIN account_users u ON u.id=t.user_id
               WHERE t.manager_id=%s AND t.user_id=%s AND u.status='approved'
               AND u.role='user'""",(actor,user_id)).fetchone()
    if not row:raise HTTPException(404,"Сотрудник не найден в вашем подразделении")
    return user_id

def _audit(db,actor,subject,kind,entity,action,details=""):
    db.execute(
        """INSERT INTO account_manager_actions
           (actor_id,target_user_id,entity_type,entity_id,action,details)
           VALUES(%s,%s,%s,%s,%s,%s)""",
        (actor,subject,kind,str(entity),action,str(details)[:1500]))

class RoleUpdate(BaseModel):
    role:str=Field(pattern="^(user|manager|admin)$")

class Assignment(BaseModel):
    manager_id:UUID|None

class TaskCreate(BaseModel):
    assignee_id:UUID
    reg_number:str=Field(pattern=r"^3[0-9]{10}$")
    title:str=Field(min_length=3,max_length=180)
    note:str=Field(default="",max_length=2000)
    due_date:date|None=None

class TaskDecision(BaseModel):
    action:str=Field(pattern="^(return|complete|cancel)$")
    comment:str=Field(default="",max_length=1200)

class MemoDecision(BaseModel):
    decision:str=Field(pattern="^(approved|returned|rejected)$")
    comment:str=Field(default="",max_length=1500)

@router.get("/admin/mail/status")
def admin_mail_status(request:Request,response:Response):
    administrator(request)
    response.headers.update(no_store_headers())
    from scripts.account_notifications_v1 import email_provider
    with connect() as db:
        metrics=db.execute(
            """SELECT COUNT(*) FILTER (WHERE sent_at IS NULL AND attempts < 12),
                      COUNT(*) FILTER (WHERE sent_at IS NOT NULL),
                      COUNT(*) FILTER (WHERE sent_at IS NULL AND attempts >= 12)
               FROM account_mail_outbox"""
        ).fetchone()
    provider=email_provider()
    return {"configured":bool(provider),"provider":provider,
            "pending":int(metrics[0]),"accepted":int(metrics[1]),
            "failed":int(metrics[2]),
            "note":"Принято сервисом отправки — не подтверждение доставки во входящие."}


@router.post("/admin/mail/test",status_code=202)
def admin_mail_test(request:Request):
    require_origin(request)
    admin=administrator(request)
    from scripts.account_notifications_v1 import enqueue,email_provider
    if not email_provider():
        raise HTTPException(503,"Почтовый сервис не настроен в Railway")
    window=int(time.time()//600)
    with connect() as db:
        enqueue(db,admin,"test:email:"+str(window),
                "ЕИС Аудитор — проверка почтовых уведомлений",
                "Проверка отправки писем из ЕИС Аудитор. "
                "Это письмо направлено на адрес, указанный при регистрации.")
    return {"queued":True,"note":"Тестовое письмо поставлено в очередь на почту регистрации."}


@router.get("/admin/users")
def admin_users(request:Request,response:Response):
    actor=administrator(request)
    response.headers.update(no_store_headers())
    with connect() as db:
        rows=db.execute(
            """SELECT u.id,u.email,u.role,u.status,t.manager_id,
                      u.last_name,u.first_name,u.patronymic
               FROM account_users u
               LEFT JOIN account_manager_teams t ON t.user_id=u.id
               ORDER BY lower(u.email) ASC LIMIT 250""").fetchall()
    return [{"id":str(r[0]),"email":r[1],"role":r[2],
             "status":r[3],"manager_id":str(r[4]) if r[4] else None,
             "full_name":" ".join(x for x in (r[5],r[6],r[7]) if x),
             "is_self":r[0]==actor} for r in rows]

@router.post("/admin/users/{user_id}/role")
def admin_change_role(user_id:str,data:RoleUpdate,request:Request):
    require_origin(request)
    admin=administrator(request);target=_uuid(user_id)
    if admin==target:raise HTTPException(400,"Нельзя изменить собственную роль")
    with connect() as db:
        row=db.execute(
            "SELECT role,status FROM account_users WHERE id=%s FOR UPDATE",(target,)
        ).fetchone()
        if not row or row[1]!="approved":
            raise HTTPException(404,"Действующая учётная запись сотрудника не найдена")
        if row[0]==data.role:
            return {"id":str(target),"role":data.role,"changed":False}
        if row[0]=="manager":
            members=db.execute(
                "SELECT 1 FROM account_manager_teams WHERE manager_id=%s LIMIT 1",
                (target,)).fetchone()
            if members:
                raise HTTPException(409,"Сначала переназначьте сотрудников руководителя")
        if data.role in ("manager","admin"):
            # Elevated roles cannot remain listed as ordinary staff.
            db.execute("DELETE FROM account_manager_teams WHERE user_id=%s",(target,))
        db.execute("UPDATE account_users SET role=%s WHERE id=%s",(data.role,target))
        _audit(db,admin,target,"role",target,"set_"+data.role)
        from scripts.account_notifications_v1 import enqueue_role_change
        enqueue_role_change(db,target,data.role,uuid4().hex)
    return {"id":str(target),"role":data.role,"changed":True}

@router.put("/admin/team/{user_id}")
def admin_assign_team(user_id:str,data:Assignment,request:Request):
    require_origin(request)
    admin=administrator(request);target=_uuid(user_id)
    with connect() as db:
        employee=db.execute(
            "SELECT role,status FROM account_users WHERE id=%s",(target,)
        ).fetchone()
        if not employee or employee!=("user","approved"):
            raise HTTPException(409,"В подразделение можно включить только одобренного сотрудника")
        if data.manager_id is None:
            db.execute("DELETE FROM account_manager_teams WHERE user_id=%s",(target,))
            _audit(db,admin,target,"team",target,"unassign")
            return {"employee":str(target),"manager":None}
        manager=db.execute(
            "SELECT 1 FROM account_users WHERE id=%s AND role='manager' AND status='approved'",
            (data.manager_id,)).fetchone()
        if not manager:
            raise HTTPException(404,"Назначенный руководитель не найден")
        db.execute(
            """INSERT INTO account_manager_teams
                 (user_id,manager_id,assigned_by) VALUES(%s,%s,%s)
               ON CONFLICT(user_id) DO UPDATE SET
                 manager_id=EXCLUDED.manager_id,assigned_by=EXCLUDED.assigned_by,
                 assigned_at=CURRENT_TIMESTAMP""",
            (target,data.manager_id,admin))
        _audit(db,admin,target,"team",target,"assign",str(data.manager_id))
    return {"employee":str(target),"manager":str(data.manager_id)}

@router.get("/team")
def team(request:Request,response:Response):
    actor,role=supervisor(request)
    response.headers.update(no_store_headers())
    with connect() as db:
        if role=="admin":
            rows=db.execute(
                """SELECT u.id,u.email,u.role,u.status,t.manager_id,m.email,
                          u.last_name,u.first_name,u.patronymic
                   FROM account_users u
                   LEFT JOIN account_manager_teams t ON t.user_id=u.id
                   LEFT JOIN account_users m ON m.id=t.manager_id
                   WHERE u.role<>'admin' ORDER BY lower(u.email) LIMIT 300"""
            ).fetchall()
        else:
            rows=db.execute(
                """SELECT u.id,u.email,u.role,u.status,t.manager_id,m.email,
                          u.last_name,u.first_name,u.patronymic
                   FROM account_manager_teams t
                   JOIN account_users u ON u.id=t.user_id
                   JOIN account_users m ON m.id=t.manager_id
                   WHERE t.manager_id=%s AND u.status='approved'
                   ORDER BY lower(u.email) LIMIT 200""",(actor,)).fetchall()
    return [{"id":str(r[0]),"email":r[1],"role":r[2],"status":r[3],
             "manager_id":str(r[4]) if r[4] else None,"manager_email":r[5],
             "full_name":" ".join(part for part in (r[6],r[7],r[8]) if part).strip()} for r in rows]

def _task_rows(db,actor,role):
    where=("WHERE t.manager_id=%s AND mt.manager_id=%s"
           if role!="admin" else "")
    params=(actor,actor) if role!="admin" else ()
    return db.execute(
        """SELECT t.id,t.user_id,u.email,t.reg_number,t.title,t.note,t.due_date,
                  t.status,t.created_at,t.updated_at,t.manager_id
           FROM account_manager_tasks t
           JOIN account_users u ON u.id=t.user_id
           LEFT JOIN account_manager_teams mt ON mt.user_id=t.user_id
           """+where+""" ORDER BY t.created_at DESC LIMIT 400""",params).fetchall()

def _task_dict(row):
    return {"id":str(row[0]),"user_id":str(row[1]),"employee":row[2],
            "reg_number":row[3],"title":row[4],"note":row[5],
            "due_date":row[6].isoformat() if row[6] else None,
            "status":row[7],"created_at":row[8].isoformat(),
            "updated_at":row[9].isoformat(),"manager_id":str(row[10]),
            "overdue":bool(row[6] and row[6]<date.today() and
                row[7] not in ("completed","cancelled"))}

@router.get("/tasks")
def tasks(request:Request,response:Response):
    actor,role=supervisor(request)
    response.headers.update(no_store_headers())
    with connect() as db:
        return [_task_dict(row) for row in _task_rows(db,actor,role)]

@router.post("/tasks",status_code=201)
def create_task(data:TaskCreate,request:Request):
    require_origin(request)
    actor,role=supervisor(request)
    if not REG.fullmatch(data.reg_number):raise HTTPException(422,"Неверный номер ЕИС")
    if data.due_date and data.due_date<date.today():
        raise HTTPException(422,"Срок исполнения не может быть в прошлом")
    task_id=uuid4()
    with connect() as db:
        employee=_member(db,actor,role,data.assignee_id)
        # An administrator can assign on behalf of an explicitly designated
        # manager, but an unassigned employee cannot silently become a team.
        manager_id=(db.execute(
            "SELECT manager_id FROM account_manager_teams WHERE user_id=%s",
            (employee,)).fetchone())
        if not manager_id or (role!="admin" and manager_id[0]!=actor):
            raise HTTPException(409,"Сначала назначьте сотруднику руководителя")
        existing=db.execute(
            """SELECT id FROM account_manager_tasks
               WHERE manager_id=%s AND user_id=%s AND reg_number=%s
                 AND status NOT IN ('completed','cancelled') LIMIT 1""",
            (manager_id[0],employee,data.reg_number)).fetchone()
        if existing:raise HTTPException(409,"Эта закупка уже поручена сотруднику")
        db.execute(
            """INSERT INTO account_manager_tasks
                 (id,manager_id,user_id,reg_number,title,note,due_date)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (task_id,manager_id[0],employee,data.reg_number,data.title.strip(),
             data.note.strip(),data.due_date))
        _audit(db,actor,employee,"task",task_id,"assign",data.title)
    return {"id":str(task_id),"status":"assigned",
            "note":"Поручение создано. Проверка ЕИС запускается сотрудником отдельно."}

def _task_auth(db,actor,role,task_id):
    task_id=_uuid(task_id)
    row=db.execute(
        """SELECT t.id,t.user_id,t.manager_id,t.reg_number,t.status
           FROM account_manager_tasks t WHERE t.id=%s FOR UPDATE""",
        (task_id,)).fetchone()
    if not row:raise HTTPException(404,"Поручение не найдено")
    _member(db,actor,role,row[1])
    if role!="admin" and row[2]!=actor:
        raise HTTPException(404,"Поручение не входит в ваше подразделение")
    return row

@router.post("/tasks/{task_id}/decision")
def task_decision(task_id:str,data:TaskDecision,request:Request):
    require_origin(request)
    actor,role=supervisor(request)
    if data.action=="return" and len(data.comment.strip())<5:
        raise HTTPException(422,"Укажите замечание сотруднику")
    with connect() as db:
        row=_task_auth(db,actor,role,task_id)
        old=row[4]
        allowed={"return":("submitted",),
                 "complete":("submitted",),
                 "cancel":("assigned","in_progress","submitted","returned")}
        if old not in allowed[data.action]:
            raise HTTPException(409,"Недопустимый переход состояния поручения")
        if data.action=="complete":
            # Completion means the employee submitted a real legal check,
            # not simply a finished EIS download. Verified on submission.
            new_status="completed"
        else:
            new_status={"return":"returned","cancel":"cancelled"}[data.action]
        db.execute(
            """UPDATE account_manager_tasks SET status=%s,updated_at=CURRENT_TIMESTAMP,
              closed_at=CASE WHEN %s IN ('completed','cancelled')
                             THEN CURRENT_TIMESTAMP ELSE NULL END
              WHERE id=%s""",(new_status,new_status,row[0]))
        _audit(db,actor,row[1],"task",row[0],data.action,data.comment)
    return {"status":new_status}

@employee_router.get("")
def my_assignments(request:Request,response:Response):
    owner=current_account(request)[0]
    response.headers.update(no_store_headers())
    with connect() as db:
        rows=db.execute(
            """SELECT t.id,t.user_id,u.email,t.reg_number,t.title,t.note,
                      t.due_date,t.status,t.created_at,t.updated_at,t.manager_id
               FROM account_manager_tasks t
               JOIN account_users u ON u.id=t.user_id
               WHERE t.user_id=%s ORDER BY t.created_at DESC LIMIT 200""",
            (owner,)).fetchall()
    return [_task_dict(row) for row in rows]

@employee_router.post("/{task_id}/{action}")
def employee_task_action(task_id:str,action:str,request:Request):
    require_origin(request)
    if action not in ("take","submit"):
        raise HTTPException(422,"Доступны только принятие и сдача проверки")
    owner=current_account(request)[0]
    with connect() as db:
        task=db.execute(
            """SELECT id,reg_number,status FROM account_manager_tasks
               WHERE id=%s AND user_id=%s FOR UPDATE""",
            (_uuid(task_id),owner)).fetchone()
        if not task:raise HTTPException(404,"Поручение не найдено")
        if action=="take":
            if task[2] not in ("assigned","returned"):
                raise HTTPException(409,"Поручение уже находится в работе")
            new_status="in_progress"
        else:
            if task[2]!="in_progress":
                raise HTTPException(409,"Сначала примите поручение")
            check=db.execute(
                "SELECT job_id,created_at,last_started_at FROM account_manual_checks "
                "WHERE user_id=%s AND reg_number=%s",(owner,task[1])).fetchone()
            if not check:
                raise HTTPException(409,"Сначала запустите самостоятельную проверку ЕИС")
            ns=getattr(request.app.state,"eis223_manual_ns",None)
            if ns is None:
                raise HTTPException(503,"Сведения юридической проверки недоступны")
            from scripts.v366_account_manual import _read_only_status
            verified=_read_only_status(ns,[{"reg_number":task[1],"job_id":check[0],
                                            "created_at":check[1],"last_started_at":check[2]}])
            if not verified or not verified[0].get("legal_review_completed"):
                raise HTTPException(409,"Юридическая проверка ещё не завершена")
            new_status="submitted"
        db.execute(
            """UPDATE account_manager_tasks SET status=%s,updated_at=CURRENT_TIMESTAMP,
               submitted_at=CASE WHEN %s='submitted' THEN CURRENT_TIMESTAMP
                                 ELSE submitted_at END WHERE id=%s""",
            (new_status,new_status,task[0]))
        _audit(db,owner,owner,"task",task[0],action)
    return {"status":new_status}

@router.get("/memos")
def reviewable_memos(request:Request,response:Response):
    actor,role=supervisor(request)
    response.headers.update(no_store_headers())
    predicate="" if role=="admin" else "AND t.manager_id=%s"
    params=() if role=="admin" else (actor,)
    with connect() as db:
        rows=db.execute(
            """SELECT m.id,m.reg_number,m.created_at,u.email,r.status,r.comment,
                      r.submitted_at,r.reviewed_at,m.user_id
               FROM account_manager_memo_reviews r
               JOIN account_memos m ON m.id=r.memo_id
               JOIN account_memo_blobs b ON b.memo_id=m.id
               JOIN account_users u ON u.id=m.user_id
               JOIN account_manager_teams t ON t.user_id=m.user_id
               WHERE u.status='approved' """+predicate+
               " ORDER BY r.submitted_at DESC LIMIT 250",params).fetchall()
    return [{"id":str(r[0]),"reg_number":r[1],"created_at":r[2].isoformat(),
             "employee":r[3],"status":r[4],"comment":r[5],
             "submitted_at":r[6].isoformat(),
             "reviewed_at":r[7].isoformat() if r[7] else None,
             "user_id":str(r[8])} for r in rows]

@employee_router.post("/memo/{memo_id}/submit")
def employee_submit_memo(memo_id:str,request:Request):
    require_origin(request)
    owner=current_account(request)[0];memo_id=_uuid(memo_id)
    with connect() as db:
        member=db.execute(
            "SELECT manager_id FROM account_manager_teams WHERE user_id=%s",
            (owner,)).fetchone()
        if not member:raise HTTPException(409,"Ваш руководитель не назначен")
        memo=db.execute(
            """SELECT m.id FROM account_memos m JOIN account_memo_blobs b
               ON b.memo_id=m.id WHERE m.id=%s AND m.user_id=%s""",
            (memo_id,owner)).fetchone()
        if not memo:raise HTTPException(404,"Сохранённая записка ODT не найдена")
        review=db.execute(
            "SELECT status FROM account_manager_memo_reviews WHERE memo_id=%s FOR UPDATE",
            (memo_id,)).fetchone()
        if review and review[0] not in ("returned",):
            raise HTTPException(409,"Записка уже передана на рассмотрение")
        db.execute(
            """INSERT INTO account_manager_memo_reviews(memo_id,owner_id,status)
               VALUES(%s,%s,'submitted')
               ON CONFLICT(memo_id) DO UPDATE SET
                 status='submitted',reviewer_id=NULL,comment='',
                 submitted_at=CURRENT_TIMESTAMP,reviewed_at=NULL""",
            (memo_id,owner))
        _audit(db,owner,owner,"memo",memo_id,"submit")
    return {"status":"submitted"}

@router.post("/memos/{memo_id}/decision")
def memo_decision(memo_id:str,data:MemoDecision,request:Request):
    require_origin(request)
    actor,role=supervisor(request)
    if data.decision in ("returned","rejected") and len(data.comment.strip())<5:
        raise HTTPException(422,"Для возврата или отклонения укажите причину")
    memo_id=_uuid(memo_id)
    with connect() as db:
        row=db.execute(
            """SELECT r.owner_id,r.status
               FROM account_manager_memo_reviews r WHERE r.memo_id=%s FOR UPDATE""",
            (memo_id,)).fetchone()
        if not row:raise HTTPException(404,"Записка не передана на согласование")
        _member(db,actor,role,row[0])
        if row[1]!="submitted":
            raise HTTPException(409,"Записка уже рассмотрена")
        db.execute(
            """UPDATE account_manager_memo_reviews
               SET status=%s,reviewer_id=%s,comment=%s,reviewed_at=CURRENT_TIMESTAMP
               WHERE memo_id=%s""",
            (data.decision,actor,data.comment.strip(),memo_id))
        _audit(db,actor,row[0],"memo",memo_id,data.decision,data.comment)
    return {"status":data.decision}

@employee_router.get("/memos")
def my_memo_decisions(request:Request,response:Response):
    owner=current_account(request)[0]
    response.headers.update(no_store_headers())
    with connect() as db:
        rows=db.execute(
            """SELECT memo_id,status,comment,submitted_at,reviewed_at
               FROM account_manager_memo_reviews WHERE owner_id=%s
               ORDER BY submitted_at DESC LIMIT 100""",(owner,)).fetchall()
    return [{"id":str(r[0]),"status":r[1],"comment":r[2],
             "submitted_at":r[3].isoformat(),
             "reviewed_at":r[4].isoformat() if r[4] else None} for r in rows]
