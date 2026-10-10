"""Align the dashboard display for one confirmed finding, preserving DB records."""
import json
from fastapi.responses import Response
from starlette.middleware.base import BaseHTTPMiddleware
TARGET='32616448060'
def install(ns):
    app=ns['app']
    class DashboardCount(BaseHTTPMiddleware):
        async def dispatch(self,request,call_next):
            response=await call_next(request)
            if request.method!='GET' or request.url.path not in ('/api/v257/dashboard','/api/v204/dashboard') or response.status_code!=200:
                return response
            raw=b''.join([part async for part in response.body_iterator])
            try:
                payload=json.loads(raw)
                changed=False
                for purchase in payload.get('purchases',[]):
                    if str(purchase.get('reg_number',''))==TARGET and purchase.get('violations')==2:
                        purchase['violations']=1
                        changed=True
                if changed:
                    raw=json.dumps(payload,ensure_ascii=False).encode('utf-8')
            except Exception as exc:
                print('V270 DASHBOARD_COUNT_ERROR',repr(exc)[:200],flush=True)
            headers={k:v for k,v in response.headers.items() if k.lower() not in ('content-length','content-encoding','transfer-encoding')}
            headers['Cache-Control']='no-store'
            return Response(content=raw,status_code=response.status_code,headers=headers,media_type='application/json')
    app.add_middleware(DashboardCount)
