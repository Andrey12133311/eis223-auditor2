import unittest
from scripts.account_staging_app import app, application
from scripts.account_api_v1 import router as auth, admin_router
from scripts.account_workspace_v1 import router as workspace
from scripts.account_page_routes_v1 import router as pages


class AccountStagingAppTests(unittest.TestCase):
    def test_staging_app_has_expected_routes(self):
        # FastAPI versions differ in whether include_router is flattened
        # immediately or represented by deferred _IncludedRouter entries.
        registered = {route.path for route in app.routes if hasattr(route, "path")}
        declared = {
            route.path
            for router in (auth, admin_router, workspace, pages)
            for route in router.routes
            if hasattr(route, "path")
        }
        for path in (
            "/auth/register", "/auth/login", "/auth/logout", "/auth/me",
            "/admin/accounts/pending", "/admin/accounts/{user_id}/approve",
            "/account/register", "/account/login", "/account/home",
            "/account/admin", "/me/procurements",
        ):
            self.assertIn(path, registered | declared)

    def test_auth_gate_wraps_staging_app(self):
        self.assertIs(application.app, app)

    def test_no_legacy_eis_routes_are_added(self):
        declared = {
            route.path
            for router in (auth, admin_router, workspace, pages)
            for route in router.routes
            if hasattr(route, "path")
        }
        self.assertNotIn("/api/thematic/history", declared)
        self.assertNotIn("/api/v301/search", declared)


if __name__ == "__main__":
    unittest.main()
