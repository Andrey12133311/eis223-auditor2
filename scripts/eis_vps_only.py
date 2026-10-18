"""Enforce the deployed VPS as the sole EIS document intake channel."""
from __future__ import annotations

from fastapi import HTTPException
from fastapi.responses import JSONResponse, Response

from eis_server_worker import authenticate
from v301_search_jobs import _db, _utc

MESSAGE = "Документы ЕИС загружаются автоматически через VPS. Браузерный загрузчик отключён."
SCRIPT_PATHS = frozenset(("/eis223/collector.js", "/api/v299/eis223/collector.js"))
DISABLED_PATHS = frozenset((
    "/api/v294/eis223/upload", "/api/v295/eis223/documents",
    "/api/v310/eis223/card-metadata", "/api/v311/eis223/customer",
    "/api/v322/eis223/price", "/api/v338/eis223/card-price", "/api/v341/eis223/card-facts",
    "/api/v301/bridge/next",
))
STOP_SCRIPT = """(() => {
  try { window.__eis223_five_minutes?.stop(); } catch (_) {}
  console.info('Документы ЕИС загружаются автоматически через VPS. Браузерный загрузчик отключён.');
})();
"""


def release_browser_leases(ns):
    """Give interrupted browser work back to the VPS without touching its leases."""
    db = _db(ns)
    try:
        changed = db.execute("""UPDATE v301_search_jobs SET status='queued',
          not_before=0,lease_until=0,lease_token='',lease_owner='server-retry',
          attempts=MAX(0,attempts-1),updated_at=?,
          detail='Загрузка передана на VPS'
          WHERE kind='reg' AND status='leased' AND lease_owner=''""", (_utc(),)).rowcount
        # The installed worker accepts registry numbers. Keep former INN search
        # history with an explicit outcome instead of leaving unclaimable work.
        db.execute("""UPDATE v301_search_jobs SET status='error',lease_until=0,
          lease_owner='',lease_token='',updated_at=?,
          detail='В режиме VPS укажите номер закупки ЕИС.'
          WHERE kind='inn' AND status IN ('queued','leased','deferred')""", (_utc(),))
        db.commit()
        return changed
    finally:
        db.close()


def install(ns):
    # Install after the account gate so disabled legacy routes cannot accept a
    # browser request even if the browser still has a valid shared upload key.
    app = ns['app']
    released = release_browser_leases(ns)

    async def blocked_remote_fetch(*args, **kwargs):
        raise RuntimeError('EIS223_VPS_ONLY: Railway cannot retrieve EIS documents')

    # Also guard the old core's low-level entry points if a legacy caller is
    # invoked accidentally. Saved-file extraction and report generation remain local.
    for name in ('eis_request', 'fetch_binary'):
        if name in ns:
            ns[name] = blocked_remote_fetch

    @app.middleware('http')
    async def vps_only_intake(request, call_next):
        path = request.url.path.lower().rstrip('/')
        if path in SCRIPT_PATHS:
            return Response(STOP_SCRIPT, media_type='application/javascript', headers={
                'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                'Access-Control-Allow-Origin': 'https://zakupki.gov.ru', 'Vary': 'Origin',
            })
        legacy = (path in DISABLED_PATHS or path.startswith('/api/v301/bridge/report/')
                  or (path.startswith('/api/v299/eis223/')
                      and path != '/api/v299/eis223/monitor-status'))
        if legacy:
            return JSONResponse({'code': 'EIS223_VPS_ONLY', 'detail': MESSAGE,
                                 'intake_mode': 'vps_only', 'browser_collector_enabled': False},
                                status_code=410, headers={'Cache-Control': 'no-store'})
        upload = (path == '/api/v294/eis223/archive'
                  or path.startswith('/api/v295/eis223/original/'))
        if upload:
            if any(name in request.headers for name in
                   ('origin', 'sec-fetch-site', 'sec-fetch-mode', 'x-eis223-collector-version')):
                return JSONResponse({'code': 'EIS223_VPS_ONLY', 'detail': MESSAGE}, status_code=403)
            try:
                authenticate(request)
            except HTTPException as error:
                return JSONResponse({'detail': error.detail}, status_code=error.status_code)
        return await call_next(request)

    print('EIS_VPS_ONLY_READY browser_intake=False railway_downloads=False '
          'released_browser_leases=' + str(released), flush=True)
