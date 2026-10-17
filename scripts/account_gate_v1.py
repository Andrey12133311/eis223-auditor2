"""Fail-closed ASGI authentication gate for staged deployment.

Only use after reviewing the public route allowlist and machine-to-machine
EIS bridge authentication. Do not enable on the existing production service
without integration tests: existing bridge workers must remain functional.
"""
import os
from urllib.parse import urlsplit

PUBLIC_PATHS = frozenset((
    "/auth/register", "/auth/login",
    "/account/register", "/account/login",
    "/health", "/healthz",
))
PUBLIC_PREFIXES = ("/static/",)


def is_public_path(path):
    return path in PUBLIC_PATHS or any(path.startswith(p) for p in PUBLIC_PREFIXES)


def enforce_auth_enabled():
    return os.environ.get("EIS223_AUTH_ENFORCE", "").lower() == "true"


def route_requires_account(path):
    return not is_public_path(path)


def should_deny_anonymous(path, authenticated, enabled=None):
    if enabled is None:
        enabled = enforce_auth_enabled()
    return enabled and route_requires_account(path) and not authenticated


def origin_matches(origin, canonical_origin):
    if not origin or not canonical_origin:
        return False
    parsed = urlsplit(origin)
    expected = urlsplit(canonical_origin)
    return (
        parsed.scheme == expected.scheme == "https"
        and parsed.netloc == expected.netloc
        and parsed.path in ("", "/")
        and not parsed.query and not parsed.fragment
    )
