"""Read-only unified violation details for procurement cards."""
import re
import sqlite3
from contextlib import closing
from fastapi import HTTPException

def install(ns):
    app = ns["app"]
    connect = ns["conn"]

    @app.get("/api/v267/findings/{reg}", include_in_schema=False)
    def list_findings(reg: str):
        if not re.fullmatch(r"[0-9]{10,20}", reg):
            raise HTTPException(400, "Invalid registration number")
        with closing(connect()) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT code, title, evidence, document FROM findings "
                "WHERE reg_number=? AND kind='violation' ORDER BY id DESC",
                (reg,),
            ).fetchall()
        return {"reg": reg, "total": len(rows), "items": [dict(x) for x in rows]}
