"""Retire the one-procurement counter override, keeping private no-store headers.

V352 computes distinct saved legal episodes for every procurement. A hard-coded
2 -> 1 rewrite can suppress a genuine second event and disagree with totals.
Do not buffer/rewrite response bodies, counters or evidence here.
"""
from starlette.middleware.base import BaseHTTPMiddleware


def install(ns):
    class DashboardHeaders(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            response = await call_next(request)
            if request.method == 'GET' and request.url.path in (
                    '/api/v257/dashboard', '/api/v204/dashboard'):
                response.headers['Cache-Control'] = 'no-store'
            return response
    ns['app'].add_middleware(DashboardHeaders)
    print('V270_OVERRIDE_RETIRED count_basis=unique_saved_legal_episodes', flush=True)
