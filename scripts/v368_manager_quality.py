"""Read-only leader oversight for team thematic audits and legal QA."""
from __future__ import annotations
import sqlite3
from fastapi import APIRouter,HTTPException,Request,Response
from scripts.account_api_v1 import connect
from scripts.account_session_policy_v1 import no_store_headers
from scripts.v368_manager_workflow import supervisor
from scripts.v368_manager_evidence import _db_state,_legal_items

router=APIRouter(prefix="/manager",tags=["manager-thematic-quality"])

def _team_sources(db,actor,role):
    clause="" if role=="admin" else "WHERE tm.manager_id=%s"
    return db.execute(
        """SELECT t.user_id,u.email,t.batch_id FROM account_thematic_batches t
           JOIN account_manager_teams tm ON tm.user_id=t.user_id
           JOIN account_users u ON u.id=t.user_id
           """+clause+" ORDER BY t.batch_id DESC LIMIT 120",
        () if role=="admin" else (actor,)).fetchall()


@router.get("/thematic")
def team_thematic(request:Request,response:Response):
    actor,role=supervisor(request)
    response.headers.update(no_store_headers())
    with connect() as db:
        rows=_team_sources(db,actor,role)
    if not rows:return []
    from v342_thematic_queue import thematic_history_records
    try:
        history=thematic_history_records(_db_state(request),[r[2] for r in rows])["batches"]
    except (sqlite3.Error,KeyError,ValueError) as exc:
        raise HTTPException(503,"Не удалось получить результаты тематических проверок") from exc
    by_id={x["id"]:x for x in history}
    results=[]
    for owner,email,batch in rows:
        item=by_id.get(batch)
        if item:
            results.append({"user_id":str(owner),"employee":email,**item})
    return results


@router.get("/quality")
def quality_control(request:Request,response:Response):
    actor,role=supervisor(request)
    response.headers.update(no_store_headers())
    with connect() as db:
        condition="" if role=="admin" else "AND tm.manager_id=%s"
        rows=db.execute(
            """SELECT c.user_id,u.email,c.reg_number
               FROM account_manual_checks c
               JOIN account_manager_teams tm ON tm.user_id=c.user_id
               JOIN account_users u ON u.id=c.user_id
               WHERE u.status='approved' """+condition+
               """ ORDER BY c.last_started_at DESC LIMIT 65""",
            () if role=="admin" else (actor,)).fetchall()
    issues=[]
    scanned=0
    try:
        ns=_db_state(request) if rows else None
        for owner,email,reg in rows:
            for finding in _legal_items(ns,reg):
                scanned+=1
                if finding["warnings"]:
                    issues.append({
                      "user_id":str(owner),"employee":email,"reg_number":reg,
                      "title":finding["title"],"finding_key":finding["finding_key"],
                      "warnings":finding["warnings"],
                    })
    except (sqlite3.Error,KeyError,TypeError) as exc:
        raise HTTPException(503,"Недоступна проверка полноты доказательств") from exc
    return {
       "checked_latest_procurements":len(rows),
       "reviewed_finding_episodes":scanned,
       "needs_human_verification":len(issues),
       "issues":issues[:75],
       "scope":"Последние 65 самостоятельных проверок сотрудников подразделения",
       "note":"Замечания к доказательствам не считаются новыми нарушениями",
    }
