"""Private staging endpoints; all access is scoped to authenticated owner."""
import re
from uuid import UUID, uuid4
from fastapi import APIRouter, HTTPException, Request, Response
from scripts.account_api_v1 import connect, current_account, require_origin
from scripts.account_owner_queries_v1 import MEMOS, THEMATIC_BATCHES, owned_memo_key, owns_batch
from scripts.account_session_policy_v1 import no_store_headers

router = APIRouter(prefix="/me", tags=["personal-resources"])


@router.get("/memos")
def list_memos(request: Request, response: Response):
    owner_id = current_account(request)[0]
    response.headers.update(no_store_headers())
    with connect() as db:
        rows = db.execute(MEMOS, (owner_id,)).fetchall()
    return [{"id": str(r[0]), "reg_number": r[1], "created_at": r[3].isoformat()} for r in rows]


@router.get("/memos/{memo_id}/download")
async def memo_download(memo_id: UUID, request: Request, response: Response):
    owner_id = current_account(request)[0]
    response.headers.update(no_store_headers())
    with connect() as db:
        key = owned_memo_key(db, memo_id, owner_id)
    if key is None:
        raise HTTPException(404, "Документ не найден")
    # Do not return a storage path or bypass access checks via a public URL.
    from scripts.v367_private_features import download_owned_memo
    return await download_owned_memo(memo_id,request,
        request.query_params.get("format","odt"))


@router.get("/thematic-batches")
def list_batches(request: Request, response: Response):
    owner_id = current_account(request)[0]
    response.headers.update(no_store_headers())
    with connect() as db:
        rows = db.execute(THEMATIC_BATCHES, (owner_id,)).fetchall()
    return [{"batch_id": r[0]} for r in rows]


@router.get("/thematic-batches/{batch_id}")
def batch_details(batch_id: str, request: Request, response: Response):
    owner_id = current_account(request)[0]
    response.headers.update(no_store_headers())
    with connect() as db:
        permitted = owns_batch(db, batch_id, owner_id)
    if not permitted:
        raise HTTPException(404, "Проверка не найдена")
    # A personal queue is an owned placeholder until a worker is connected.
    # Never read the shared engine using a user-bookmarked global ID.
    if batch_id.startswith("personal-") and len(batch_id) == 41:
        try:
            UUID(batch_id[9:])
        except ValueError:
            raise HTTPException(404, "Проверка не найдена")
        return {"batch_id": batch_id, "status": "saved", "execution_started": False, "results_available": False}
    raise HTTPException(501, "Детали общей тематической проверки ещё не подключены")


@router.delete("/thematic-batches/{batch_id}")
def unlink_batch(batch_id: str, request: Request, response: Response):
    """Remove a thematic queue from this user's workspace, not the shared audit engine."""
    require_origin(request)
    owner_id = current_account(request)[0]
    response.headers.update(no_store_headers())
    if not batch_id or len(batch_id) > 200 or not batch_id.isascii() or not all(c.isalnum() or c in "-_" for c in batch_id):
        raise HTTPException(422, "Некорректный идентификатор очереди")
    if re.fullmatch(r"[0-9a-f]{24}", batch_id):
        # A real active batch must retain its owner while it is running.
        raise HTTPException(409,
            "Откройте проверку в разделе «Тематические проверки» и выберите "
            "«Остановить проверку» или «Удалить очередь».")
    with connect() as db:
        row = db.execute(
            """DELETE FROM account_thematic_batches
               WHERE user_id=%s AND batch_id=%s RETURNING batch_id""",
            (owner_id, batch_id),
        ).fetchone()
    if row is None:
        raise HTTPException(404, "Очередь не найдена")
    return {"removed": True, "batch_id": row[0]}


