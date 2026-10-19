"""Secure opt-in mount of personal accounts into the legacy EIS web app.

The full EIS dashboard and shared findings require an approved login. Every
approved user can read shared audit results; writes remain admin-only and
personal data remains owner-scoped. EIS intake retains token authentication.
Call install(app) *before* starting Uvicorn; disabled by default.
"""
import hmac
import os
import re
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlencode
from starlette.concurrency import run_in_threadpool

from scripts.account_sessions_v1 import resolve_session
from scripts.account_session_policy_v1 import SESSION_COOKIE
from scripts.account_api_v1 import connect, _remember_request_account
from scripts.account_site_ui_v1 import site_header
from scripts.v361_home_shell import home_shell

PUBLIC = frozenset({
    "/health", "/healthz", "/account/login", "/account/register",
    "/auth/login", "/auth/register", "/account/setup", "/auth/setup", "/eis223/collector.js",
    "/api/v299/eis223/collector.js", "/eis223/worker-version",
})
MACHINE_EXACT = frozenset({
    "/api/v294/eis223/archive",
    "/api/v299/eis223/public-manifest",
    "/api/v299/eis223/heartbeat",
    "/api/v299/eis223/known",
    "/api/v310/eis223/card-metadata",
    # Verified EIS card fields use the same upload key as manifests/originals.
    # Keep exact paths: browser preflights carry no key or account cookie.
    "/api/v311/eis223/customer",
    "/api/v322/eis223/price",
    "/api/v341/eis223/card-facts",
    "/api/v301/bridge/next",
    "/api/eis-worker/jobs/next", "/api/eis-worker/jobs",
    "/api/eis-worker/discovered", "/api/eis-worker/heartbeat",
    "/api/eis-worker/status",
})
MACHINE_PREFIXES = (
    "/api/v295/eis223/original/",
    "/api/v295/eis223/manifest/",
    "/api/v295/eis223/recheck/",
    "/api/v295/eis223/recheck-status/",
    "/api/v301/bridge/report/",
)
PRIVATE_PREFIXES = ("/auth/", "/account/", "/me/", "/manager/", "/admin/accounts/")

# Explicit read-only allowlist. Do not grant all GET requests to the legacy
# engine: some GET handlers claim bridge jobs or trigger expensive checks.
SHARED_READ_PATHS = frozenset((
    "/", "/api/status", "/api/org-forms", "/api/procurements",
    "/api/purchases", "/api/dashboard",
    "/api/v257/dashboard", "/api/v257/violation-types",
    "/api/v304/memo-template/status",
    "/api/v332/legal-audit/progress",
    "/api/v334/violation-ledger/summary",
    "/api/v334/violation-ledger/overlaps",
    "/api/v334/violation-ledger/changes",
))
SHARED_READ_PATTERNS = tuple(map(re.compile, (
    r"/api/purchases/3[0-9]{10}",
    r"/api/v257/purchase-extra/3[0-9]{10}",
    r"/api/v267/findings/3[0-9]{10}",
    # Render saved shared findings with the current account's requisites.
    # Template uploads and personal archive writes remain separately protected.
    r"/api/v304/memo/3[0-9]{10}\.odt",
    r"/check-new/3[0-9]{10}/?",
    r"/api/v334/violation-ledger/purchase/3[0-9]{10}",
    r"/reports/3[0-9]{10}\.html",
    r"/files/3[0-9]{10}/(?:original|annotated)/[0-9A-Za-z._-]+",
)))


THEMATIC_CARD = re.compile(r"^/thematic/card/[0-9a-f]{24}/3[0-9]{10}/?$")
THEMATIC_MEMO = re.compile(r"^/api/v304/thematic-memo/[0-9a-f]{24}/3[0-9]{10}\.odt$")
THEMATIC_STATUS = re.compile(r"^/api/thematic/[0-9a-f]{24}(?:/stop)?$")
THEMATIC_CLASSIFY = re.compile(r"^/api/thematic/classify/3[0-9]{10}$")


