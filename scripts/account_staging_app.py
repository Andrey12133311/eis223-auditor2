"""Isolated staging application for registration and account integration tests.

Start only on a private test deployment with its own PostgreSQL database.
Never replace the production EIS startup with this app.
"""
import os
from fastapi import FastAPI
from scripts.account_api_v1 import router as auth, admin_router
from scripts.account_workspace_v1 import router as workspace
from scripts.account_page_routes_v1 import router as pages
from scripts.account_migrations_v1 import initialize_schema
from scripts.account_asgi_gate_v1 import AccountGateMiddleware
from scripts.account_api_v1 import connect

app = FastAPI(title="ЕИС Аудитор — тестирование личных кабинетов")
app.include_router(auth)
app.include_router(admin_router)
app.include_router(workspace)
app.include_router(pages)


@app.get("/healthz")
def healthz():
    return {"status": "ok", "identity": "staging"}


def initialize_identity_schema():
    if os.environ.get("EIS223_IDENTITY_INIT_SCHEMA") != "true":
        return
    with connect() as db:
        initialize_schema(db)


@app.on_event("startup")
def startup():
    initialize_identity_schema()


# Always wrap staging app. Authentication enforcement is controlled by
# EIS223_AUTH_ENFORCE; never expose this service before it is enabled.
application = AccountGateMiddleware(app, connect)
