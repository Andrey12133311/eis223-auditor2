"""Account page router. Staged only; must not be installed until auth gate is ready."""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from scripts.account_api_v1 import current_account
from scripts.account_pages_v1 import HOME, ADMIN, THEMATIC, MEMOS
from scripts.account_site_ui_v1 import login_portal
from scripts.v368_manager_ui import render_manager
from scripts.v370_staff_ui import render_workbench
from scripts.v371_profile_ui import render_profile

router = APIRouter(prefix="/account", tags=["account-pages"])


@router.get("/register", response_class=HTMLResponse)
def register_page():
    return login_portal('register')


@router.get("/login", response_class=HTMLResponse)
def login_page():
    return login_portal('login')


@router.get("/profile",response_class=HTMLResponse)
def profile_page(request:Request):
    current_account(request)
    return HTMLResponse(render_profile(),headers={"Cache-Control":"private,no-store"})


@router.get("/workbench", response_class=HTMLResponse)
def workbench_page(request: Request):
    current_account(request)
    return HTMLResponse(render_workbench(), headers={"Cache-Control":"private,no-store"})


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

@router.get("/manager", response_class=HTMLResponse)
def manager_page(request: Request):
    user = current_account(request)
    if user[1] not in ("manager", "admin"):
        from fastapi import HTTPException
        raise HTTPException(403, "Только для руководителя")
    return HTMLResponse(render_manager(user[1]),headers={"Cache-Control":"no-store"})
