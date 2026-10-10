"""Supplementary TenderGuru 223-FZ discovery within account quota."""
import asyncio
import datetime
import os
import re
import time
import httpx

_last=0.0
_lock=asyncio.Lock()

async def pull():
    global _last
    key=os.environ.get("TENDERGURU_API_CODE")
    if not key or time.monotonic()-_last<900:return []
    async with _lock:
        if time.monotonic()-_last<900:return []
        _last=time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=12,follow_redirects=False) as client:
                r=await client.get("https://www.tenderguru.ru/api2.3/export",params={
                    "fz":223,"dtype":"json","page":0,
                    "date_start":(datetime.date.today()-datetime.timedelta(days=4)).isoformat(),
                    "api_code":key})
            r.raise_for_status()
            data=r.json()
        except (httpx.HTTPError,ValueError,TypeError):
            print("V281 TENDERGURU_FEED_UNAVAILABLE",flush=True)
            return []
        def records(obj,depth=0):
            if isinstance(obj,list):return obj
            if isinstance(obj,dict) and depth<4:
                for k in ("Items","Item","items","data","results","result","tenders"):
                    if k in obj:return records(obj[k],depth+1)
            return []
        result=[]
        seen=set()
        for item in records(data)[:120]:
            if not isinstance(item,dict):continue
            reg=str(item.get("TenderNumber") or item.get("TenderNum") or
                    item.get("tend_num") or item.get("PurchaseNumber") or
                    item.get("RegNumber") or item.get("regNumber") or "").strip()
            if not re.fullmatch(r"[0-9]{10,20}",reg) or reg in seen:continue
            law=str(item.get("Fz") or item.get("fz") or "223")
            if law not in ("223","223-ФЗ"):continue
            seen.add(reg)
            result.append({"regNumber":reg,"reg_number":reg,"purchaseNumber":reg,
                           "source":"tenderguru_discovery"})
        print("V281 TENDERGURU_DISCOVERY",len(result),flush=True)
        return result
