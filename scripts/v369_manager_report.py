"""Manager statistics and export from real team assignments and verified audits.

No imaginary KPI baselines, no deletion and no authority to change EIS rows.
Per-period reports include only checks started during the selected interval.
All SQL access is scoped to administrator or explicitly assigned team.
"""
from __future__ import annotations

import csv
from collections import Counter,defaultdict
from datetime import date,timedelta
from io import StringIO
from fastapi import APIRouter,HTTPException,Query,Request,Response
from scripts.account_api_v1 import connect
from scripts.account_session_policy_v1 import no_store_headers
from scripts.v368_manager_workflow import supervisor

router=APIRouter(prefix="/manager",tags=["manager-reports"])


def _dates(from_day,to_day):
    end=date.today() if not to_day else date.fromisoformat(to_day)
    start=end-timedelta(days=29) if not from_day else date.fromisoformat(from_day)
    if start>end or (end-start).days>366:
        raise HTTPException(422,"Укажите период до 366 дней в правильном порядке")
    if end>date.today()+timedelta(days=1):
        raise HTTPException(422,"Дата окончания не может быть в будущем")
    return start,end


def _period_report(request,actor,role,start,end):
    clause="" if role=="admin" else "AND team.manager_id=%s"
    with connect() as db:
        workers=db.execute(
            """SELECT u.id,u.email FROM account_manager_teams team
               JOIN account_users u ON u.id=team.user_id
               WHERE u.status='approved' """+clause+
               " ORDER BY lower(u.email) LIMIT 250",
            () if role=="admin" else (actor,)).fetchall()
        members={u[0]:u[1] for u in workers}
        entries=[]
        if members:
            variables=",".join("%s" for _ in members)
            entries=db.execute(
                """SELECT user_id,reg_number,job_id,created_at,last_started_at
                   FROM account_manual_checks WHERE user_id IN ("""+variables+
                """) AND last_started_at::date BETWEEN %s AND %s
                    ORDER BY last_started_at DESC LIMIT 900""",
                (*members.keys(),start,end)).fetchall()
        tasks=db.execute(
            """SELECT task.id,task.user_id,task.reg_number,task.title,task.status,
                      task.created_at,task.due_date,task.closed_at
               FROM account_manager_tasks task
               JOIN account_manager_teams team ON team.user_id=task.user_id
               WHERE task.created_at::date BETWEEN %s AND %s """+clause+
               " ORDER BY task.created_at DESC LIMIT 500",
            (start,end) if role=="admin" else (start,end,actor)).fetchall()
        review_rows=db.execute(
            """SELECT r.decision,r.updated_at
               FROM account_manager_finding_reviews r
               JOIN account_manager_teams team ON team.user_id=r.user_id
               WHERE r.updated_at::date BETWEEN %s AND %s """+clause,
            (start,end) if role=="admin" else (start,end,actor)).fetchall()
        memo_rows=db.execute(
            """SELECT r.status,r.submitted_at
               FROM account_manager_memo_reviews r
               JOIN account_manager_teams team ON team.user_id=r.owner_id
               WHERE r.submitted_at::date BETWEEN %s AND %s """+clause,
            (start,end) if role=="admin" else (start,end,actor)).fetchall()
    by_worker={str(uid):{
        "employee":email,"manual_checks":0,"procurements_reviewed":0,
        "documents_checked":0,"confirmed_violations":0,
        "assigned_tasks":0,"submitted_tasks":0,
    } for uid,email in members.items()}
    team_checks={}
    source_available=True
    # Group by procurement so different employees reviewing the same original
    # cannot inflate team violation or document metrics.
    ns=getattr(request.app.state,"eis223_manual_ns",None)
    if entries and ns is None:
        source_available=False
    if entries and ns is not None:
        from scripts.v366_account_manual import _read_only_status
        from sqlite3 import Error as SQLiteError
        try:
            for row in entries:
                user_id,reg,job,created,last_started=row
                detail=_read_only_status(ns,[{"reg_number":reg,"job_id":job,
                                              "created_at":created,"last_started_at":last_started}])[0]
                person=by_worker.get(str(user_id))
                if not person:continue
                person["manual_checks"]+=1
                person["procurements_reviewed"]+=int(bool(detail.get("legal_review_completed")))
                person["documents_checked"]+=detail["documents_checked"]
                person["confirmed_violations"]+=detail["violations"]
                previous=team_checks.get(reg)
                # Prefer verified-completed record, then the one with more
                # documents, without claiming these are separate legal acts.
                if previous is None or (
                    (int(bool(detail.get("legal_review_completed"))),
                     detail["documents_checked"])>
                    (int(bool(previous.get("legal_review_completed"))),
                     previous["documents_checked"])
                ):
                    team_checks[reg]=detail
        except (SQLiteError,KeyError,TypeError,IndexError,ValueError):
            source_available=False
    status=Counter()
    for row in tasks:
        status[row[4]]+=1
        worker=by_worker.get(str(row[1]))
        if worker:
            worker["assigned_tasks"]+=1
            worker["submitted_tasks"]+=int(row[4]=="submitted")
    memo_status=Counter(x[0] for x in memo_rows)
    reviewed_status=Counter(x[0] for x in review_rows)
    total_unique_violations=(sum(x["violations"] for x in team_checks.values())
                              if source_available else None)
    documents_checked=(sum(x["documents_checked"] for x in team_checks.values())
                       if source_available else None)
    reviewed=sum(bool(x.get("legal_review_completed")) for x in team_checks.values()) if source_available else None
    return {
        "period":{"from":start.isoformat(),"to":end.isoformat()},
        "employees":len(members),
        "manual_checks_started":len(entries),
        "unique_procurements":len({x[1] for x in entries}),
        "verified_legal_reviews":reviewed,
        "documents_checked":documents_checked,
        "confirmed_violations":total_unique_violations,
        "tasks_created":len(tasks),
        "tasks_by_status":dict(status),
        "tasks_overdue":sum(bool(t[6] and t[6]<date.today() and
            t[4] not in ("completed","cancelled")) for t in tasks),
        "finding_decisions":dict(reviewed_status),
        "memo_decisions":dict(memo_status),
        "per_employee":sorted(by_worker.values(),key=lambda x:x["employee"].lower()),
        "source_available":source_available,
        "limits":"До 900 личных запусков и 500 поручений в отчёте; большие периоды требуют выгрузки частями.",
        "metric_note":"Счётчики по подтверждённым событиям. Одна закупка подразделения учтена один раз. "
                      "Найденные системой нарушения не равны решениям руководителя.",
    }


