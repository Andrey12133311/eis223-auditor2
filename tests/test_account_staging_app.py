import unittest
from scripts.account_staging_app import app, application


class AccountStagingAppTests(unittest.TestCase):
    def test_staging_app_has_expected_routes(self):
        paths = {route.path for route in app.routes if hasattr(route, 'path')}
        for path in (
            "/auth/register", "/auth/login", "/auth/logout", "/auth/me",
            "/admin/accounts/pending", "/admin/accounts/{user_id}/approve",
            "/account/register", "/account/login", "/account/home",
            "/account/admin", "/me/procurements",
        ):
            self.assertIn(path, paths)

    def test_auth_gate_wraps_staging_app(self):
        self.assertIs(application.app, app)

    def test_no_legacy_eis_routes_are_added(self):
        paths = {route.path for route in app.routes if hasattr(route, 'path')}
        self.assertNotIn("/api/thematic/history", paths)
        self.assertNotIn("/api/v301/search", paths)


if __name__ == "__main__":
    unittest.main()
