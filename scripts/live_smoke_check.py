#!/usr/bin/env python3
"""Read-only checks for the live EIS 223 Railway deployment.

Safe for CI: does not trigger scans, download files, or expose procurement records.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("EIS223_SMOKE_URL", "https://web-production-1c2ea.up.railway.app").rstrip("/")
CHECKS = (
    ("/health", "json"),
    ("/", "html"),
    ("/api/org-forms", "json"),
    ("/api/v204/status", "json"),
    ("/api/v233/status", "json"),
    ("/api/v204/dashboard", "json"),
)

def fetch_readonly(path: str) -> tuple[int, bytes, str]:
    url = BASE + path
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "EIS223-readonly-smoke-check/1.0", "Accept": "application/json,text/html"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        data = response.read(16 * 1024 * 1024 + 1)
        if len(data) > 16 * 1024 * 1024:
            raise ValueError("API response exceeds 16 MiB smoke-check limit")
        return response.status, data, response.headers.get("Content-Type", "")

def check() -> int:
    parts = urllib.parse.urlsplit(BASE)
    if parts.scheme != "https" or not parts.netloc or parts.username or parts.password or parts.path:
        print("Invalid EIS223_SMOKE_URL (require plain HTTPS origin)", file=sys.stderr)
        return 2
    failures = 0
    for path, expected in CHECKS:
        try:
            status, data, content_type = fetch_readonly(path)
            if status != 200:
                raise ValueError("Unexpected HTTP status " + str(status))
            if not data:
                raise ValueError("Empty HTTP response")
            if expected == "json":
                parsed = json.loads(data)
                if not isinstance(parsed, (dict, list)):
                    raise ValueError("JSON must be an object or array")
                if path == "/health" and isinstance(parsed, dict) and parsed.get("status") not in ("ok", "healthy"):
                    raise ValueError("Health endpoint reported unhealthy")
            elif b"<" not in data[:4096]:
                raise ValueError("Homepage was not HTML")
            print("PASS", path, "HTTP", status, "bytes", len(data))
        except (ValueError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            failures += 1
            print("FAIL", path, type(exc).__name__, str(exc)[:200])
    print("SMOKE_RESULT", "OK" if failures == 0 else "FAIL", "passed", len(CHECKS) - failures, "total", len(CHECKS))
    return 1 if failures else 0

if __name__ == "__main__":
    sys.exit(check())
