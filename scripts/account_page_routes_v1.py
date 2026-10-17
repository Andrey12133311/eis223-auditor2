"""Account page router. Staged only; must not be installed until auth gate is ready."""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from scripts.account_api_v1 import current_account
from scripts.account_pages_v1 import REGISTER, LOGIN, HOME, ADMIN, THEMATIC, MEMOS

router = APIRouter(prefix="/account", tags=["account-pages"])


@router.get("/register", response_class=HTMLResponse)
def register_page():
    return REGISTER


@router.get("/login", response_class=HTMLResponse)
def login_page():
    return LOGIN


@router.get("/home", response_class=HTMLResponse)
def home_page(request: Request):
    current_account(request)
    return HOME


@router.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    current_account(request, admin=True)
    return ADMIN


@router.get("/thematic", response_class=HTMLResponse)
def thematic_page(request: Request):
    current_account(request)
    return THEMATIC


@router.get("/memos", response_class=HTMLResponse)
def memos_page(request: Request):
    current_account(request)
    return MEMOS
