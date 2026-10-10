"""TenderGuru API integration (server-side credential, read-only).
No original-document checks or violation counters are inferred from this metadata.
"""
from __future__ import annotations
import asyncio
import os
import re
import time
from fastapi import HTTPException
import httpx

_URL="https://www.tenderguru.ru/api2.3/export"
_cache={}
_lock=asyncio.Lock()
_request_times=[]

def install(ns):
    app=ns["app"]

    @app.get("/api/v278/tenderguru/{reg}", include_in_schema=False)
    async def tenderguru_card(reg: str):
        if not re.fullmatch(r"[0-9]{10,20}",reg):
            raise HTTPException(400,"Неверный номер закупки")
        # Restrict lookups to purchases already registered in the local database.
        with ns['conn']() as db:
            known=db.execute('SELECT 1 FROM purchases WHERE reg_number=? LIMIT 1',(reg,)).fetchone()
        if not known:raise HTTPException(404,'Закупка отсутствует в базе')
        key=os.environ.get("TENDERGURU_API_CODE","")
        if not key:
            raise HTTPException(503,"Ключ TenderGuru не настроен")
        now=time.monotonic()
        hit=_cache.get(reg)
        if hit and now-hit[0]<1800:
            return hit[1]
        async with _lock:
            now=time.monotonic()
            hit=_cache.get(reg)
            if hit and now-hit[0]<1800:
                return hit[1]
            # Bound use of the account quota even with many dashboard visitors.
            global _request_times
            _request_times[:]=[t for t in _request_times if now-t<60]
            if len(_request_times)>=3:raise HTTPException(429,'Лимит запросов TenderGuru: повторите позже')
            _request_times.append(now)
            try:
                async with httpx.AsyncClient(timeout=12.0,follow_redirects=False) as client:
                    response=await client.get(_URL,params={
                        "tend_num":reg,"f":"223","dtype":"json","api_code":key
                    },headers={"Accept":"application/json"})
                if response.status_code in (401,403,429):
                    raise HTTPException(503,"TenderGuru: ключ, тариф или лимит запросов")
                response.raise_for_status()
                payload=response.json()
            except HTTPException:
                raise
            except (httpx.HTTPError, ValueError):
                # Avoid logging exception strings: the request URL includes an API code.
                raise HTTPException(502,"Источник TenderGuru не ответил")
            # Return bounded, non-sensitive fields only; never return a URL containing api_code.
            def items(value):
                if isinstance(value,list):return value[:15]
                if isinstance(value,dict):
                    for field in ("Items","items","Item","item"):
                        if field in value:return items(value[field])
                    return [value]
                return []
            rows=[]
            for entry in items(payload):
                if not isinstance(entry,dict):continue
                rows.append({k:str(entry.get(k) or "")[:500] for k in (
                    "ID","TenderName","Customer","CustomerINN","Date","EndTime",
                    "Price","Fz","EisLink","TenderType"
                ) if entry.get(k) is not None})
            result={"reg":reg,"source":"TenderGuru","records":rows,
                    "originals_verified":False,"counted_as_checked":False}
            if len(_cache)>600:_cache.clear()
            _cache[reg]=(time.monotonic(),result)
            return result
