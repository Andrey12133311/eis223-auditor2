"""Current registered legal conclusions over immutable saved evidence.

Older audits appended payment findings but never withdrew a conclusion when a
later contract or an applicable alternative changed the evidence.  Keep those
rows and manager decisions intact.  A small append-only assessment journal says
which exact evidence the current legal pass still confirms. Missing,
partial, stale or conflicting assessments require review; they never establish
compliance.  Rendering reads SQL metadata only, never original files.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import sqlite3


REVISION = "registered-legal-evidence-reassessment-2"
# Retain the deployed append-only table and old field aliases. Expanding the
# assessed rule set must not rebuild history or invalidate manager record keys.
TABLE = "payment_finding_assessments"
PAYMENT_CODES = frozenset({
    "1352-28-sme-payment-over-seven-workdays",
    "v337-sme_payment", "v337-sme_payment_7workdays",
    "payment", "payment_without_acceptance", "v336-payment_without_acceptance",
    "v337-contract_payment_delay", "v337-contract_no_deadline",
    "v337-contract_payment_exceptions", "vp-payment-term",
})
_PURCHASE_FIELDS = ("reg_number", "method", "title", "customer", "customer_inn",
                    "published_at", "submission_start", "submission_end", "deadline",
                    "price", "price_num", "price_kind", "card_fields_json")
_DOC_FIELDS = ("id", "reg_number", "name", "url", "doc_type", "published_at",
               "local_path", "status", "metadata_json", "final_url", "text_chars")
_OPERATIONAL = frozenset({"checked_at", "received_at", "captured_at", "updated_at",
                         "extracted_at", "last_checked_at", "downloaded_at",
                         "extraction_ms", "duration_ms", "annotated"})
_MAX_DOCUMENT_ROWS = 2000
_MAX_ASSESSMENT_BYTES = 512 * 1024


def _packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value):
    return hashlib.sha256(_packed(value).encode()).hexdigest()


def is_payment_finding(item):
    return (isinstance(item, dict) and
            str(item.get("code") or item.get("field_path") or "").strip().lower() in PAYMENT_CODES)


def is_reassessable_finding(item):
    """Registered decisions and historical sheet labels need current review.

    Keep unrelated/unregistered findings outside this overlay; a new legal
    classification must not be inferred from a similar title or article.
    """
    if not isinstance(item, dict):
        return False
    from vp_inspection_criteria import BY_KEY
    code = str(item.get("code") or item.get("field_path") or "").strip().lower()
    return code in BY_KEY or _can_confirm_finding(item)


def _can_confirm_finding(item):
    """A retained row label cannot register a substantive rule via a journal."""
    if not isinstance(item, dict):
        return False
    from vp_inspection_criteria import BY_KEY
    code = str(item.get("code") or item.get("field_path") or "").strip().lower()
    if code in BY_KEY:
        return False
    if is_payment_finding(item):
        return True
    from vp_inspection_engine import _rule_known, criterion_keys
    return bool(_rule_known(item) and criterion_keys(item))


def _record(item):
    return {"code": str(item.get("code") or item.get("field_path") or "").strip().lower(),
            "title": str(item.get("title") or item.get("item") or "").strip(),
            **{key: str(item.get(key) or "").strip()
               for key in ("law", "evidence", "document", "source_url")}}


def evidence_key(item):
    """Exact source and quotation, independent of a rewritten display heading.

    A shared URL, a similar title or another clause with the same duration is
    insufficient.  Different rule codes and different source evidence retain
    independent classifications.
    """
    item = _record(item)
    return _digest([item[key] for key in ("code", "evidence", "document", "source_url")])


def initialize(db):
    db.execute("""CREATE TABLE IF NOT EXISTS payment_finding_assessments(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scope TEXT NOT NULL CHECK(scope IN ('general','thematic')),
        batch_id TEXT NOT NULL DEFAULT '',reg TEXT NOT NULL,
        detector_revision TEXT NOT NULL,engine_signature TEXT NOT NULL,
        source_version TEXT NOT NULL,db_token TEXT NOT NULL,
        audit_state TEXT NOT NULL,confirmed_json TEXT NOT NULL,
        reason TEXT NOT NULL,created_at TEXT NOT NULL
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_payment_assessment_scope "
               "ON payment_finding_assessments(scope,batch_id,reg,id)")
    for action in ("UPDATE", "DELETE"):
        db.execute("CREATE TRIGGER IF NOT EXISTS payment_assessment_no_" + action.lower() +
                   " BEFORE " + action + " ON " + TABLE +
                   " BEGIN SELECT RAISE(ABORT,'Payment assessment journal is append-only'); END")


def _exists(db, table):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def _rows(db, sql, args=()):
    cursor = db.execute(sql, args)
    fields = [entry[0] for entry in cursor.description]
    try:
        return [dict(zip(fields, row)) for row in cursor.fetchall()]
    finally:
        cursor.close()


def _stable(value):
    if isinstance(value, dict):
        return {key: _stable(item) for key, item in value.items() if key not in _OPERATIONAL}
    if isinstance(value, list):
        return [_stable(item) for item in value]
    return value


