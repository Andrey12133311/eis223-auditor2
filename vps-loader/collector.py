#!/usr/bin/env python3
"""Run the existing EIS collector on an unattended VPS using a real browser."""
import asyncio, logging, os, signal, sys, urllib.request
from urllib.parse import urlparse
from playwright.async_api import async_playwright

RAIL=os.getenv("EIS223_RAILWAY_URL","https://web-production-1c2ea.up.railway.app").rstrip("/")
URL="https://zakupki.gov.ru/"
STOP=asyncio.Event()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log=logging.getLogger("eis223-vps")

def fetch_collector():
    src=RAIL+"/api/v299/eis223/collector.js"
    if urlparse(src).scheme!="https":raise RuntimeError("HTTPS Railway URL required")
    with urllib.request.urlopen(src,timeout=40) as r: raw=r.read(250001)
    if len(raw)>250000:raise RuntimeError("Collector script is too large")
    js=raw.decode("utf-8-sig")
    if "__eis223_five_minutes" not in js or "COLLECTOR_VERSION" not in js:
        raise RuntimeError("Unexpected collector response")
    return js

async def run(token):
    js=await asyncio.to_thread(fetch_collector)
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(headless=True)
        try:
            context=await browser.new_context(locale="ru-RU",timezone_id="Europe/Moscow")
            page=await context.new_page()
            async def dialog_handler(d):
                if d.type=="prompt" and "EIS223_UPLOAD_TOKEN" in d.message:
                    await d.accept(token)
                else:
                    await d.dismiss()
            page.on("dialog",lambda d:asyncio.create_task(dialog_handler(d)))
            page.on("console",lambda m:log.info("Browser %s: %s",m.type,m.text[:900]))
            page.on("pageerror",lambda e:log.warning("Browser JS error: %s",str(e)[:400]))
            response=await page.goto(URL,wait_until="domcontentloaded",timeout=60000)
            if response and response.status in (401,403,429,503):
                raise RuntimeError("EIS temporarily rejected access (HTTP %s)"%response.status)
            if urlparse(page.url).netloc!="zakupki.gov.ru":
                raise RuntimeError("EIS changed origin unexpectedly")
            await page.evaluate(js)
            log.info("Official-origin collector started; polling existing Railway queues")
            while not STOP.is_set():
                if page.is_closed():raise RuntimeError("Browser page closed")
                state=await page.evaluate(
                    "() => window.__eis223_five_minutes?.status?.() ?? null"
                )
                if not state or not state.get("active"):
                    raise RuntimeError("Official EIS collector is not active")
                log.info("active=%s cycles=%s uploaded=%s",state.get("active"),
                         state.get("cycles"),state.get("uploaded"))
                try:await asyncio.wait_for(STOP.wait(),timeout=30)
                except asyncio.TimeoutError:pass
        finally:
            await browser.close()

async def main():
    token=os.getenv("EIS223_UPLOAD_TOKEN","").strip()
    if not token:
        log.error("Set EIS223_UPLOAD_TOKEN, the Railway upload token, not EIS API token")
        return 2
    loop=asyncio.get_running_loop()
    for sig in (signal.SIGTERM,signal.SIGINT):
        try:loop.add_signal_handler(sig,STOP.set)
        except NotImplementedError:pass
    while not STOP.is_set():
        try:await run(token)
        except Exception as e:log.error("Collector interrupted: %s",str(e)[:600])
        if not STOP.is_set():
            try:await asyncio.wait_for(STOP.wait(),timeout=120)
            except asyncio.TimeoutError:pass
    return 0

if __name__=="__main__":
    try:sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:sys.exit(0)