@router.post("/thematic-batches/{batch_id}", status_code=201)
def save_batch(batch_id: str, request: Request, response: Response):
    """Save a queue identifier in the current user's private workspace."""
    require_origin(request)
    owner_id = current_account(request)[0]
    response.headers.update(no_store_headers())
    if not batch_id or len(batch_id) > 200 or not batch_id.isascii() or not all(c.isalnum() or c in '-_' for c in batch_id):
        raise HTTPException(422, "Некорректный идентификатор очереди")
    if batch_id.startswith("personal-"):
        raise HTTPException(409, "Используйте создание личной очереди")
    if re.fullmatch(r"[0-9a-f]{24}", batch_id):
        # Real ownership is assigned only by the authenticated launch flows
        # or the guarded administrator adoption of an unassigned old batch.
        raise HTTPException(409,
            "Создайте свою проверку в разделе «Тематические проверки». "
            "Привязка запущенной проверки по идентификатору недоступна.")
    with connect() as db:
        db.execute(
            """INSERT INTO account_thematic_batches(user_id,batch_id)
               VALUES (%s,%s) ON CONFLICT DO NOTHING""",
            (owner_id, batch_id),
        )
    return {"saved": True, "batch_id": batch_id}


@router.post("/thematic-batches", status_code=201)
def create_personal_batch(request: Request, response: Response):
    """Allocate an unguessable user-owned queue ID; execution is not started."""
    require_origin(request)
    owner_id = current_account(request)[0]
    response.headers.update(no_store_headers())
    batch_id = "personal-" + uuid4().hex
    with connect() as db:
        db.execute(
            """INSERT INTO account_thematic_batches(user_id,batch_id)
               VALUES (%s,%s)""",
            (owner_id, batch_id),
        )
    return {"batch_id": batch_id, "status": "saved", "execution_started": False}


def _personal_batch_id(batch_id: str) -> bool:
    if not batch_id.startswith("personal-") or len(batch_id) != 41:
        return False
    try:
        return UUID(batch_id[9:]).hex == batch_id[9:]
    except ValueError:
        return False


@router.get("/thematic-batches/{batch_id}/procurements")
def personal_batch_procurements(batch_id: str, request: Request, response: Response):
    owner_id = current_account(request)[0]
    response.headers.update(no_store_headers())
    if not _personal_batch_id(batch_id):
        raise HTTPException(404, "Личная очередь не найдена")
    with connect() as db:
        if not owns_batch(db, batch_id, owner_id):
            raise HTTPException(404, "Личная очередь не найдена")
        rows = db.execute(
            """SELECT reg_number FROM account_thematic_procurements
               WHERE user_id=%s AND batch_id=%s ORDER BY created_at DESC LIMIT 500""",
            (owner_id, batch_id),
        ).fetchall()
    return [{"reg_number": row[0]} for row in rows]


@router.post("/thematic-batches/{batch_id}/procurements/{reg_number}", status_code=201)
def add_personal_batch_procurement(batch_id: str, reg_number: str, request: Request, response: Response):
    require_origin(request)
    owner_id = current_account(request)[0]
    response.headers.update(no_store_headers())
    if not _personal_batch_id(batch_id):
        raise HTTPException(404, "Личная очередь не найдена")
    from scripts.account_workspace_v1 import REG_NUMBER
    if not REG_NUMBER.fullmatch(reg_number):
        raise HTTPException(422, "Некорректный номер закупки")
    with connect() as db:
        if not owns_batch(db, batch_id, owner_id):
            raise HTTPException(404, "Личная очередь не найдена")
        db.execute(
            """INSERT INTO account_thematic_procurements(user_id,batch_id,reg_number)
               VALUES (%s,%s,%s) ON CONFLICT DO NOTHING""",
            (owner_id, batch_id, reg_number),
        )
    return {"batch_id": batch_id, "reg_number": reg_number, "saved": True, "execution_started": False}


@router.delete("/thematic-batches/{batch_id}/procurements/{reg_number}")
def remove_personal_batch_procurement(batch_id: str, reg_number: str, request: Request, response: Response):
    """Remove only the current owner's procurement from an owned personal queue."""
    require_origin(request)
    owner_id = current_account(request)[0]
    response.headers.update(no_store_headers())
    if not _personal_batch_id(batch_id):
        raise HTTPException(404, "Личная очередь не найдена")
    from scripts.account_workspace_v1 import REG_NUMBER
    if not REG_NUMBER.fullmatch(reg_number):
        raise HTTPException(422, "Некорректный номер закупки")
    with connect() as db:
        if not owns_batch(db, batch_id, owner_id):
            raise HTTPException(404, "Личная очередь не найдена")
        row = db.execute(
            """DELETE FROM account_thematic_procurements
               WHERE user_id=%s AND batch_id=%s AND reg_number=%s
               RETURNING reg_number""",
            (owner_id, batch_id, reg_number),
        ).fetchone()
    if row is None:
        raise HTTPException(404, "Закупка в очереди не найдена")
    return {"removed": True, "batch_id": batch_id, "reg_number": row[0]}


