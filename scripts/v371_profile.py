"""Account-scoped ФИО profile: user-entered, never inferred from e-mail.

Applies to employees, supervisors and administrator alike. Names are managed
on the identity PostgreSQL database, separate from EIS originals and findings.
"""
from __future__ import annotations
import re
from fastapi import APIRouter,HTTPException,Request,Response
from pydantic import BaseModel,Field,field_validator
from scripts.account_api_v1 import connect,current_account,require_origin
from scripts.account_session_policy_v1 import no_store_headers

router=APIRouter(prefix="/me/profile",tags=["personal-profile"])
NAME=re.compile(r"^[^\W\d_]+(?:[ '-][^\W\d_]+)*$",re.UNICODE)

def normalize_name(value,required=True):
    val=re.sub(r"\s+"," ",str(value or "").strip())
    if (not val and not required):
        return ""
    if len(val)<2 or len(val)>80 or not NAME.fullmatch(val):
        raise ValueError("Используйте настоящее имя: буквы, пробел или дефис, от 2 до 80 символов")
    return val

def full_name(last,first,middle):
    return " ".join(str(x).strip() for x in (last,first,middle) if str(x or "").strip())

class ProfileUpdate(BaseModel):
    last_name:str=Field(min_length=2,max_length=80)
    first_name:str=Field(min_length=2,max_length=80)
    patronymic:str=Field(default="",max_length=80)

    @field_validator("last_name","first_name")
    @classmethod
    def valid_required(cls,val):
        return normalize_name(val,True)

    @field_validator("patronymic")
    @classmethod
    def valid_middle(cls,val):
        return normalize_name(val,False)


@router.get("")
def my_profile(request:Request,response:Response):
    user_id,role,status=current_account(request)
    response.headers.update(no_store_headers())
    with connect() as db:
        row=db.execute(
            """SELECT email,last_name,first_name,patronymic
               FROM account_users WHERE id=%s AND status='approved'""",
            (user_id,)).fetchone()
    if not row:
        raise HTTPException(404,"Учётная запись не найдена")
    return {
        "email":row[0],
        "last_name":row[1],
        "first_name":row[2],
        "patronymic":row[3],
        "full_name":full_name(row[1],row[2],row[3]),
        "completed":bool(row[1] and row[2]),
        "role":role,
    }


@router.put("")
def update_profile(request:Request,data:ProfileUpdate):
    require_origin(request)
    user_id=current_account(request)[0]
    with connect() as db:
        updated=db.execute(
            """UPDATE account_users
               SET last_name=%s,first_name=%s,patronymic=%s
               WHERE id=%s AND status='approved' RETURNING id""",
            (data.last_name,data.first_name,data.patronymic,user_id)
        ).fetchone()
        if not updated:
            raise HTTPException(404,"Учётная запись не найдена")
        db.execute(
            """INSERT INTO account_audit(actor_id,subject_id,action)
               VALUES (%s,%s,'update_profile')""",
            (user_id,user_id))
    return {"saved":True,"full_name":full_name(data.last_name,data.first_name,data.patronymic)}
