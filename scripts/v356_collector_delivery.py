"""Serve the public browser collector from the application's own domain.

The collector's Railway upload token is requested in the EIS browser console,
not embedded here. No external GitHub fetch is required at runtime.
"""
from pathlib import Path
from fastapi import HTTPException
from fastapi.responses import FileResponse

COLLECTOR = Path(__file__).resolve().with_name("v299_browser_collector.js")


def install(ns):
    app = ns["app"]

    @app.get("/eis223/collector.js", include_in_schema=False)
    async def eis223_collector_script():
        if not COLLECTOR.is_file():
            raise HTTPException(status_code=503, detail="Collector unavailable")
        return FileResponse(
            path=str(COLLECTOR),
            media_type="application/javascript",
            headers={
                "Access-Control-Allow-Origin": "https://zakupki.gov.ru",
                "Vary": "Origin",
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )
