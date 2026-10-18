"""Admin-only role management; audit changes and revoke old sessions."""
from uuid import UUID
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from scripts.account_api_v1 import connect, current_account, require_origin

router = APIRouter(prefix="/admin/roles", tags=["account-roles"])

class RoleInput(BaseModel):
    role: str

@router.get("/users")
def users(request: Request):
    current_account(request, admin=True)
    with connect() as db:
        rows=db.execute("""SELECT u.id,u.email,u.role,u.status,u.last_name,u.first_name,u.patronymic,
                   t.manager_id,m.email,m.last_name,m.first_name,m.patronymic,m.status
            FROM account_users u
            LEFT JOIN account_manager_teams t ON t.user_id=u.id
            LEFT JOIN account_users m ON m.id=t.manager_id
            ORDER BY u.created_at DESC""").fetchall()
    return [{"id":str(r[0]),"email":r[1],"role":r[2],"status":r[3],
             "full_name":" ".join(v for v in r[4:7] if v),
             "manager_id":str(r[7]) if r[7] else None,"manager_email":r[8],
             "manager_name":" ".join(v for v in r[9:12] if v),"manager_status":r[12]} for r in rows]

@router.put("/users/{user_id}")
def change_role(user_id: UUID, data: RoleInput, request: Request):
    # The account admin page and manager workspace must enforce exactly the
    # same role permissions, audit trail, team rules and notifications.
    from scripts.v368_manager_workflow import RoleUpdate, admin_change_role
    if data.role not in ("user", "manager", "admin"):
        raise HTTPException(422, "Недопустимая роль")
    result = admin_change_role(str(user_id), RoleUpdate(role=data.role), request)
    return {"status": "ok", **result}
