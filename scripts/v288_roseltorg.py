"""Read-only Roseltorg public search connector; never claims original verification."""
import re
import time
import httpx
from fastapi import HTTPException

_CACHE={}
async def lookup(number):
    now=time.monotonic()
    if number in _CACHE and now-_CACHE[number][0]<900:return _CACHE[number][1]
    try:
        async with httpx.AsyncClient(timeout=12,follow_redirects=False,headers={"User-Agent":"EIS223-Auditor/1.0 (public metadata lookup)"}) as client:
            response=await client.get("https://www.roseltorg.ru/procedures/search",params={"query_field":number})
        if response.status_code!=200:
            raise HTTPException(502,"Росэлторг: поиск недоступен")
        page=response.text[:1000000]
        # Public procedure identifiers only; no login, file download or guessed endpoints.
        found=list(dict.fromkeys(re.findall(r'/procedure/([A-Za-z0-9_-]{8,35})',page)))[:30]
        result={"source":"roseltorg","query":number,"procedures":[{"id":p,"url":"https://www.roseltorg.ru/procedure/"+p} for p in found],"originals_verified":False,"counted_as_checked":False}
        if len(_CACHE)>100:_CACHE.clear()
        _CACHE[number]=(now,result)
        return result
    except httpx.HTTPError:
        raise HTTPException(502,"Росэлторг: ошибка подключения")

def install(ns):
    app=ns["app"]
    @app.get("/api/v288/roseltorg/search/{number}",include_in_schema=False)
    async def search(number:str):
        if not re.fullmatch(r"[0-9]{10,20}",number):
            raise HTTPException(400,"Неверный номер ЕИС")
        return await lookup(number)