def _source_rows(db, table, key, reg, wanted=None):
    columns = {row[1] for row in db.execute("PRAGMA table_info(" + table + ")")}
    if key not in columns:
        return []
    fields = [name for name in wanted or sorted(columns) if name in columns and name not in _OPERATIONAL]
    values = _rows(db, "SELECT " + ",".join('"'+name+'"' for name in fields) +
                   " FROM " + table + " WHERE " + key + "=?" +
                   (" ORDER BY id" if "id" in columns else "") + " LIMIT ?",
                   (reg, _MAX_DOCUMENT_ROWS+1))
    if len(values) > _MAX_DOCUMENT_ROWS:
        raise ValueError("Source metadata exceeds bounded payment snapshot")
    for row in values:
        for field, value in list(row.items()):
            if isinstance(value, str) and (field.endswith("_json") or field in {"payload", "data"}):
                try:
                    row[field] = _stable(json.loads(value))
                except (TypeError, ValueError):
                    pass
    return sorted(values, key=_packed)


def db_source_token(db, reg):
    """Cheap invalidation token; this is not a replacement for receipt proof."""
    purchases = _source_rows(db, "purchases", "reg_number", reg, _PURCHASE_FIELDS)
    if len(purchases) != 1:
        return ""
    docs = _source_rows(db, "docs", "reg_number", reg, _DOC_FIELDS)
    facts = {table: _source_rows(db, table, "reg", reg)
             for table in ("v341_card_facts", "v338_card_price") if _exists(db, table)}
    return _digest({"purchase": purchases, "documents": docs, "facts": facts})


def prepare_assessment(reg, findings, *, scope="general", batch="", engine_signature,
                       source_version, db_token, state="completed"):
    """Build outside SQLite's writer from this pass's complete output.

    Pass fresh confirmed findings before historical deduplication.  An empty
    list means no saved quotation was reconfirmed, not that the procurement
    complies. A partial/failed pass cannot reconfirm any old row.
    """
    if scope not in {"general", "thematic"} or (scope == "thematic") != bool(batch):
        raise ValueError("Invalid payment assessment scope")
    complete = state == "completed" and bool(engine_signature and source_version and db_token)
    records = []
    if complete:
        seen = set()
        for item in findings:
            if not _can_confirm_finding(item):
                continue
            if item.get("kind", "violation") != "violation" or item.get("status", "violation") != "violation":
                continue
            if item.get("methodology_reasons") or item.get("effective_status", "confirmed") != "confirmed":
                continue
            record = _record(item)
            if not record["evidence"] or not (record["document"] or record["source_url"]):
                continue
            key = evidence_key(record)
            if key not in seen:
                seen.add(key)
                records.append({**record, "evidence_key": key})
    reason = ("Прежний юридический вывод не подтверждён текущей проверкой применимой нормы, "
              "источника и его редакции. Исходное доказательство и решения сохранены."
              if complete else
              "Юридическая проверка не завершена по полному актуальному набору оригиналов. "
              "До завершения проверки прежний вывод не считается подтверждённым.")
    if len(_packed(records).encode()) > _MAX_ASSESSMENT_BYTES:
        records = []
        complete = False
        reason = "Результат юридической проверки превышает допустимый объём. Требуется повторная проверка."
    return {"scope": scope, "batch_id": str(batch), "reg": str(reg),
            "detector_revision": REVISION, "engine_signature": str(engine_signature or ""),
            "source_version": str(source_version or ""), "db_token": str(db_token or ""),
            "audit_state": "completed" if complete else "requires_review",
            "confirmed_json": _packed(records), "reason": reason}


def record_assessment(db, event):
    """Append in the caller's existing guarded transaction; never commit here."""
    if not _exists(db, TABLE):
        initialize(db)
    fields = ("scope", "batch_id", "reg", "detector_revision", "engine_signature",
              "source_version", "db_token", "audit_state", "confirmed_json", "reason")
    previous = _rows(db, "SELECT * FROM " + TABLE +
                     " WHERE scope=? AND batch_id=? AND reg=? ORDER BY id DESC LIMIT 1",
                     (event["scope"], event["batch_id"], event["reg"]))
    if previous and all(previous[0][field] == event[field] for field in fields):
        return previous[0]["id"]
    created = datetime.now(timezone.utc).isoformat()
    cursor = db.execute("INSERT INTO " + TABLE + "(" + ",".join(fields) + ",created_at) VALUES(" +
                        ",".join("?" for _ in range(len(fields)+1)) + ")",
                        tuple(event[field] for field in fields) + (created,))
    return cursor.lastrowid


def _engine():
    from legal_audit_revision import engine_signature, EngineSignatureError
    try:
        return engine_signature()
    except EngineSignatureError:
        return ""


def _thematic_marker(db, batch, reg):
    columns = {row[1] for row in db.execute("PRAGMA table_info(thematic_items)")}
    fields = ("status", "last_semantic_version", "last_engine_signature")
    if not set(fields).issubset(columns):
        return {}
    rows = _rows(db, "SELECT " + ",".join(fields) +
                 " FROM thematic_items WHERE batch_id=? AND reg=?", (batch, reg))
    return rows[0] if len(rows) == 1 else {}


