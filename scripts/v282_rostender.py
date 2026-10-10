"""Rostender read-only API: safe key checks and EIS procurement detail."""
import asyncio
import os
import re
import time
import httpx
from fastapi import HTTPException

BASE="https://rostender.info/api/tenders/get/"
_CACHE={}
_LAST={}
_LOCK=asyncio.Lock()

async def call(path, ttl=900):
    key=os.environ.get("ROSTENDER_API_KEY","")
    if not key:raise HTTPException(503,"Ростендер: ключ не настроен")
    now=time.monotonic()
    if path in _CACHE and now-_CACHE[path][0]<ttl:return _CACHE[path][1]
    async with _LOCK:
        now=time.monotonic()
        if path in _CACHE and now-_CACHE[path][0]<ttl:return _CACHE[path][1]
        if path!="info" and now-_LAST.get("billable",0)<30:
            raise HTTPException(429,"Ростендер: ограничение частоты запросов")
        if path!="info":_LAST["billable"]=now
        try:
            async with httpx.AsyncClient(timeout=12,follow_redirects=False) as client:
                response=await client.get(BASE+path,headers={"X-API-KEY":key,"Accept":"application/json"})
            if response.status_code in (401,403):
                raise HTTPException(response.status_code,"Ростендер: ключ недействителен либо тариф ограничен")
            if response.status_code==404:raise HTTPException(404,"Ростендер: закупка не найдена")
            if response.status_code!=200:raise HTTPException(502,"Ростендер: источник недоступен")
            payload=response.json()
            if not isinstance(payload,dict) or payload.get("success") is not True:
                raise HTTPException(502,"Ростендер: неожиданный ответ")
        except HTTPException:raise
        except (httpx.HTTPError,ValueError):
            raise HTTPException(502,"Ростендер: ошибка подключения")
        if path=="info":
            data=payload.get("data") or {}
            value={"connected":True,"remaining":data.get("remaining"),"daily_limit":data.get("daily_limit"),"used_today":data.get("used_today")}
        else:
            data=payload.get("data") or {}
            if not isinstance(data,dict):raise HTTPException(502,"Ростендер: формат карточки не распознан")
            files=data.get("files") or {}
            result=[]
            for item in (files.get("items") or [])[:30]:
                if not isinstance(item,dict):continue
                url=item.get("link") or ""
                if not isinstance(url,str) or not url.startswith("https://"):continue
                result.append({"title":str(item.get("title") or "")[:180],"url":url})
            value={"source":"rostender","eis":str(data.get("eis") or "")[:30],
                   "customer":(data.get("customer") or {}).get("name"),
                   "files":result,"archive_available":bool(files.get("archive")),
                   "originals_verified":False,"counted_as_checked":False}
        if len(_CACHE)>400:_CACHE.clear()
        _CACHE[path]=(time.monotonic(),value)
        return value

def install(ns):
    app=ns["app"]
    @app.get("/api/v282/rostender/status",include_in_schema=False)
    async def status():return await call("info",300)
    @app.get("/api/v282/rostender/{reg}",include_in_schema=False)
    async def detail(reg:str):
        if not re.fullmatch(r"[0-9]{10,20}",reg):
            raise HTTPException(400,"Некорректный номер ЕИС")
        return await call("eis/"+reg,1800)
