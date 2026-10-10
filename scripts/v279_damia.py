"""Read-only DaMIA connector for 223-FZ procurement metadata.

Responses are not counted as checked original documents. Secret is exclusively
read from Railway environment. Never log exception URLs or response bodies.
"""
from __future__ import annotations
import asyncio
import os
import re
import time
from collections import deque
from contextlib import closing

import httpx
from fastapi import HTTPException

BASE="https://api.damia.ru/zakupki/"
_cache={}
_request_times=deque()
_lock=asyncio.Lock()

def install(ns):
    app=ns["app"]
    connect=ns["conn"]

    async def fetch(kind, params, cache_key):
        now=time.monotonic()
        hit=_cache.get(cache_key)
        if hit and now-hit[0]<900:return hit[1]
        async with _lock:
            now=time.monotonic()
            hit=_cache.get(cache_key)
            if hit and now-hit[0]<900:return hit[1]
            key=os.environ.get("DAMIA_API_KEY")
            if not key:raise HTTPException(503,"DaMIA не настроен")
            while _request_times and now-_request_times[0]>60:
                _request_times.popleft()
            if len(_request_times)>=4:raise HTTPException(429,"Лимит DaMIA, повторите позже")
            _request_times.append(now)
            try:
                async with httpx.AsyncClient(timeout=15,follow_redirects=False) as client:
                    result=await client.get(BASE+kind,params={**params,"key":key},headers={"Accept":"application/json"})
                if result.status_code in (401,403,429):
                    raise HTTPException(503,"DaMIA: доступ или лимит тарифного плана")
                if result.status_code!=200:raise HTTPException(502,"DaMIA: источник не ответил")
                data=result.json()
            except HTTPException:raise
            except (httpx.HTTPError,ValueError):
                raise HTTPException(502,"DaMIA: ошибка получения данных")
            # Bounded, lossy public metadata; do not expose credentials embedded in provider URLs.
            def cleanse(value,depth=0):
                if depth>4:return None
                if isinstance(value,dict):
                    return {str(k)[:70]:cleanse(v,depth+1) for k,v in list(value.items())[:55]
                            if not re.search(r"key|token|secret|парол|ключ",str(k),re.I)}
                if isinstance(value,list):return [cleanse(v,depth+1) for v in value[:30]]
                if isinstance(value,str):
                    value=value[:1200]
                    if "api.damia.ru" in value or "key=" in value.lower():return "[ссылка скрыта]"
                    return value
                if isinstance(value,(bool,int,float)) or value is None:return value
                return str(value)[:120]
            payload={"source":"DaMIA","data":cleanse(data),"documents_verified":False,
                     "added_to_verified_dashboard":False}
            if len(_cache)>300:_cache.clear()
            _cache[cache_key]=(time.monotonic(),payload)
            return payload

    @app.get("/api/v279/damia/purchase/{reg}",include_in_schema=False)
    async def card(reg:str):
        if not re.fullmatch(r"[0-9]{10,20}",reg):raise HTTPException(400,"Неверный номер ЕИС")
        with closing(connect()) as db:
            exists=db.execute("SELECT 1 FROM purchases WHERE reg_number=? LIMIT 1",(reg,)).fetchone()
        if not exists:raise HTTPException(404,"Закупка ещё отсутствует в базе")
        return await fetch("zakupka",{"regn":reg,"actual":1},"purchase:"+reg)

    @app.get("/api/v279/damia/customer/{inn}",include_in_schema=False)
    async def customer(inn:str):
        if not re.fullmatch(r"[0-9]{10}|[0-9]{12}",inn):raise HTTPException(400,"Неверный ИНН")
        with closing(connect()) as db:
            exists=db.execute("SELECT 1 FROM purchases WHERE customer_inn=? LIMIT 1",(inn,)).fetchone()
        if not exists:raise HTTPException(404,"Заказчик отсутствует в базе")
        return await fetch("customer",{"req":inn},"customer:"+inn)
