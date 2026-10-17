"""ASGI identity middleware for a dedicated staging instance only.

This middleware checks all HTTP paths before route dispatch, including
legacy API and file endpoints. Browser-bridge routes are intentionally NOT
exempted; an authenticated machine-bridge design is required before rollout.
"""
from http.cookies import SimpleCookie

from scripts.account_gate_v1 import enforce_auth_enabled, is_public_path
from scripts.account_sessions_v1 import resolve_session


class AccountGateMiddleware:
    def __init__(self, app, connect):
        self.app = app
        self.connect = connect

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket") or not enforce_auth_enabled():
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if scope["type"] == "http" and is_public_path(path):
            await self.app(scope, receive, send)
            return
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        cookies = SimpleCookie()
        try:
            cookies.load(headers.get(b"cookie", b"").decode("latin1"))
            item = cookies.get("eis223_session")
            token = item.value if item else ""
        except Exception:
            token = ""
        try:
            with self.connect() as db:
                account = resolve_session(db, token)
        except Exception:
            account = None
        if account is None:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            body = b'{"detail":"Authentication required"}'
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json"),
                                    (b"cache-control", b"no-store")]})
            await send({"type": "http.response.body", "body": body})
            return
        scope["eis223_account_id"] = account[0]
        scope["eis223_account_role"] = account[1]
        await self.app(scope, receive, send)
