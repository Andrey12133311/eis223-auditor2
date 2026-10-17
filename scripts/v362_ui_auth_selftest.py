"""Read-only regression suite: login requires credentials, cabinet shows memos,
and the old oversized blue promotional panel cannot return.
This suite never contacts PostgreSQL, Railway or the EIS and never edits data.
"""
import asyncio
import os
import inspect
from unittest.mock import patch
from datetime import datetime, timezone

from scripts.account_session_policy_v1 import SESSION_COOKIE, SESSION_MAX_AGE, cookie_options
from scripts.account_site_ui_v1 import login_portal, site_header
from scripts.account_pages_v1 import MEMOS
from scripts.account_production_v1 import ProductionAccountGate, add_account_navigation, user_session
from scripts.account_sessions_v1 import create_session
from scripts.v361_home_shell import home_shell


def test_login_cookie():
    policy = cookie_options()
    assert SESSION_COOKIE == "eis223_session_v2"
    assert SESSION_MAX_AGE == 2 * 3600
    assert policy["httponly"] and policy["secure"] and policy["samesite"] == "strict"
    assert "max_age" not in policy and "expires" not in policy
    class FakeDb:
        def execute(self, query, args):
            assert "account_sessions" in query
            self.expiry = args[3]
    db = FakeDb()
    created = datetime.now(timezone.utc)
    token, expiry = create_session(db, "user1")
    assert token and db.expiry == expiry
    assert 2 * 3600 - 10 < (expiry-created).total_seconds() <= 2 * 3600 + 10

    old = b"eis223_session=" + b"x" * 60
    assert user_session({b"cookie": old}, lambda: None) is None
    login = login_portal("login")
    assert 'id="login-password"' in login and 'type="password"' in login
    assert "fetch('/auth/me'" not in login
    assert 'autocomplete="current-password"' in login

    from scripts import account_api_v1 as api
    from scripts.account_production_v1 import user_session as gate_resolve
    login_source = inspect.getsource(api.login)
    logout_source = inspect.getsource(api.logout)
    assert "verify_password(" in login_source
    assert "response.set_cookie(SESSION_COOKIE" in login_source
    assert "response.delete_cookie(SESSION_COOKIE" not in login_source
    assert "response.delete_cookie(SESSION_COOKIE" in logout_source
    assert "request.cookies.get(SESSION_COOKIE" in inspect.getsource(api.current_account)
    assert "cookies.get(SESSION_COOKIE)" in inspect.getsource(gate_resolve)


def test_cabinet_and_home():
    ui = site_header()
    assert b'eis223-toggle-cabinet' in ui
    assert b'eis223-my-memos' in ui
    assert b'/account/memos' in ui
    assert b'/api/thematic/history' in ui
    assert b'eis361-hero' not in ui
    assert '/me/memo-archive' in ui.decode()
    assert 'Архив служебных записок' in MEMOS and '/me/memo-archive' in MEMOS
    shell = home_shell()
    assert b'eis361-home-style' in shell
    assert b'eis361-home-script' in shell
    assert b'insertBefore(hero' not in shell
    assert "gradient(112deg,#122e4e" not in shell.decode("utf-8")
    assert "Мои закупки" not in shell.decode("utf-8")  # no giant CTA
    sample = b'<html><head></head><body><main class="container"></main></body></html>'
    combined = add_account_navigation(sample)
    assert b'eis223-my-memos' in combined
    assert b'eis361-home-script' in combined
    assert combined.count(b'eis361-home-script') == 1


def test_login_page_never_autoredirects():
    results = []
    async def dummy_app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/html")]})
        await send({"type": "http.response.body", "body": b"<html>password</html>"})
    def forbid_db():
        raise AssertionError("Login GET must not silently restore account session")
    gate = ProductionAccountGate(dummy_app, connection=forbid_db)
    async def send(message):
        results.append(message)
    scope = {"type": "http", "method": "GET", "path": "/account/login",
             "headers": [(b"cookie", b"eis223_session_v2=" + b"x" * 60)]}
    # Even with a cookie, explicitly opening /account/login must show the form.
    with patch.dict(os.environ, {"EIS223_AUTH_ENFORCE": "true"}):
        asyncio.run(gate(scope, lambda: None, send))
    assert results[0]["status"] == 200
    assert results[-1]["body"] == b"<html>password</html>"


if __name__ == "__main__":
    test_login_cookie()
    test_cabinet_and_home()
    test_login_page_never_autoredirects()
    print("V362_UI_AUTH_SELFTEST_OK password_prompt=True session_only=True "
          "old_cookie_rejected=True no_giant_hero=True "
          "memos_in_cabinet=True no_data_mutation=True", flush=True)