@router.post("/thematic-batches/{batch_id}/jobs/{reg_number}", status_code=202)
def enqueue_personal_audit(batch_id: str, reg_number: str, request: Request, response: Response):
    """Persist a user-owned audit request; worker execution is not yet connected."""
    require_origin(request)
    owner_id = current_account(request)[0]
    response.headers.update(no_store_headers())
    from scripts.account_workspace_v1 import REG_NUMBER
    if not _personal_batch_id(batch_id):
        raise HTTPException(404, "Личная очередь не найдена")
    if not REG_NUMBER.fullmatch(reg_number):
        raise HTTPException(422, "Некорректный номер закупки")
    with connect() as db:
        if not owns_batch(db, batch_id, owner_id):
            raise HTTPException(404, "Личная очередь не найдена")
        row = db.execute(
            """INSERT INTO account_thematic_jobs(user_id,batch_id,reg_number,status)
               SELECT user_id,batch_id,reg_number,'queued'
               FROM account_thematic_procurements
               WHERE user_id=%s AND batch_id=%s AND reg_number=%s
               ON CONFLICT DO NOTHING RETURNING status""",
            (owner_id, batch_id, reg_number),
        ).fetchone()
        if row is None:
            existing = db.execute(
                """SELECT status FROM account_thematic_jobs
                   WHERE user_id=%s AND batch_id=%s AND reg_number=%s""",
                (owner_id, batch_id, reg_number),
            ).fetchone()
            if existing is None:
                raise HTTPException(404, "Закупка не добавлена в личную очередь")
            status = existing[0]
        else:
            status = row[0]
    return {"batch_id": batch_id, "reg_number": reg_number, "status": status, "execution_started": status in ("running", "completed", "failed")}


@router.get("/thematic-batches/{batch_id}/jobs")
def list_personal_jobs(batch_id: str, request: Request, response: Response):
    owner_id = current_account(request)[0]
    response.headers.update(no_store_headers())
    if not _personal_batch_id(batch_id):
        raise HTTPException(404, "Личная очередь не найдена")
    with connect() as db:
        if not owns_batch(db, batch_id, owner_id):
            raise HTTPException(404, "Личная очередь не найдена")
        rows = db.execute(
            """SELECT reg_number,status,attempts FROM account_thematic_jobs
               WHERE user_id=%s AND batch_id=%s ORDER BY created_at DESC LIMIT 500""",
            (owner_id, batch_id),
        ).fetchall()
    return [{"reg_number": r[0], "status": r[1], "attempts": r[2]} for r in rows]


@router.get("/thematic-batches/{batch_id}/findings")
def personal_batch_findings(batch_id: str, request: Request, response: Response):
    """Read only evidence-backed findings from an owned personal queue."""
    owner_id = current_account(request)[0]
    response.headers.update(no_store_headers())
    if not _personal_batch_id(batch_id):
        raise HTTPException(404, "Личная очередь не найдена")
    with connect() as db:
        if not owns_batch(db, batch_id, owner_id):
            raise HTTPException(404, "Личная очередь не найдена")
        rows = db.execute(
            """SELECT reg_number,finding_key,legal_basis,description,evidence_source
               FROM account_thematic_findings
               WHERE user_id=%s AND batch_id=%s
               ORDER BY reg_number,finding_key LIMIT 500""",
            (owner_id, batch_id),
        ).fetchall()
    return [
        {"reg_number": row[0], "finding_key": row[1],
         "legal_basis": row[2], "description": row[3],
         "evidence_source": row[4]}
        for row in rows
    ]
