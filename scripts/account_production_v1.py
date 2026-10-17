"""Secure opt-in mount of personal accounts into the legacy EIS web app.

Shared historical EIS audits remain admin-only; private account resources are
owner-scoped. Existing EIS intake endpoints keep their independent token auth.
Call install(app) *before* starting Uvicorn; disabled by default.
"""
import hmac
import os
from http.cookies import SimpleCookie

from scripts.account_sessions_v1 import resolve_session
from scripts.account_api_v1 import connect

PUBLIC = frozenset({
    "/health", "/healthz", "/account/login", "/account/register",
    "/auth/login", "/auth/register", "/eis223/collector.js",
    "/api/v299/eis223/collector.js",
})
MACHINE_EXACT = frozenset({
    "/api/v294/eis223/archive",
    "/api/v299/eis223/public-manifest",
    "/api/v299/eis223/heartbeat",
    "/api/v299/eis223/known",
    "/api/v310/eis223/card-metadata",
})
MACHINE_PREFIXES = (
    "/api/v295/eis223/original/",
    "/api/v295/eis223/manifest/",
    "/api/v295/eis223/recheck/",
    "/api/v295/eis223/recheck-status/",
)
PRIVATE_PREFIXES = ("/auth/", "/account/", "/me/", "/admin/accounts/")


def machine_path(path):
    return path in MACHINE_EXACT or any(path.startswith(p) for p in MACHINE_PREFIXES)


def verified_machine(headers):
    expected = os.environ.get("EIS223_UPLOAD_TOKEN", "")
    given = headers.get(b"x-eis223-upload-token", b"").decode("latin1")
    return len(expected) >= 24 and hmac.compare_digest(expected, given)


def user_session(headers, connection):
    cookies = SimpleCookie()
    try:
        cookies.load(headers.get(b"cookie", b"").decode("latin1"))
        item = cookies.get("eis223_session")
        token = item.value if item else ""
    except Exception:
        token = ""
    with connection() as db:
        return resolve_session(db, token)


async def _answer(send, status, body, content_type=b"application/json", location=None):
    headers = [(b"content-type", content_type), (b"cache-control", b"no-store")]
    if location:
        headers.append((b"location", location.encode("utf-8")))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


class ProductionAccountGate:
    def __init__(self, app, connection=connect):
        self.app = app
        self.connection = connection

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        if os.environ.get("EIS223_AUTH_ENFORCE", "").lower() != "true":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        method = scope.get("method", "GET")
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        if path in PUBLIC or path.startswith("/static/"):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "http" and method == "OPTIONS" and machine_path(path):
            await self.app(scope, receive, send)
            return
        if machine_path(path) and verified_machine(headers):
            # Each downstream handler independently checks EIS upload token.
            await self.app(scope, receive, send)
            return
        try:
            account = user_session(headers, self.connection)
        except Exception:
            await _answer(send, 503, b'{"detail":"Identity service unavailable"}')
            return
        if not account:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            elif method == "GET" and (path == "/" or path.startswith("/account/")):
                await _answer(send, 303, b"", location="/account/login")
            else:
                await _answer(send, 401, b'{"detail":"Authentication required"}')
            return
        scope["eis223_account_id"] = account[0]
        scope["eis223_account_role"] = account[1]
        if account[1] != "admin" and not path.startswith(PRIVATE_PREFIXES):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            elif path == "/" and method == "GET":
                await _answer(send, 303, b"", location="/account/home")
            else:
                await _answer(send, 403, b'{"detail":"Only administrators may access shared EIS records"}')
            return
        async def no_cache_send(message):
            if message["type"] == "http.response.start":
                original = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"cache-control"]
                message = {**message, "headers": original + [(b"cache-control", b"no-store")]}
            await send(message)
        await self.app(scope, receive, no_cache_send)


def install(app):
    if os.environ.get("EIS223_ACCOUNTS_MOUNT", "").lower() != "true":
        return False
    if not os.environ.get("EIS223_IDENTITY_DATABASE_URL"):
        raise RuntimeError("PostgreSQL identity database is required")
    if len(os.environ.get("EIS223_RATE_LIMIT_SECRET", "")) < 32:
        raise RuntimeError("Rate limit secret is required")
    origin = os.environ.get("EIS223_PUBLIC_ORIGIN", "")
    from scripts.account_gate_v1 import origin_matches
    if not origin_matches(origin, origin):
        raise RuntimeError("An HTTPS public origin is required")
    from scripts.account_api_v1 import router as auth, admin_router
    from scripts.account_workspace_v1 import router as workspace
    from scripts.account_page_routes_v1 import router as pages
    from scripts.account_private_api_v1 import router as private
    from scripts.account_migrations_v1 import initialize_schema
    app.include_router(auth)
    app.include_router(admin_router)
    app.include_router(workspace)
    app.include_router(pages)
    app.include_router(private)
    app.add_middleware(ProductionAccountGate, connection=connect)

    @app.on_event("startup")
    def start_identity():
        with connect() as db:
            initialize_schema(db)
    return True
