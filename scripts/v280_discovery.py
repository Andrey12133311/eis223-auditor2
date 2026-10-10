"""Bounded DaMIA 223-FZ discovery feed. Never count metadata as checked documents."""
import asyncio
import datetime
import os
import time
import httpx

_LAST=0.0
_LOCK=asyncio.Lock()
async def pull():
    global _LAST
    if not os.environ.get("DAMIA_API_KEY"):return []
    now=time.monotonic()
    if now-_LAST<300:return []
    async with _LOCK:
        now=time.monotonic()
        if now-_LAST<300:return []
        _LAST=now
        since=(datetime.date.today()-datetime.timedelta(days=3)).isoformat()
        try:
            async with httpx.AsyncClient(timeout=12,follow_redirects=False) as client:
                response=await client.get("https://api.damia.ru/zakupki/zsearch",params={
                    "fz":223,"page":1,"from_date":since,"key":os.environ["DAMIA_API_KEY"]})
            response.raise_for_status()
            body=response.json()
        except (httpx.HTTPError,ValueError,TypeError):
            print("V280 DAMIA_DISCOVERY_UNAVAILABLE",flush=True)
            return []
        def records(x,depth=0):
            if isinstance(x,list):return x
            if isinstance(x,dict) and depth<3:
                for k in ("data","items","result","results","Закупки","Список","223"):
                    if k in x:return records(x[k],depth+1)
                # Mapping from law number to procurement rows.
                for v in x.values():
                    if isinstance(v,list) and v and isinstance(v[0],dict):
                        return v
            return []
        output=[];seen=set()
        for item in records(body)[:100]:
            if not isinstance(item,dict):continue
            reg=str(item.get("РегНомер") or item.get("regNumber") or item.get("reg_number") or "").strip()
            if not(reg.isdecimal() and 10<=len(reg)<=20 and reg not in seen):continue
            law=str(item.get("ФЗ") or item.get("fz") or "223")
            if law not in ("223","223-ФЗ"):continue
            seen.add(reg)
            # Minimal hint only; the existing pipeline fetches the real EIS card.
            output.append({"regNumber":reg,"reg_number":reg,"purchaseNumber":reg,
                          "title":str(item.get("Продукт") or "")[:180],
                          "source":"damia_discovery"})
        print("V280 DAMIA_DISCOVERY",len(output),flush=True)
        return output