@router.get("/report")
def manager_report(request:Request,response:Response,
                   from_day:str|None=Query(None,alias="from"),
                   to_day:str|None=Query(None,alias="to")):
    actor,role=supervisor(request)
    try:start,end=_dates(from_day,to_day)
    except (ValueError,TypeError) as exc:
        raise HTTPException(422,"Дата должна быть в формате ГГГГ-ММ-ДД") from exc
    response.headers.update(no_store_headers())
    return _period_report(request,actor,role,start,end)


def _safe_cell(value):
    """Block spreadsheet-formula injection in employee labels and notes."""
    s=str(value if value is not None else "")
    return "'"+s if s.lstrip().startswith(("=","+","-","@","\t","\r")) else s


@router.get("/report.csv")
def manager_report_csv(request:Request,
                       from_day:str|None=Query(None,alias="from"),
                       to_day:str|None=Query(None,alias="to")):
    actor,role=supervisor(request)
    try:start,end=_dates(from_day,to_day)
    except (ValueError,TypeError) as exc:
        raise HTTPException(422,"Неверный формат периода") from exc
    report=_period_report(request,actor,role,start,end)
    out=StringIO()
    writer=csv.writer(out,delimiter=";")
    writer.writerow(["ЕИС Аудитор — отчёт руководителя",start.isoformat(),end.isoformat()])
    writer.writerow(["Показатель","Значение"])
    summary=[
        ("Сотрудники",report["employees"]),
        ("Самостоятельные проверки",report["manual_checks_started"]),
        ("Уникальных закупок",report["unique_procurements"]),
        ("Завершено юридических проверок",report["verified_legal_reviews"]),
        ("Проверено оригиналов",report["documents_checked"]),
        ("Нарушения (уникальные эпизоды)",report["confirmed_violations"]),
        ("Назначено поручений",report["tasks_created"]),
        ("Просроченные поручения",report["tasks_overdue"]),
        ("Согласовано нарушений",report["finding_decisions"].get("approved",0)),
        ("Согласовано записок",report["memo_decisions"].get("approved",0)),
    ]
    for label,value in summary:
        writer.writerow([label,value if value is not None else "Данные ЕИС временно недоступны"])
    writer.writerow([])
    writer.writerow(["Сотрудник","Запуски", "Завершено","Прочитано документов",
                     "Уникальные нарушения", "Поручения","На согласовании"])
    for person in report["per_employee"]:
        writer.writerow([_safe_cell(person[k]) for k in (
            "employee","manual_checks","procurements_reviewed","documents_checked",
            "confirmed_violations","assigned_tasks","submitted_tasks")])
    writer.writerow([])
    writer.writerow([report["metric_note"]])
    writer.writerow([report["limits"]])
    return Response(out.getvalue().encode("utf-8-sig"),media_type="text/csv; charset=utf-8",
        headers={"Cache-Control":"private,no-store",
                 "X-Content-Type-Options":"nosniff",
                 "Content-Disposition":
                 f'attachment; filename="eis_auditor_report_{start}_{end}.csv"'})
