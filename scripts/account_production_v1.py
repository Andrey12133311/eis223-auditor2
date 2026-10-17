"""Secure opt-in mount of personal accounts into the legacy EIS web app.

Shared EIS audit results are readable by everyone, including guests; mutations
stay admin-only. Personal resources remain owner-scoped. Existing EIS intake endpoints keep their independent token auth.
Call install(app) *before* starting Uvicorn; disabled by default.
"""
import hmac
import os
import re
from http.cookies import SimpleCookie

from scripts.account_sessions_v1 import resolve_session
from scripts.account_api_v1 import connect

PUBLIC = frozenset({
    "/health", "/healthz", "/account/login", "/account/register",
    "/auth/login", "/auth/register", "/account/setup", "/auth/setup", "/eis223/collector.js",
    "/api/v299/eis223/collector.js",
})
MACHINE_EXACT = frozenset({
    "/api/v294/eis223/archive",
    "/api/v299/eis223/public-manifest",
    "/api/v299/eis223/heartbeat",
    "/api/v299/eis223/known",
    "/api/v310/eis223/card-metadata",
    "/api/v301/bridge/next",
})
MACHINE_PREFIXES = (
    "/api/v295/eis223/original/",
    "/api/v295/eis223/manifest/",
    "/api/v295/eis223/recheck/",
    "/api/v295/eis223/recheck-status/",
    "/api/v301/bridge/report/",
)
PRIVATE_PREFIXES = ("/auth/", "/account/", "/me/", "/admin/accounts/")

# Explicit read-only allowlist. Do not grant all GET requests to the legacy
# engine: some GET handlers claim bridge jobs or trigger expensive checks.
SHARED_READ_PATHS = frozenset((
    "/", "/api/status", "/api/org-forms", "/api/procurements",
    "/api/purchases", "/api/dashboard",
    "/api/v332/legal-audit/progress",
    "/api/v334/violation-ledger/summary",
    "/api/v334/violation-ledger/overlaps",
    "/api/v334/violation-ledger/changes",
))
SHARED_READ_PATTERNS = tuple(map(re.compile, (
    r"/api/purchases/3[0-9]{10}",
    r"/api/v334/violation-ledger/purchase/3[0-9]{10}",
    r"/reports/3[0-9]{10}\.html",
    r"/files/3[0-9]{10}/(?:original|annotated)/[0-9A-Za-z._-]+",
)))


def shared_read_route(path, method):
    return (method in ("GET", "HEAD") and (
        path in SHARED_READ_PATHS or
        any(pattern.fullmatch(path) for pattern in SHARED_READ_PATTERNS)
    ))


_ACCOUNT_NAV = (
    '<nav data-eis223-account-nav="1" aria-label="Навигация ЕИС Аудитор" '
    'style="padding:12px 5%;background:#eef3f8;border-bottom:1px solid #d5e0ec;'
    'font:600 14px system-ui;display:flex;gap:20px;flex-wrap:wrap">'
    '<a href="/">Общая проверка</a>'
    '<a href="/account/login">Войти</a>'
    '<a href="/account/register">Регистрация</a>'
    '<a href="/account/home">Личный кабинет</a></nav>'
).encode("utf-8")


def add_account_navigation(html):
    """Add same-domain account entry points without modifying legacy UI code."""
    if not isinstance(html, bytes) or b'data-eis223-account-nav' in html:
        return html
    start = html.lower().find(b'<body')
    if start < 0:
        return html
    end = html.find(b'>', start)
    if end < 0:
        return html
    return html[:end + 1] + _ACCOUNT_NAV + html[end + 1:]



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

        async def no_cache_send(message):
            if message["type"] == "http.response.start":
                original = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"cache-control"]
                message = {**message, "headers": original + [(b"cache-control", b"no-store")]}
            await send(message)

        if path in PUBLIC or path.startswith("/static/"):
            await self.app(scope, receive, send)
            return
        # Public procurement evidence is shared with visitors, not tied to a
        # personal session. PostgreSQL outages must not hide public audits.
        if scope["type"] == "http" and shared_read_route(path, method):
            if path == "/" and method == "GET":
                is_html = False
                async def shared_home_send(message):
                    nonlocal is_html
                    if message["type"] == "http.response.start":
                        headers = message.get("headers", [])
                        is_html = any(k.lower() == b"content-type" and
                                      b"text/html" in v.lower() for k, v in headers)
                        if is_html:
                            message = {**message, "headers": [
                                (k, v) for k, v in headers
                                if k.lower() != b"content-length"
                            ]}
                    elif message["type"] == "http.response.body" and is_html:
                        message = {**message, "body": add_account_navigation(
                            message.get("body", b"")
                        )}
                    await no_cache_send(message)
                await self.app(scope, receive, shared_home_send)
            else:
                await self.app(scope, receive, no_cache_send)
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
        if account[1] != "admin" and (path.startswith("/admin/accounts/") or path == "/account/admin"):
            await _answer(send, 403, b'{"detail":"Administrator required"}')
            return
        if account[1] != "admin" and not path.startswith(PRIVATE_PREFIXES):
            if scope["type"] != "http" or not shared_read_route(path, method):
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                else:
                    await _answer(send, 403, b'{"detail":"Shared audit is read-only for users"}')
                return
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
    from scripts.account_setup_v1 import router as first_admin_setup
    from scripts.account_workspace_v1 import router as workspace
    from scripts.account_page_routes_v1 import router as pages
    from scripts.account_private_api_v1 import router as private
    from scripts.account_migrations_v1 import initialize_schema
    app.include_router(first_admin_setup)
    app.include_router(auth)
    app.include_router(admin_router)
    app.include_router(workspace)
    app.include_router(pages)
    app.include_router(private)
    app.add_middleware(ProductionAccountGate, connection=connect)

    @app.on_event("startup")
    def start_identity():
        # A temporary PostgreSQL outage must never take down the public
        # procurement dashboard or the existing EIS audit workers.
        import logging
        try:
            with connect() as db:
                initialize_schema(db)
        except Exception:
            logging.getLogger(__name__).exception(
                "Account database unavailable; public EIS audit stays online"
            )
    return True
