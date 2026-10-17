"""Supervisor review of employee-proposed violations and case discussions.

Proposals never enter verified EIS findings automatically. A human acceptance
is explicitly labeled and recorded in a separate identity database.
"""
from __future__ import annotations
from uuid import UUID
from fastapi import APIRouter,HTTPException,Request,Response
from pydantic import BaseModel,Field
from scripts.account_api_v1 import connect,require_origin
from scripts.account_session_policy_v1 import no_store_headers
from scripts.v368_manager_workflow import supervisor,_member,_audit
from scripts.v370_staff_workbench import REG

router=APIRouter(prefix="/manager/workbench",tags=["manager-proposed-violations"])

class Decision(BaseModel):
    decision:str=Field(pattern="^(approved|rejected)$")
    comment:str=Field(default="",max_length=1300)

def _permitted(db,actor,role,worker,reg):
    employee=_member(db,actor,role,worker)
    if not REG.fullmatch(reg):raise HTTPException(422,"Некорректная закупка")
    if not db.execute(
        "SELECT 1 FROM account_manual_checks WHERE user_id=%s AND reg_number=%s",
        (employee,reg)).fetchone():
        raise HTTPException(404,"Проверка сотрудника не найдена")
    return employee

@router.get("/{employee}/{reg}/proposals")
def proposals(employee:UUID,reg:str,request:Request,response:Response):
    actor,role=supervisor(request)
    response.headers.update(no_store_headers())
    with connect() as db:
        uid=_permitted(db,actor,role,employee,reg)
        rows=db.execute(
            """SELECT id,title,law,evidence,source_url,status,created_at
               FROM account_staff_manual_findings
               WHERE user_id=%s AND reg_number=%s ORDER BY created_at DESC LIMIT 100""",
            (uid,reg)).fetchall()
    return [{"id":str(r[0]),"title":r[1],"law":r[2],"evidence":r[3],
             "source_url":r[4],"status":r[5],"created_at":r[6].isoformat()} for r in rows]

@router.post("/{employee}/{reg}/proposals/{proposal_id}/decision")
def decide(employee:UUID,reg:str,proposal_id:UUID,request:Request,data:Decision):
    require_origin(request)
    actor,role=supervisor(request)
    if data.decision=="rejected" and len(data.comment.strip())<5:
        raise HTTPException(422,"Укажите причину отклонения")
    with connect() as db:
        uid=_permitted(db,actor,role,employee,reg)
        row=db.execute(
            """SELECT title,law,evidence,source_url,status
               FROM account_staff_manual_findings
               WHERE id=%s AND user_id=%s AND reg_number=%s FOR UPDATE""",
            (proposal_id,uid,reg)).fetchone()
        if not row:raise HTTPException(404,"Предложение не найдено")
        if row[4]!="needs_review":
            raise HTTPException(409,"Предложение уже рассмотрено")
        if data.decision=="approved":
            from v365_consultant_refs import references
            if not references({"law":row[1]}):
                raise HTTPException(409,"Для подтверждения необходима точная норма права")
            if len(row[2].strip())<30:
                raise HTTPException(409,"Недостаточно доказательств")
        db.execute(
            "UPDATE account_staff_manual_findings SET status=%s WHERE id=%s",
            (data.decision,proposal_id))
        _audit(db,actor,uid,"manual_proposal",proposal_id,data.decision,
               data.comment.strip())
    return {"status":data.decision,
            "source_eis_findings_unchanged":True,
            "note":"Решение руководителя не создаёт автоматическое нарушение без перепроверки исходного документа."}
