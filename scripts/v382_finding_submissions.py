"""Explicit employee-to-manager submissions, separate from legal findings/memos."""
from fastapi import APIRouter, HTTPException, Request, Response
from scripts.account_api_v1 import connect, current_account, require_origin
from scripts.account_session_policy_v1 import no_store_headers
from scripts.v368_manager_workflow import supervisor, _audit, REG
from scripts.v368_manager_evidence import _legal_items, _db_state
from scripts.v384_thematic_findings import require_batch_owner, thematic_items
import sqlite3

router = APIRouter(tags=['finding-submissions'])


def _shared_reg(reg):
    # /check-new/{reg} is already an authenticated shared read route.
    # A personal manual-check row is not provenance for shared EIS evidence.
    if not REG.fullmatch(reg):
        raise HTTPException(422, 'Некорректный номер закупки ЕИС')


def _items(request, reg, batch=""):
    try:
        return (thematic_items(_db_state(request), batch, reg) if batch
                else _legal_items(_db_state(request), reg))
    except sqlite3.Error as exc:
        raise HTTPException(503, 'Не удалось открыть доказательства ЕИС') from exc


@router.get('/me/finding-submissions/{reg}')
def employee_findings(reg: str, request: Request, response: Response, batch: str = ""):
    user = current_account(request)[0]
    response.headers.update(no_store_headers())
    with connect() as db:
        _shared_reg(reg)
        if batch:
            require_batch_owner(db, user, batch)
        manager = db.execute("SELECT t.manager_id FROM account_manager_teams t JOIN account_users m ON m.id=t.manager_id WHERE t.user_id=%s AND m.role='manager' AND m.status='approved'", (user,)).fetchone()
        submitted = db.execute('SELECT finding_key,status,submitted_at,version FROM account_finding_submissions WHERE user_id=%s AND reg_number=%s', (user, reg)).fetchall()
        reviewed = db.execute('SELECT finding_key,decision,comment FROM account_manager_finding_reviews WHERE user_id=%s AND reg_number=%s', (user, reg)).fetchall()
    submissions = {r[0]: {'status': r[1], 'submitted_at': r[2].isoformat(), 'version': r[3]} for r in submitted}
    reviews = {r[0]: {'decision': r[1], 'comment': r[2]} for r in reviewed}
    items = _items(request, reg, batch)
    for item in items:
        item['submission'] = submissions.get(item['finding_key'])
        item['review'] = reviews.get(item['finding_key'])
    return {'findings': items, 'manager_assigned': bool(manager)}


@router.post('/me/finding-submissions/{reg}/{finding_key}/submit')
def submit_finding(reg: str, finding_key: str, request: Request, batch: str = ""):
    require_origin(request)
    user = current_account(request)[0]
    with connect() as db:
        _shared_reg(reg)
        # Shared procurements need no personal check. Lock the submitting
        # account, also used by manager decisions, before a row exists.
        db.execute('SELECT id FROM account_users WHERE id=%s FOR UPDATE', (user,)).fetchone()
        if batch:
            require_batch_owner(db, user, batch)
        manager = db.execute("SELECT t.manager_id FROM account_manager_teams t JOIN account_users m ON m.id=t.manager_id WHERE t.user_id=%s AND m.role='manager' AND m.status='approved'", (user,)).fetchone()
        if not manager:
            raise HTTPException(409, 'Ваш руководитель не назначен. Обратитесь к администратору.')
        item = next((x for x in _items(request, reg, batch) if x['finding_key'] == finding_key), None)
        if item is None:
            raise HTTPException(409, 'Доказательство изменилось; обновите карточку')
        saved = db.execute('SELECT status FROM account_finding_submissions WHERE user_id=%s AND reg_number=%s AND finding_key=%s', (user, reg, finding_key)).fetchone()
        review = db.execute('SELECT decision FROM account_manager_finding_reviews WHERE user_id=%s AND reg_number=%s AND finding_key=%s', (user, reg, finding_key)).fetchone()
        if saved:
            return {'status': saved[0], 'already_submitted': True}
        if review and review[0] in ('approved', 'rejected'):
            return {'status': review[0], 'already_submitted': True}
        db.execute('''INSERT INTO account_finding_submissions(user_id,reg_number,finding_key,title,source_batch)
                      VALUES(%s,%s,%s,%s,%s)
                      ON CONFLICT(user_id,reg_number,finding_key) DO NOTHING''',
                   (user, reg, finding_key, item['title'], batch))
        _audit(db, user, user, 'finding', finding_key, 'submit', reg)
    return {'status': 'submitted', 'already_submitted': False}


@router.get('/manager/finding-submissions')
def manager_submissions(request: Request, response: Response):
    actor, role = supervisor(request)
    response.headers.update(no_store_headers())
    predicate = '' if role == 'admin' else ' AND t.manager_id=%s'
    with connect() as db:
        rows = db.execute('''SELECT s.user_id,s.reg_number,s.finding_key,s.title,u.email,
                                   s.status,s.submitted_at,COALESCE(r.comment,''),s.version
                            FROM account_finding_submissions s
                            JOIN account_users u ON u.id=s.user_id
                            JOIN account_manager_teams t ON t.user_id=s.user_id
                            LEFT JOIN account_manager_finding_reviews r ON
                              r.user_id=s.user_id AND r.reg_number=s.reg_number AND r.finding_key=s.finding_key
                            WHERE u.status='approved' AND u.role='user' ''' + predicate + '''
                            ORDER BY (s.status='submitted') DESC,s.submitted_at DESC LIMIT 250''',
                          () if role == 'admin' else (actor,)).fetchall()
    return [dict(user_id=str(r[0]), reg_number=r[1], finding_key=r[2], title=r[3],
                 employee=r[4], status=r[5], submitted_at=r[6].isoformat(), comment=r[7], version=r[8]) for r in rows]
