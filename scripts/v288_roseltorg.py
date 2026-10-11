"""Read-only Roseltorg public search connector; never claims original verification."""
import re
import time
import httpx
from html.parser import HTMLParser
from urllib.parse import urljoin,urlsplit
from fastapi.responses import Response
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

class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links=[]
        self.href=None
        self.label=[]
    def handle_starttag(self,tag,attrs):
        if tag=="a":
            self.href=dict(attrs).get("href")
            self.label=[]
    def handle_data(self,data):
        if self.href is not None:self.label.append(data)
    def handle_endtag(self,tag):
        if tag=="a" and self.href:
            label=" ".join(self.label).strip()[:180]
            url=urljoin("https://www.roseltorg.ru",self.href)
            parsed=urlsplit(url)
            if parsed.scheme=="https" and parsed.hostname in ("www.roseltorg.ru","roseltorg.ru") and (
                re.search(r"\.(?:pdf|docx?|xlsx?|odt|ods|zip|rar|7z)$",parsed.path,re.I)
                or any(token in parsed.path.lower() for token in ("/download/","/file/","/attachment/","/webdav/"))
            ):
                self.links.append({"name":label,"url":url})
        if tag=="a":self.href=None

async def document_links(procedure):
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,35}",procedure):
        raise HTTPException(400,"Неверный номер процедуры")
    try:
        async with httpx.AsyncClient(timeout=18,follow_redirects=False) as client:
            response=await client.get("https://www.roseltorg.ru/procedure/"+procedure)
        if response.status_code!=200:raise HTTPException(502,"Карточка Росэлторга недоступна")
        parser=_Links()
        parser.feed(response.text[:1500000])
        unique={x["url"]:x for x in parser.links}
        return {"source":"roseltorg","procedure":procedure,"files":list(unique.values())[:100],
                "originals_verified":False,"counted_as_checked":False}
    except httpx.HTTPError:
        raise HTTPException(502,"Ошибка обращения к Росэлторгу")

def install_documents(ns):
    app=ns["app"]
    @app.get("/api/v289/roseltorg/documents/{procedure}",include_in_schema=False)
    async def documents(procedure:str):
        return await document_links(procedure)
    @app.get("/api/v289/roseltorg/download/{procedure}/{index}",include_in_schema=False)
    async def download(procedure:str,index:int):
        listing=await document_links(procedure)
        if index<0 or index>=len(listing["files"]):
            raise HTTPException(404,"Документ не найден")
        url=listing["files"][index]["url"]
        try:
            async with httpx.AsyncClient(timeout=25,follow_redirects=False) as client:
                response=await client.get(url)
            if response.status_code!=200:raise HTTPException(502,"Оригинал недоступен")
            if len(response.content)>20*1024*1024:raise HTTPException(413,"Файл превышает лимит")
            typ=response.headers.get("content-type","application/octet-stream").split(";")[0]
            if typ in ("text/html","application/xhtml+xml"):
                raise HTTPException(502,"Вместо файла возвращена страница")
            return Response(response.content,media_type=typ,headers={"Content-Disposition":'attachment; filename="roseltorg-original"'})
        except httpx.HTTPError:
            raise HTTPException(502,"Ошибка загрузки оригинала")

def install_queue_bridge(ns):
    """Attach verified public Roseltorg file links to existing document metadata.
    Existing document recovery and legal validation remain authoritative."""
    import json
    import asyncio
    import os
    import sqlite3
    lock=asyncio.Lock()
    last={}
    async def attach(reg):
        if not re.fullmatch(r"[0-9]{10,20}",reg):return 0
        if time.monotonic()-last.get(reg,0)<900:return 0
        async with lock:
            if time.monotonic()-last.get(reg,0)<3600:return 0
            last[reg]=time.monotonic()
        try:
            found=await lookup(reg)
            count=0
            for procedure in found.get("procedures",[])[:3]:
                try:
                    listing=await document_links(procedure["id"])
                except Exception:continue
                links=[x["url"] for x in listing.get("files",[]) if x.get("url")]
                if not links:continue
                def store():
                    db=ns["conn"]()
                    try:
                        rows=db.execute("SELECT id,metadata_json FROM documents WHERE reg_number=? LIMIT 30",(reg,)).fetchall()
                        changed=0
                        for row in rows:
                            meta=json.loads(row["metadata_json"] or "{}")
                            if not isinstance(meta,dict):continue
                            existing=meta.get("alt_urls") or []
                            if not isinstance(existing,list):existing=[]
                            merged=list(dict.fromkeys(links+[u for u in existing if isinstance(u,str)]))[:50]
                            if merged==existing:continue
                            meta["alt_urls"]=merged
                            db.execute("UPDATE documents SET metadata_json=? WHERE id=?",(json.dumps(meta,ensure_ascii=False),row["id"]))
                            changed+=1
                        if changed:db.commit()
                        return changed
                    finally:db.close()
                count+=await asyncio.to_thread(store)
            if count:print("V290 ROSELTORG_ALT_URLS reg="+reg+" documents="+str(count),flush=True)
            return count
        except Exception as exc:
            print("V290 ROSELTORG_BRIDGE_ERROR "+type(exc).__name__,flush=True)
            return 0
    ns["_v290_roseltorg_attach"]=attach