def thematic_user_route(path, method):
    """Allow the genuine thematic UI/API; endpoint checks ownership by session."""
    if method in ("GET", "HEAD"):
        return (path in ("/thematic", "/api/thematic/history")
                or THEMATIC_CARD.fullmatch(path) is not None
                or THEMATIC_MEMO.fullmatch(path) is not None
                or THEMATIC_CLASSIFY.fullmatch(path) is not None
                or (THEMATIC_STATUS.fullmatch(path) is not None
                    and not path.endswith("/stop")))
    if method == "POST":
        return path == "/api/thematic/start" or (
            bool(THEMATIC_STATUS.fullmatch(path)) and path.endswith("/stop"))
    if method == "DELETE":
        return bool(THEMATIC_STATUS.fullmatch(path)) and not path.endswith("/stop")
    return False


def shared_read_route(path, method):
    return (method in ("GET", "HEAD") and (
        path in SHARED_READ_PATHS or
        any(pattern.fullmatch(path) for pattern in SHARED_READ_PATTERNS)
    ))


def personal_manual_location(query_string=b""):
    """Keep valid buyer shortcuts while retiring the shared manual page."""
    try:
        query=parse_qs(query_string[:2048].decode("utf-8",errors="replace"),max_num_fields=20)
    except ValueError:
        query={}
    inn=query.get("inn",[""])[0]
    return "/account/home"+("?"+urlencode({"inn":inn})
        if re.fullmatch(r"(?:[0-9]{10}|[0-9]{12})",inn) else "")


def add_account_navigation(html, role="user"):
    """Show an integrated personal workspace on the shared homepage."""
    if not isinstance(html, bytes) or b'data-eis223-account-nav' in html:
        return html
    start = html.lower().find(b'<body')
    if start < 0:
        return html
    end = html.find(b'>', start)
    if end < 0:
        return html
    return html[:end + 1] + site_header(role) + home_shell() + html[end + 1:]


def machine_path(path):
    return (path in MACHINE_EXACT or any(path.startswith(p) for p in MACHINE_PREFIXES)
            or re.fullmatch(r"/api/eis-worker/(?:jobs|inn-jobs)/[0-9a-f]{24}/report",path) is not None)


def verified_machine(headers):
    expected = os.environ.get("EIS223_UPLOAD_TOKEN", "")
    given = headers.get(b"x-eis223-upload-token", b"").decode("latin1")
    return len(expected) >= 24 and hmac.compare_digest(expected, given)


