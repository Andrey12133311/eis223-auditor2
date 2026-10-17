"""Capture new real EIS thematic findings and email their authenticated owners.

A SQLite INSERT trigger journals findings transactionally alongside the real
v342/v345 evidence. PostgreSQL keeps the mail outbox and per-owner dedup keys.
Successful cross-DB handoff removes journal rows; retries are idempotent.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re

from scripts.account_notifications_v1 import enqueue, dispatch_pending

log = logging.getLogger(__name__)
_TASK = None


def open_eis(ns):
    import v342_thematic_queue
    con = v342_thematic_queue.db(ns)
    con.execute("PRAGMA busy_timeout=2000")
    return con


def install_capture(ns):
    """Install BEFORE the EIS worker starts. Existing historical rows are ignored."""
    db = open_eis(ns)
    try:
        db.execute(
            """CREATE TABLE IF NOT EXISTS thematic_mail_events(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id TEXT NOT NULL,
                reg TEXT NOT NULL,
                category TEXT NOT NULL,
                title TEXT NOT NULL,
                law TEXT NOT NULL,
                evidence TEXT NOT NULL
            )"""
        )
        db.execute(
            """CREATE TRIGGER IF NOT EXISTS thematic_mail_on_finding
               AFTER INSERT ON thematic_findings BEGIN
                 INSERT INTO thematic_mail_events
                   (batch_id,reg,category,title,law,evidence)
                 VALUES (NEW.batch_id,NEW.reg,
                         substr(COALESCE(NEW.category,''),1,100),
                         substr(COALESCE(NEW.title,''),1,250),
                         substr(COALESCE(NEW.law,''),1,250),
                         substr(COALESCE(NEW.evidence,''),1,350));
               END"""
        )
        db.commit()
    finally:
        db.close()


def finding_hash(event):
    def norm(field):
        return " ".join(str(event.get(field) or "").casefold().replace("ё", "е").split())
    message = "\x1f".join(norm(field) for field in
                         ("category", "title", "law", "evidence"))
    return hashlib.sha256(message.encode("utf-8")).hexdigest()


def safe(value, length):
    return re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or "")).strip()[:length]


def format_message(batch, reg, findings):
    count = len(findings)
    subject = "ЕИС Аудитор — новые нарушения в тематической проверке: " + reg
    body = [
        "При тематической проверке закупки выявлены новые нарушения.",
        "Номер закупки ЕИС: " + reg,
        "Новых нарушений: " + str(count),
        "",
    ]
    for i, event in enumerate(findings[:25], 1):
        body.append(str(i) + ". " + (safe(event["title"], 240) or "Нарушение"))
        basis = safe(event["law"], 200)
        if basis:
            body.append("Основание: " + basis)
        body.append("")
    origin = os.getenv("EIS223_PUBLIC_ORIGIN", "").rstrip("/")
    if origin.startswith("https://"):
        body.extend(["Карточка проверки:",
                     origin + "/thematic/card/" + batch + "/" + reg, ""])
    body.append("Подтверждающие документы и подробности доступны после входа в личный кабинет.")
    return subject, "\n".join(body)


def poll(ns, connect):
    """Transfer at most 160 journal entries; never acknowledge before PG commit."""
    source = open_eis(ns)
    try:
        entries = [dict(row) for row in source.execute(
            """SELECT id,batch_id,reg,category,title,law,evidence
               FROM thematic_mail_events ORDER BY id LIMIT 160"""
        ).fetchall()]
    finally:
        source.close()
    if not entries:
        return 0
    batches = sorted({event["batch_id"] for event in entries
                      if re.fullmatch(r"[0-9a-f]{24}", event["batch_id"])})
    grouped = {}
    with connect() as db:
        members = db.execute(
            """SELECT b.batch_id,b.user_id
               FROM account_thematic_batches b
               JOIN account_users u ON u.id=b.user_id
               WHERE b.batch_id=ANY(%s) AND u.status='approved'""",
            (batches,),
        ).fetchall() if batches else []
        owners = {}
        for batch, uid in members:
            owners.setdefault(batch, []).append(uid)
        for event in entries:
            batch, reg = event["batch_id"], event["reg"]
            if not re.fullmatch(r"[0-9a-f]{24}", batch) or not re.fullmatch(r"3[0-9]{10}", reg):
                continue
            digest = finding_hash(event)
            for user_id in owners.get(batch, []):
                new_row = db.execute(
                    """INSERT INTO account_thematic_email_seen
                       (user_id,batch_id,reg_number,finding_hash)
                       VALUES (%s,%s,%s,%s)
                       ON CONFLICT DO NOTHING RETURNING finding_hash""",
                    (user_id,batch,reg,digest),
                ).fetchone()
                if new_row:
                    grouped.setdefault((user_id,batch,reg), []).append(
                        (digest,event)
                    )
        for (user_id,batch,reg), findings in grouped.items():
            # Per procurement/scan digest, not one message for every row.
            fingerprint = hashlib.sha256(
                "\x1f".join(sorted(x[0] for x in findings)).encode("ascii")
            ).hexdigest()[:32]
            subject, body = format_message(batch,reg,[x[1] for x in findings])
            enqueue(db,user_id,"thematic:" + batch + ":" + reg + ":" + fingerprint,
                    subject,body,batch_id=batch)
    # PG outbox and seen-finding keys committed before removing SQLite entries.
    source = open_eis(ns)
    try:
        source.execute("DELETE FROM thematic_mail_events WHERE id <= ?",(entries[-1]["id"],))
        source.commit()
    finally:
        source.close()
    return sum(len(items) for items in grouped.values())


async def run_notifications(ns):
    from scripts.account_api_v1 import connect
    await asyncio.sleep(8)  # Let account schema migration finish on startup.
    while True:
        try:
            # A disk/SQLite transient must not crash the EIS audit worker.
            await asyncio.to_thread(install_capture, ns)
            await asyncio.to_thread(poll, ns, connect)
        except Exception as exc:
            log.warning("Thematic mail queue unavailable: %s", type(exc).__name__)
        try:
            await asyncio.to_thread(dispatch_pending, connect, 3)
        except Exception as exc:
            log.warning("Mail delivery unavailable: %s", type(exc).__name__)
        await asyncio.sleep(30)


def install(ns):
    if os.getenv("EIS223_ACCOUNTS_MOUNT", "").lower() != "true":
        return False
    try:
        install_capture(ns)
    except Exception as exc:
        log.warning("Thematic mail capture deferred: %s", type(exc).__name__)
    app = ns["app"]

    @app.on_event("startup")
    async def start_thematic_email_notifications():
        global _TASK
        if _TASK is None or _TASK.done():
            _TASK = asyncio.create_task(run_notifications(ns),
                                        name="thematic-email-notifications")
    return True