def _assessment(db, scope, batch, reg, current_engine=None):
    reason = ("Сохранённый юридический вывод не подтверждён актуальной проверкой источника, "
              "его редакции и применимости нормы. Требуется повторная юридическая проверка.")
    result = {"id": None, "created_at": None, "effective_status": "requires_review",
              "reason": reason, "stale": True, "confirmed_keys": set()}
    if not _exists(db, TABLE):
        return result
    rows = _rows(db, "SELECT * FROM " + TABLE +
                 " WHERE scope=? AND batch_id=? AND reg=? ORDER BY id DESC LIMIT 1", (scope, batch, reg))
    if not rows:
        return result
    event = rows[0]
    result.update(id=event["id"], created_at=event["created_at"],
                  source_version=event["source_version"], engine_signature=event["engine_signature"],
                  reason=event["reason"])
    engine = _engine() if current_engine is None else current_engine
    if not engine or event["detector_revision"] != REVISION or event["engine_signature"] != engine:
        result["reason"] = "Правила юридической проверки изменились. Сохранённый вывод требует перепроверки."
        return result
    try:
        current = db_source_token(db, reg)
    except (sqlite3.Error, ValueError):
        current = ""
    if not current or not event["db_token"] or current != event["db_token"]:
        result["reason"] = "Источники или сведения закупки изменились после юридической проверки. Требуется повторная проверка."
        return result
    if not event["source_version"] or event["audit_state"] != "completed":
        result["stale"] = False
        return result
    if scope == "thematic":
        marker = _thematic_marker(db, batch, reg)
        if (marker.get("status") != "completed" or
                marker.get("last_semantic_version") != event["source_version"] or
                marker.get("last_engine_signature") != event["engine_signature"]):
            result["reason"] = "Текущая юридическая проверка ещё не завершена или относится к другой версии источников."
            return result
    try:
        if len(event["confirmed_json"].encode()) > _MAX_ASSESSMENT_BYTES:
            raise ValueError("Oversized payment assessment")
        records = json.loads(event["confirmed_json"])
        if not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
            raise ValueError("Invalid payment assessment")
        result["confirmed_keys"] = {evidence_key(item) for item in records if _can_confirm_finding(item)}
        result["stale"] = False
    except (TypeError, ValueError):
        result["reason"] = "Сохранённая юридическая перепроверка недоступна. Требуется повторная проверка."
    return result


def _apply(db, scope, batch, reg, items):
    items = list(items)
    if not any(is_reassessable_finding(item) for item in items):
        return items
    assessment = _assessment(db, scope, batch, reg)
    result = []
    for item in items:
        if not is_reassessable_finding(item):
            result.append(item)
            continue
        confirmed = (_can_confirm_finding(item) and
                     evidence_key(item) in assessment["confirmed_keys"] and not assessment["stale"])
        status = "confirmed" if confirmed else "requires_review"
        # A different applicable overlay may already have rejected this row.
        if item.get("effective_status", "confirmed") != "confirmed":
            status = item["effective_status"]
        event = {key: value for key, value in assessment.items() if key != "confirmed_keys"}
        event.update(effective_status=status,
                     reason=("Вывод подтверждён текущей проверкой применимой нормы и исходного документа."
                             if status == "confirmed" else assessment["reason"]))
        warnings, gaps = list(item.get("warnings") or []), list(item.get("presentation_gaps") or [])
        if status != "confirmed":
            warnings.append(event["reason"])
            gaps.append(event["reason"])
        result.append({**item, "effective_status": status, "legal_reassessment": event,
                       "payment_reassessment": event,
                       "warnings": list(dict.fromkeys(warnings)),
                       "presentation_gaps": list(dict.fromkeys(gaps))})
    return result


def apply_general_reassessments(db, reg, items):
    return _apply(db, "general", "", reg, items)


def apply_thematic_reassessments(db, batch, reg, items):
    return _apply(db, "thematic", batch, reg, items)


def revision(db, batch):
    """Saved-view cache stamp, including SQL source edits without added rows."""
    regs = sorted({row[0] for row in db.execute("SELECT DISTINCT reg,code FROM thematic_findings "
            "WHERE batch_id=? AND code IS NOT NULL", (batch,))
            if is_reassessable_finding({"code": row[1]})})
    if not regs:
        return (0, "")
    latest = db.execute("SELECT COALESCE(MAX(id),0) FROM " + TABLE +
                       " WHERE scope='thematic' AND batch_id=?", (batch,)).fetchone()[0] if _exists(db, TABLE) else 0
    tokens = []
    for reg in regs:
        try:
            token = db_source_token(db, reg)
        except (sqlite3.Error, ValueError):
            token = ""
        tokens.append((reg, token, _thematic_marker(db, batch, reg)))
    return (int(latest), _digest([REVISION, _engine(), tokens]))
