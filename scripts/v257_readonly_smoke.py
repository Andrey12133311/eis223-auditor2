"""Unauthenticated, GET-only health/auth-gate probe; never a dashboard audit.

The protected dashboard must NOT be readable anonymously. A login redirect
is neither a failed dashboard integration nor proof that authenticated data
loads. No login, cookie, upload token, job claim, recheck or retry is sent.
"""
from __future__ import annotations
import argparse
import json
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

DEFAULT_ORIGIN = "https://web-production-1c2ea.up.railway.app"
MAX_BODY = 1024 * 1024
PATHS = ("/health", "/", "/account/login", "/manual-purchases",
         "/api/v257/manual", "/api/v257/violation-types",
         "/api/v257/dashboard?limit=1", "/api/v257/purchase-extra/32615969056")


class ProbeFailure(RuntimeError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def inspect_response(path, status, headers, body):
    """Validate this gate contract, not a prior release's private UI markers."""
    if len(body) > MAX_BODY:
        raise ProbeFailure("Response exceeded read budget: " + path)
    if path in ("/", "/manual-purchases"):
        if status != 303 or headers.get("Location") != "/account/login":
            raise ProbeFailure("Expected exact unauthenticated login redirect: " + path)
        return "authentication_required"
    if path == "/account/login":
        if status != 200 or b"/auth/login" not in body or b'role="dialog"' not in body:
            raise ProbeFailure("Login form unavailable")
        return "login_form_available"
    expected = 200 if path == "/health" else 401
    if status != expected:
        raise ProbeFailure("Unexpected HTTP status %s for %s" % (status, path))
    try:
        value = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ProbeFailure("Expected JSON: " + path) from exc
    if not isinstance(value, dict):
        raise ProbeFailure("Expected JSON object: " + path)
    if path != "/health" and value.get("detail") != "Authentication required":
        raise ProbeFailure("Expected explicit authentication denial: " + path)
    return "health_available" if path == "/health" else "protected_api_denied"


def run(origin=DEFAULT_ORIGIN, opener=None):
    parsed = urlsplit(origin)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise ValueError("An HTTPS origin without credentials, path or query is required")
    opener = opener or build_opener(NoRedirect())
    outcomes = []
    for path in PATHS:
        request = Request(origin.rstrip("/") + path,
                          headers={"User-Agent": "EIS223-readonly-auth-probe/2.0"}, method="GET")
        try:
            response = opener.open(request, timeout=25)
        except HTTPError as exc:
            response = exc  # 303 and 401 are inspected without following them.
        with response:
            body = response.read(MAX_BODY + 1)
            outcomes.append({"path": path, "http": response.code,
                             "result": inspect_response(path, response.code, response.headers, body)})
    return {"probe": "anonymous_health_and_auth_gate", "results": outcomes,
            "authenticated_dashboard_verified": False,
            "procurement_processing_verified": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", default=DEFAULT_ORIGIN)
    args = parser.parse_args()
    print(json.dumps(run(args.origin), ensure_ascii=False, indent=2))
