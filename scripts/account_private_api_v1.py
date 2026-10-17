"""Private staging endpoints; all access is scoped to authenticated owner."""
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
def memo_download(memo_id: UUID, request: Request, response: Response):
    owner_id = current_account(request)[0]
    response.headers.update(no_store_headers())
    with connect() as db:
        key = owned_memo_key(db, memo_id, owner_id)
    if key is None:
        raise HTTPException(404, "Документ не найден")
    # Do not return a storage path or bypass access checks via a public URL.
    # Streaming requires a verified private storage adapter before activation.
    raise HTTPException(501, "Защищённая выдача документов ещё не подключена")


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
    # Never read a global queue by a guessed ID: bookmark ownership is not engine ownership.
    raise HTTPException(501, "Детали тематической проверки ещё не подключены")


@router.delete("/thematic-batches/{batch_id}")
def unlink_batch(batch_id: str, request: Request, response: Response):
    """Remove a thematic queue from this user's workspace, not the shared audit engine."""
    require_origin(request)
    owner_id = current_account(request)[0]
    response.headers.update(no_store_headers())
    if not batch_id or len(batch_id) > 200 or not batch_id.isascii() or not all(c.isalnum() or c in "-_" for c in batch_id):
        raise HTTPException(422, "Некорректный идентификатор очереди")
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
