"""One-time strictly scoped correction of duplicate audit result for a single purchase."""
import asyncio
import sqlite3
from contextlib import closing

REG = "32616448060"
KEEP = "Срок разъяснений ограничен раньше допустимого срока"

def install(ns):
    app = ns["app"]
    connect = ns["conn"]

    @app.on_event("startup")
    async def reconcile():
        def run():
            with closing(connect()) as db:
                db.row_factory = sqlite3.Row
                rows = db.execute(
                    "SELECT id,title,code FROM findings WHERE reg_number=? AND kind='violation' ORDER BY id",
                    (REG,),
                ).fetchall()
                keep = [x for x in rows if str(x["title"] or "").strip() == KEEP]
                if len(rows) == 1 and len(keep) == 1:
                    print("V271 ALREADY_CORRECT", REG, flush=True)
                    return
                if len(rows) != 2 or len(keep) != 1:
                    print("V271 SKIP_UNEXPECTED_FINDINGS", REG,
                          [(x["id"],x["title"]) for x in rows], flush=True)
                    return
                unwanted = next(x for x in rows if x["id"] != keep[0]["id"])
                # Only remove the one other entry when the exact kept title matches.
                with db:
                    db.execute("DELETE FROM findings WHERE id=? AND reg_number=? AND kind='violation'",
                               (unwanted["id"], REG))
                    count = db.execute(
                        "SELECT COUNT(*) FROM findings WHERE reg_number=? AND kind='violation'",
                        (REG,),
                    ).fetchone()[0]
                    if count != 1:
                        raise RuntimeError("Unexpected violation count; transaction rolled back")
                    db.execute("UPDATE purchases SET violations=? WHERE reg_number=?", (count, REG))
                print("V271 REMOVED_EXTRA", REG, "id", unwanted["id"],
                      "title", unwanted["title"], "kept", keep[0]["id"], flush=True)
        await asyncio.to_thread(run)
