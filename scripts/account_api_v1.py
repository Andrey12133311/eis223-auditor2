"""Optional identity API router. NOT installed into the EIS application.

Requirements: psycopg[binary], argon2-cffi, email-validator, PostgreSQL,
EIS223_IDENTITY_DATABASE_URL and EIS223_PUBLIC_ORIGIN. Route protection for
legacy APIs must be completed before this router is mounted publicly.
"""
import os
from fastapi import APIRouter, HTTPException, Request, Response
from uuid import UUID
from pydantic import BaseModel, Field
from email_validator import validate_email, EmailNotValidError
from scripts.account_passwords_v1 import hash_password, verify_password
from scripts.account_security_v1 import normalize_email
from scripts.account_gate_v1 import origin_matches
from scripts.account_session_policy_v1 import cookie_options, no_store_headers
from scripts.account_throttle_v1 import request_allowed
from scripts.account_repository_v1 import (
    create_pending_user, pending_users, approve_user, reject_user, block_user
)
from scripts.account_sessions_v1 import create_session, resolve_session, revoke_session
from scripts.account_email_v1 import create_challenge, verify_challenge, send_verification_email

router = APIRouter(prefix="/auth", tags=["accounts"])
admin_router = APIRouter(prefix="/admin/accounts", tags=["accounts"])


def connect():
    import psycopg
    dsn = os.getenv("EIS223_IDENTITY_DATABASE_URL")
    if not dsn:
        raise RuntimeError("Identity database is not configured")
    return psycopg.connect(dsn)


def require_origin(request):
    expected = os.getenv("EIS223_PUBLIC_ORIGIN", "")
    origin = request.headers.get("origin", "")
    if not origin_matches(origin, expected):
        raise HTTPException(403, "Invalid origin")


def current_account(request, admin=False):
    token = request.cookies.get("eis223_session", "")
    with connect() as db:
        row = resolve_session(db, token)
    if not row:
        raise HTTPException(401, "Authentication required")
    if admin and row[1] != "admin":
        raise HTTPException(403, "Administrator required")
    return row


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=256)


@router.post("/register", status_code=202)
def register(data: Credentials, request: Request):
    require_origin(request)
    try:
        email = normalize_email(data.email)
    except ValueError:
        raise HTTPException(422, "Некорректный адрес почты или пароль")
    with connect() as db:
        allowed = request_allowed(db, request, email, "register")
    if not allowed:
        raise HTTPException(429, "Слишком много попыток. Повторите позже")
    try:
        validate_email(email, check_deliverability=True)
    except EmailNotValidError:
        raise HTTPException(422, "Адрес почты некорректен или его домен не принимает почту")
    encoded = hash_password(data.password)
    with connect() as db:
        user_id = create_pending_user(db, email, encoded)
        token = create_challenge(db, user_id) if user_id else None
    if token:
        try:
            send_verification_email(email, token)
        except (OSError, RuntimeError, KeyError, ValueError) as exc:
            raise HTTPException(503, "Не удалось отправить письмо. Повторите позже или обратитесь к администратору") from exc
    return {"status": "pending", "message": "Если заявка создана, проверьте почту для подтверждения адреса"}

@router.get("/verify-email")
def verify_email(token: str, response: Response):
    response.headers.update(no_store_headers())
    response.headers["Referrer-Policy"] = "no-referrer"
    with connect() as db:
        verified = verify_challenge(db, token)
    if not verified:
        raise HTTPException(400, "Ссылка недействительна")
    return {"status": "verified"}


@router.post("/login")
def login(data: Credentials, request: Request, response: Response):
    require_origin(request)
    try:
        email = normalize_email(data.email)
    except ValueError:
        raise HTTPException(422, "Некорректный адрес почты")
    with connect() as db:
        allowed = request_allowed(db, request, email, "login")
    if not allowed:
        raise HTTPException(429, "Слишком много попыток. Повторите позже")
    with connect() as db:
        row = db.execute(
            "SELECT id,password_hash,status FROM account_users WHERE lower(email)=%s",
            (email,)
        ).fetchone()
        if not row or not verify_password(row[1], data.password) or row[2] != "approved":
            raise HTTPException(401, "Неверные данные или доступ не подтверждён")
        token, expiry = create_session(db, row[0])
    response.set_cookie("eis223_session", token, **cookie_options())
    response.headers.update(no_store_headers())
    return {"status": "ok"}


@router.post("/logout")
def logout(request: Request, response: Response):
    require_origin(request)
    token = request.cookies.get("eis223_session", "")
    with connect() as db:
        revoke_session(db, token)
    response.delete_cookie("eis223_session", path="/")
    response.headers.update(no_store_headers())
    return {"status": "ok"}


@router.get("/me")
def me(request: Request, response: Response):
    response.headers.update(no_store_headers())
    row = current_account(request)
    return {"id": str(row[0]), "role": row[1], "status": row[2]}


@admin_router.get("/pending")
def list_pending(request: Request, response: Response):
    response.headers.update(no_store_headers())
    current_account(request, admin=True)
    with connect() as db:
        rows = pending_users(db)
    return [{"id": str(r[0]), "email": r[1], "created_at": r[2].isoformat()} for r in rows]


@admin_router.post("/{user_id}/approve")
def approve(user_id: str, request: Request):
    require_origin(request)
    actor = current_account(request, admin=True)
    try:
        uid = UUID(user_id)
    except ValueError:
        raise HTTPException(422, "Некорректный идентификатор")
    with connect() as db:
        success = approve_user(db, actor[0], uid)
    if not success:
        raise HTTPException(409, "Заявка уже рассмотрена или не найдена")
    return {"status": "approved"}


@admin_router.post("/{user_id}/reject")
def reject(user_id: str, request: Request):
    require_origin(request)
    actor = current_account(request, admin=True)
    try:
        uid = UUID(user_id)
    except ValueError:
        raise HTTPException(422, "Некорректный идентификатор")
    with connect() as db:
        success = reject_user(db, actor[0], uid)
    if not success:
        raise HTTPException(409, "Заявка уже рассмотрена или не найдена")
    return {"status": "rejected"}


@admin_router.post("/{user_id}/block")
def block(user_id: str, request: Request):
    require_origin(request)
    actor = current_account(request, admin=True)
    if str(actor[0]) == user_id:
        raise HTTPException(400, "Нельзя заблокировать себя")
    try:
        uid = UUID(user_id)
    except ValueError:
        raise HTTPException(422, "Некорректный идентификатор")
    with connect() as db:
        success = block_user(db, actor[0], uid)
    if not success:
        raise HTTPException(404, "Пользователь не найден")
    return {"status": "blocked"}
