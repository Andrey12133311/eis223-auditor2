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
        rows=db.execute("""SELECT id,email,role,status,last_name,first_name,patronymic
            FROM account_users ORDER BY created_at DESC LIMIT 500""").fetchall()
    return [{"id":str(r[0]),"email":r[1],"role":r[2],"status":r[3],
             "full_name":" ".join(v for v in r[4:7] if v)} for r in rows]

@router.put("/users/{user_id}")
def change_role(user_id: UUID, data: RoleInput, request: Request):
    require_origin(request)
    actor=current_account(request,admin=True)
    if data.role not in ("user","manager","admin"):
        raise HTTPException(422,"Недопустимая роль")
    if actor[0]==user_id and data.role!="admin":
        raise HTTPException(409,"Нельзя лишить себя прав администратора")
    with connect() as db:
        row=db.execute("SELECT role,status FROM account_users WHERE id=%s FOR UPDATE",(user_id,)).fetchone()
        if not row:
            raise HTTPException(404,"Пользователь не найден")
        if row[1]!="approved":
            raise HTTPException(409,"Сначала подтвердите пользователя")
        if row[0]!=data.role:
            db.execute("UPDATE account_users SET role=%s WHERE id=%s",(data.role,user_id))
            db.execute("UPDATE account_sessions SET revoked_at=CURRENT_TIMESTAMP WHERE user_id=%s",(user_id,))
            db.execute("INSERT INTO account_audit(actor_id,subject_id,action) VALUES (%s,%s,%s)",
                       (actor[0],user_id,"role:"+row[0]+"->"+data.role))
    return {"status":"ok","role":data.role}
