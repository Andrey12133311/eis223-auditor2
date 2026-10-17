"""Tenant-isolated adapter for the existing EIS audit functions.

Uses app.py EIS discovery/analysis functions but never invokes its shared
SQLite save() or global process_purchase() pipeline.
"""
import asyncio
import re

import httpx

from scripts.account_api_v1 import connect
from scripts.account_thematic_worker_v1 import save_findings


async def collect(reg_number):
    import app as legacy
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(35, connect=12),
        follow_redirects=True,
        http2=False,
    ) as client:
        card = await legacy.fetch_eis_card(client, reg_number, {})
        docs = await legacy.discover_eis_documents(client, reg_number, card)
        if not docs:
            return None
        await legacy.download_all_documents(client, reg_number, docs)
        readable = [d for d in docs if d.get("status") == "checked"]
        if not readable:
            return None
        summary = card.get("summary") or {}
        purchase = {
            "reg": reg_number,
            "title": summary.get("title") or "",
            "customer": summary.get("customer") or "",
            "method": summary.get("method") or "",
            "price": summary.get("price") or "",
            "submission_start": summary.get("submission_start"),
            "submission_end": summary.get("submission_end"),
            "deadline": summary.get("submission_end"),
            "url": legacy.old_eis_url(reg_number),
        }
        raw = legacy.analyze(purchase, readable)
        raw_checks = legacy.build_checks(purchase, {}, card, readable)
        findings = []
        for item in raw:
            if item.get("kind") != "violation":
                continue
            source = item.get("source_url") or item.get("document")
            if not source or not item.get("law") or not item.get("evidence"):
                continue
            findings.append({
                "finding_key": (item.get("code") or "violation") + ":" +
                               str(item.get("document") or "eis")[:120],
                "legal_basis": str(item["law"]),
                "description": str(item.get("title") or "") + ": " +
                               str(item["evidence"]),
                "evidence_source": str(source),
            })
        for item in raw_checks:
            if item.get("status") != "violation":
                continue
            source = item.get("source_url") or item.get("source")
            if not source or not item.get("law") or not item.get("evidence"):
                continue
            findings.append({
                "finding_key": "check:" + str(item.get("scope") or "")[:60] +
                               ":" + str(item.get("item") or "")[:100],
                "legal_basis": str(item["law"]),
                "description": str(item.get("item") or "") + ": " +
                               str(item["evidence"]),
                "evidence_source": str(source),
            })
        return findings


def audit(owner_id, batch_id, reg_number, attempts):
    """Return True only after a completed evidence-backed EIS pass."""
    if not isinstance(reg_number, str) or not re.fullmatch(r"3[0-9]{10}", reg_number):
        return False
    findings = asyncio.run(collect(reg_number))
    if findings is None:
        return False
    with connect() as db:
        return save_findings(db, owner_id, batch_id, reg_number, attempts, findings)
