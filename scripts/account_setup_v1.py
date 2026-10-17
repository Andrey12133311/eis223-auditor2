"""Single-use administrator setup for new deployments.

A random operator token is held only in deployment configuration. It is
invalid once the first administrator exists. Never accept credentials in URLs.
"""
import hmac
import os

from email_validator import validate_email, EmailNotValidError
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from scripts.account_api_v1 import connect, require_origin
from scripts.account_bootstrap_v1 import create_first_admin
from scripts.account_passwords_v1 import hash_password
from scripts.account_security_v1 import normalize_email
from scripts.account_session_policy_v1 import no_store_headers

router = APIRouter(tags=["first-admin"])


class AdminSetup(BaseModel):
    token: str = Field(min_length=32, max_length=256)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=256)


@router.get("/account/setup", response_class=HTMLResponse)
def setup_page(response: Response):
    response.headers.update(no_store_headers())
    return """<!doctype html><html lang="ru"><meta charset="utf-8">
<title>Первый администратор · ЕИС Аудитор</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{font:16px system-ui;max-width:530px;margin:8vh auto;padding:20px;background:#f4f7fb}
section{background:white;border-radius:16px;padding:28px;box-shadow:0 3px 24px #0001}
label{display:block;margin:16px 0}input{width:100%;box-sizing:border-box;padding:12px;margin-top:6px}
button{padding:12px 20px;background:#14599b;border:0;color:white;border-radius:8px}</style>
<section><h1>Первоначальная настройка администратора</h1>
<p>Создайте первого администратора, используя одноразовый ключ настройки.</p>
<form id="setup">
<label>Ключ настройки<input type="password" name="token" required minlength="32"></label>
<label>Электронная почта<input type="email" name="email" required></label>
<label>Пароль (от 12 символов)<input type="password" name="password" required minlength="12"></label>
<button>Создать администратора</button></form><p id="status" role="status"></p>
<p><a href="/account/login">Вход</a></p></section>
<script>
document.querySelector('#setup').addEventListener('submit',async e=>{
e.preventDefault();
const b=e.target.querySelector('button'),s=document.querySelector('#status');
b.disabled=true;s.textContent='Проверяем…';
try{
const r=await fetch('/auth/setup',{method:'POST',credentials:'same-origin',
headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.fromEntries(new FormData(e.target)))});
if(r.ok){s.textContent='Администратор создан. Теперь войдите в систему.';e.target.reset()}
else{s.textContent='Настройка отклонена или администратор уже создан'}
}catch{s.textContent='Не удалось подключиться'}finally{b.disabled=false}
});
</script></html>"""


@router.post("/auth/setup", status_code=201)
def create_admin(data: AdminSetup, request: Request, response: Response):
    require_origin(request)
    response.headers.update(no_store_headers())
    expected = os.environ.get("EIS223_FIRST_ADMIN_SETUP_TOKEN", "")
    # Require an independently generated unguessable secret, not a default.
    if len(expected) < 48 or not hmac.compare_digest(expected, data.token):
        raise HTTPException(403, "Неверный ключ настройки")
    try:
        email = normalize_email(data.email)
        validate_email(email, check_deliverability=True)
    except (ValueError, EmailNotValidError):
        raise HTTPException(422, "Некорректный адрес электронной почты")
    with connect() as db:
        user_id = create_first_admin(db, email, hash_password(data.password))
    return {"status": "created", "id": str(user_id)}
