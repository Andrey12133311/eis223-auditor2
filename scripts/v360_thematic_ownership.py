"""Owner bridge between real EIS thematic campaigns and signed-in accounts.

The working thematic engine remains in SQLite. PostgreSQL stores ONLY which
authenticated user may see/manage each real campaign. Never auto-assign a
historical campaign whose creator is unknown.
"""
from __future__ import annotations
import os
from fastapi import HTTPException



def enabled() -> bool:
    return (os.getenv("EIS223_ACCOUNTS_MOUNT", "").lower() == "true"
            and os.getenv("EIS223_AUTH_ENFORCE", "").lower() == "true")


def account_id(request):
    if not enabled():
        return None
    owner = request.scope.get("eis223_account_id")
    if owner is None:
        raise HTTPException(401, "Требуется авторизация")
    return owner


def protect_write(request):
    if enabled():
        from scripts.account_api_v1 import require_origin
        require_origin(request)


def linked_ids(request):
    owner = account_id(request)
    if owner is None:
        return None
    try:
        from scripts.account_api_v1 import connect
        with connect() as db:
            rows = db.execute(
                "SELECT batch_id FROM account_thematic_batches "
                "WHERE user_id=%s AND batch_id ~ '^[0-9a-f]{24}$' "
                "ORDER BY batch_id LIMIT 500", (owner,)
            ).fetchall()
        return [row[0] for row in rows]
    except Exception as exc:
        raise HTTPException(503, "Список личных проверок временно недоступен") from exc


def require_owned(request, batch):
    owner = account_id(request)
    if owner is None:
        return
    try:
        from scripts.account_api_v1 import connect
        with connect() as db:
            row = db.execute(
                "SELECT 1 FROM account_thematic_batches "
                "WHERE user_id=%s AND batch_id=%s", (owner, batch)
            ).fetchone()
    except Exception as exc:
        raise HTTPException(503, "Не удалось проверить доступ к тематической проверке") from exc
    if not row:
        raise HTTPException(404, "Тематическая проверка не найдена в вашем кабинете")


def link_started(request, batch):
    owner = account_id(request)
    if owner is None:
        return
    try:
        from scripts.account_api_v1 import connect
        with connect() as db:
            db.execute(
                "INSERT INTO account_thematic_batches(user_id,batch_id) "
                "VALUES (%s,%s) ON CONFLICT DO NOTHING", (owner, batch)
            )
    except Exception as exc:
        raise HTTPException(503, "Не удалось привязать проверку к личному кабинету") from exc


def unlink(request, batch):
    owner = account_id(request)
    if owner is None:
        return
    try:
        from scripts.account_api_v1 import connect
        with connect() as db:
            db.execute(
                "DELETE FROM account_thematic_batches "
                "WHERE user_id=%s AND batch_id=%s", (owner, batch)
            )
    except Exception as exc:
        raise HTTPException(503, "Не удалось обновить список личных проверок") from exc


def _require_admin(request):
    if not enabled() or request.scope.get("eis223_account_role") != "admin":
        raise HTTPException(403, "Требуются права администратора")
    return account_id(request)


def already_assigned(request):
    """For administrator-only one-click adoption of historical unknown-owner runs."""
    _require_admin(request)
    try:
        from scripts.account_api_v1 import connect
        with connect() as db:
            rows = db.execute(
                "SELECT DISTINCT batch_id FROM account_thematic_batches "
                "WHERE batch_id ~ '^[0-9a-f]{24}$'"
            ).fetchall()
        return {row[0] for row in rows}
    except Exception as exc:
        raise HTTPException(503, "Не удалось получить историю привязок") from exc


def claim_historical(request, batch):
    """Admin explicitly adopts only a real campaign with no existing owner.

    Transaction-scoped advisory lock prevents two simultaneous admins from
    assigning the same batch to two different accounts. Nothing is removed
    from the source EIS database or its evidence records.
    """
    owner = _require_admin(request)
    try:
        from scripts.account_api_v1 import connect
        with connect() as db:
            db.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (batch,))
            existing = db.execute(
                "SELECT 1 FROM account_thematic_batches WHERE batch_id=%s LIMIT 1",
                (batch,)
            ).fetchone()
            if existing:
                raise HTTPException(409, "Эта проверка уже привязана к учётной записи")
            db.execute(
                "INSERT INTO account_thematic_batches(user_id,batch_id) VALUES (%s,%s)",
                (owner, batch)
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, "Не удалось привязать старую проверку") from exc
