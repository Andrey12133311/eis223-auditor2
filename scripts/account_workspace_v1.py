"""Optional personal procurement endpoints. Not mounted in production."""
import re
from fastapi import APIRouter, HTTPException, Request
from scripts.account_api_v1 import connect, current_account, require_origin
from scripts.account_owner_queries_v1 import PROCUREMENTS

router = APIRouter(prefix="/me", tags=["personal-workspace"])
REG_NUMBER = re.compile(r"^3[0-9]{10}$")


@router.get("/procurements")
def my_procurements(request: Request):
    user = current_account(request)
    with connect() as db:
        rows = db.execute(
            PROCUREMENTS,
            (user[0],)
        ).fetchall()
    return [{"reg_number": r[0], "added_at": r[1].isoformat()} for r in rows]


@router.post("/procurements/{reg_number}")
def add_procurement(reg_number: str, request: Request):
    require_origin(request)
    user = current_account(request)
    if not REG_NUMBER.fullmatch(reg_number):
        raise HTTPException(422, "Некорректный номер закупки")
    with connect() as db:
        db.execute(
            """INSERT INTO account_procurements(user_id,reg_number)
               VALUES (%s,%s) ON CONFLICT DO NOTHING""",
            (user[0], reg_number)
        )
    return {"reg_number": reg_number, "saved": True}


@router.delete("/procurements/{reg_number}")
def remove_procurement(reg_number: str, request: Request):
    require_origin(request)
    user = current_account(request)
    with connect() as db:
        db.execute(
            """DELETE FROM account_procurements
               WHERE user_id=%s AND reg_number=%s""",
            (user[0], reg_number)
        )
    return {"deleted": True}