def user_session(headers, connection):
    cookies = SimpleCookie()
    try:
        cookies.load(headers.get(b"cookie", b"").decode("latin1"))
        item = cookies.get(SESSION_COOKIE)
        token = item.value if item else ""
    except Exception:
        token = ""
    if len(token) < 40:
        return None
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

        if scope["type"] == "http" and path in PUBLIC:
            # Login and registration pages must always be visible when requested.
            # Never silently bypass the password form using a stale session.
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
            # PostgreSQL connection/lookup may wait on I/O. Starlette's
            # bounded worker pool is separate from the audit executor, so a
            # slow identity service cannot freeze health, uploads or the UI.
            account = await run_in_threadpool(user_session, headers, self.connection)
        except Exception:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1013})
            else:
                await _answer(send, 503, b'{"detail":"Identity service unavailable"}')
            return
        if not account:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            elif method in ("GET", "HEAD") and (path == "/" or path.startswith("/account/")
                                                  or path.rstrip("/")=="/manual-purchases"):
                await _answer(send, 303, b"", location="/account/login")
            else:
                await _answer(send, 401, b'{"detail":"Authentication required"}')
            return
        scope["eis223_account_id"] = account[0]
        scope["eis223_account_role"] = account[1]
        _remember_request_account(scope, account)
        if scope["type"]=="http" and method in ("GET","HEAD") and path.rstrip("/")=="/manual-purchases":
            await _answer(send,303,b"",location=personal_manual_location(scope.get("query_string",b"")))
            return
        if account[1] != "admin" and (path.startswith("/admin/accounts/") or path == "/account/admin"):
            await _answer(send, 403, b'{"detail":"Administrator required"}')
            return
        if account[1] != "admin" and not path.startswith(PRIVATE_PREFIXES):
            if scope["type"] != "http" or not (shared_read_route(path, method) or thematic_user_route(path, method)):
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                else:
                    await _answer(send, 403, b'{"detail":"Shared audit is read-only for users"}')
                return
        if scope["type"] == "http" and path == "/" and method == "GET":
            start_message = None
            body = bytearray()
            async def authenticated_home_send(message):
                nonlocal start_message
                if message["type"] == "http.response.start":
                    received = message.get("headers", [])
                    if (message.get("status") == 200 and
                        any(k.lower() == b"content-type" and b"text/html" in v.lower()
                            for k, v in received) and
                        not any(k.lower() == b"content-encoding" for k, v in received)):
                        start_message = message
                        return
                elif message["type"] == "http.response.body" and start_message:
                    body.extend(message.get("body", b""))
                    if message.get("more_body", False):
                        return
                    result = add_account_navigation(bytes(body), account[1])
                    response_headers = [(k, v) for k, v in start_message.get("headers", [])
                                        if k.lower() not in (b"content-length", b"etag", b"content-md5")]
                    response_headers.append((b"content-length", str(len(result)).encode("ascii")))
                    await no_cache_send({**start_message, "headers": response_headers})
                    await send({**message, "body": result})
                    return
                await no_cache_send(message)
            await self.app(scope, receive, authenticated_home_send)
        else:
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
    from scripts.v366_account_manual import router as real_manual_checks, inn_router as personal_inn_search
    from scripts.v367_private_features import router as personal_features
    from scripts.v368_manager_workflow import router as manager_workflow, employee_router as employee_assignments
    from scripts.v368_manager_evidence import router as manager_evidence
    from scripts.v368_manager_quality import router as manager_quality
    from scripts.v369_evidence_case import router as account_cases, manager_router as manager_case_notes
    from scripts.v369_manager_report import router as manager_report
    from scripts.v370_manager_campaigns import router as manager_campaigns, staff_router as staff_campaigns
    from scripts.v370_staff_workbench import router as staff_workbench, team_router as manager_discussions
    from scripts.v375_staff_center import router as staff_center
    from scripts.v370_staff_documents import router as staff_documents
    from scripts.v370_staff_extras import router as staff_extras
    from scripts.v370_manager_review import router as manager_manual_reviews
    from scripts.v371_profile import router as account_profile
    from scripts.account_page_routes_v1 import router as pages
    from scripts.account_private_api_v1 import router as private
    from scripts.account_migrations_v1 import initialize_schema
    app.include_router(first_admin_setup)
    app.include_router(auth)
    app.include_router(admin_router)
    from scripts.account_role_management_v1 import router as role_router
    app.include_router(role_router)
    app.include_router(workspace)
    app.include_router(real_manual_checks)
    app.include_router(personal_inn_search)
    app.include_router(personal_features)
    app.include_router(manager_workflow)
    app.include_router(manager_evidence)
    from scripts.v382_finding_submissions import router as finding_submissions
    app.include_router(finding_submissions)
    from scripts.v385_msp_reassessment import router as msp_reassessment
    app.include_router(msp_reassessment)
    app.include_router(manager_quality)
    app.include_router(account_cases)
    app.include_router(manager_case_notes)
    app.include_router(manager_report)
    app.include_router(manager_campaigns)
    app.include_router(staff_campaigns)
    app.include_router(staff_workbench)
    app.include_router(staff_center)
    app.include_router(manager_discussions)
    app.include_router(staff_documents)
    app.include_router(staff_extras)
    app.include_router(manager_manual_reviews)
    app.include_router(account_profile)
    app.include_router(employee_assignments)
    app.include_router(pages)
    app.include_router(private)
    app.add_middleware(ProductionAccountGate, connection=connect)

    @app.on_event("startup")
    def start_identity():
        # A temporary PostgreSQL outage must not crash the EIS collector.
        # Private requests fail closed until the identity database recovers.
        import logging
        try:
            with connect() as db:
                initialize_schema(db)
            from scripts.v375_task_autoload import start as start_manager_task_queue
            start_manager_task_queue(app)
        except Exception:
            logging.getLogger(__name__).exception(
                "Account database unavailable; private sign-in is temporarily blocked"
            )

    @app.on_event("shutdown")
    async def stop_identity():
        from scripts.account_connection_pool_v1 import close_pool
        await run_in_threadpool(close_pool)
    return True
