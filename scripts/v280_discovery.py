"""Bounded DaMIA 223-FZ discovery feed. Never count metadata as checked documents."""
import asyncio
import datetime
import os
import time
import httpx
import re

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
        def records(x, depth=0):
            if depth > 6:
                return []
            if isinstance(x, list):
                rows = [v for v in x if isinstance(v, dict)]
                if rows:
                    return rows
                return []
            if isinstance(x, dict):
                for k in ("223", "data", "items", "result", "results", "Закупки", "Список", "purchases", "tenders"):
                    if k in x:
                        found = records(x[k], depth + 1)
                        if found:
                            return found
                for v in x.values():
                    if isinstance(v, (dict, list)):
                        found = records(v, depth + 1)
                        if found:
                            return found
            return []
        if isinstance(body,dict):
            print("V283 DAMIA_SCHEMA", sorted(str(k)[:30] for k in body.keys())[:12],flush=True)
        output=[];seen=set()
        rows=records(body)
        if rows:
            print("V284 DAMIA_ROW_KEYS",sorted(str(k)[:35] for k in rows[0].keys())[:30],flush=True)
        for item in rows[:100]:
            if not isinstance(item,dict):continue
            reg=""
            for key in ("РегНомер","regNumber","reg_number","purchaseNumber","purchase_number",
                        "number","registry_number","registryNumber","purchase_number_eis",
                        "НомерЗакупки","Номер закупки","Номер извещения","regnum","regn","eis"):
                value=item.get(key)
                if isinstance(value,(str,int)):
                    match=re.search(r"(?<!\d)\d{10,20}(?!\d)",str(value))
                    if match:
                        reg=match.group()
                        break
            if not reg or reg in seen:continue
            law=str(item.get("ФЗ") or item.get("fz") or item.get("law") or "223")
            if law not in ("223","223-ФЗ","223-fz"):continue
            seen.add(reg)
            output.append({"regNumber":reg,"reg_number":reg,"purchaseNumber":reg,
                           "title":str(item.get("Продукт") or item.get("name") or "")[:180],
                           "source":"damia_discovery"})
        print("V280 DAMIA_DISCOVERY",len(output),flush=True)
        return output
