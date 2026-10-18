"""Source-exact thematic evidence shared by cards and manager submissions."""
from hashlib import sha256
import json
import re
import sqlite3

from fastapi import HTTPException

BATCH = re.compile(r"^[0-9a-f]{24}$")
REG = re.compile(r"^3[0-9]{10}$")


def canonical_item(reg, item):
    from v365_consultant_refs import references
    title, law, evidence, document, url = (
        str(item.get(key) or "").strip()
        for key in ("title", "law", "evidence", "document", "source_url"))
    key = sha256(json.dumps([reg, title, law, evidence, document, url],
        ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    refs = references(item)
    warnings = []
    if not law: warnings.append("Не указана правовая норма")
    if not evidence: warnings.append("Отсутствует текст доказательства")
    if not (document or url): warnings.append("Не определён источник доказательства")
    if law and not refs: warnings.append("Нужно уточнить статью и часть нормы")
    return {"reg_number": reg, "finding_key": key, "title": title,
        "law": law, "evidence": evidence, "document": document,
        "source_url": url, "references": refs,
        "supporting_sources": item.get("supporting_sources", []),
        "source_representations": int(item.get("raw_count", 1)), "warnings": warnings,
        "category": item.get("category", ""), "law_url": item.get("law_url", ""),
        "code": item.get("code", "")}


def require_batch_owner(db, owner, batch):
    if not BATCH.fullmatch(batch):
        raise HTTPException(422, "Некорректный номер тематической проверки")
    if not db.execute("SELECT 1 FROM account_thematic_batches WHERE user_id=%s AND batch_id=%s",
                      (owner, batch)).fetchone():
        raise HTTPException(404, "Тематическая проверка не найдена в вашем кабинете")


def thematic_items(ns, batch, reg):
    """Caller checks the batch owner; never substitute general-audit findings."""
    if not BATCH.fullmatch(batch) or not REG.fullmatch(reg):
        raise HTTPException(422, "Некорректный номер проверки или закупки")
    from v352_dashboard_truth import collapse_legal_events
    db = ns["conn"]()
    try:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=1500")
        db.execute("PRAGMA query_only=ON")
        row = db.execute("SELECT b.categories FROM thematic_batches b JOIN thematic_items i "
            "ON i.batch_id=b.id WHERE b.id=? AND i.reg=?", (batch, reg)).fetchone()
        if row is None:
            raise HTTPException(404, "Закупка не найдена в тематической проверке")
        try:
            categories = json.loads(row[0])
        except (TypeError, ValueError) as exc:
            raise HTTPException(409, "Направления тематической проверки повреждены") from exc
        if not isinstance(categories, list) or not all(isinstance(x, str) for x in categories):
            raise HTTPException(409, "Направления тематической проверки повреждены")
        raw = [dict(r) for r in db.execute("SELECT category,title,law,evidence,code,document,source_url,law_url "
            "FROM thematic_findings WHERE batch_id=? AND reg=? ORDER BY rowid", (batch, reg))
            if r["category"] in categories]
        return [canonical_item(reg, item) for item in collapse_legal_events(raw)]
    finally:
        db.close()


def review_items(db, ns, owner, reg, general_items):
    """Resolve manager evidence from each employee submission's stored source."""
    sources = db.execute("SELECT finding_key,source_batch FROM account_finding_submissions "
        "WHERE user_id=%s AND reg_number=%s AND source_batch<>''", (owner, reg)).fetchall()
    if not sources:
        return general_items
    source_keys = {r[0] for r in sources}
    result = [item for item in general_items if item["finding_key"] not in source_keys]
    loaded = {}
    for key, batch in sources:
        if batch not in loaded:
            try:
                require_batch_owner(db, owner, batch)
                loaded[batch] = thematic_items(ns, batch, reg)
            except HTTPException as exc:
                if exc.status_code not in (404, 409, 422): raise
                loaded[batch] = []
        item = next((x for x in loaded[batch] if x["finding_key"] == key), None)
        if item:
            result.append({**item, "source_batch": batch,
                "source_card": "/thematic/card/" + batch + "/" + reg})
    return result
