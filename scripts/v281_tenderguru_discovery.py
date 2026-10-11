"""Supplementary TenderGuru 223-FZ discovery within account quota."""
import asyncio
import datetime
import os
import re
import time
import httpx

_last=0.0
_page=0
_lock=asyncio.Lock()

async def pull():
    global _last,_page
    key=os.environ.get("TENDERGURU_API_CODE")
    if not key or time.monotonic()-_last<900:return []
    async with _lock:
        if time.monotonic()-_last<900:return []
        _last=time.monotonic()
        page=_page
        _page=(_page+1)%5
        try:
            async with httpx.AsyncClient(timeout=12,follow_redirects=False) as client:
                r=await client.get("https://www.tenderguru.ru/api2.3/export",params={
                    "fz":223,"dtype":"json","page":page,
                    "date_start":(datetime.date.today()-datetime.timedelta(days=45)).isoformat(),
                    "api_code":key})
            r.raise_for_status()
            data=r.json()
        except (httpx.HTTPError,ValueError,TypeError):
            print("V281 TENDERGURU_FEED_UNAVAILABLE",flush=True)
            return []
        def records(obj,depth=0):
            if depth>8:return []
            if isinstance(obj,list):
                output=[]
                for entry in obj[:250]:
                    output.extend(records(entry,depth+1))
                return output
            if isinstance(obj,dict):
                if any(k in obj for k in ("TenderNumber","TenderNum","tend_num","PurchaseNumber","RegNumber","regNumber","TenderNumOuter")):
                    return [obj]
                output=[]
                for v in obj.values():
                    if isinstance(v,(dict,list)):
                        output.extend(records(v,depth+1))
                return output
            return []
        if isinstance(data,dict):print("V283 TENDERGURU_SCHEMA",sorted(str(k)[:30] for k in data.keys())[:12],flush=True)
        raw_rows=records(data)
        if raw_rows:
            print("V286 TENDERGURU_ROW_KEYS",sorted(str(k)[:35] for k in raw_rows[0].keys())[:30],flush=True)
        elif isinstance(data,dict):
            print("V287 TENDERGURU_EMPTY_STRUCTURE",[(str(k)[:30],type(v).__name__) for k,v in list(data.items())[:12]],flush=True)
        result=[]
        seen=set()
        for item in raw_rows[:250]:
            if not isinstance(item,dict):continue
            reg=str(item.get("TenderNumber") or item.get("TenderNum") or
                    item.get("tend_num") or item.get("PurchaseNumber") or
                    item.get("RegNumber") or item.get("regNumber") or
                    item.get("TenderNumOuter") or "").strip()
            if not re.fullmatch(r"[0-9]{10,20}",reg) or reg in seen:continue
            law=str(item.get("Fz") or item.get("fz") or "223")
            if law not in ("223","223-ФЗ"):continue
            seen.add(reg)
            result.append({"regNumber":reg,"reg_number":reg,"purchaseNumber":reg,
                           "source":"tenderguru_discovery"})
        print("V281 TENDERGURU_DISCOVERY",len(result),"page",page,flush=True)
        return result
