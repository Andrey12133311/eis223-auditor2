"""Explicit owner-requested reassessment of one thematic SME security finding.

Original findings, submissions and manager decisions remain immutable here.
The append-only journal records the effective classification separately. No
queue is started and no general-audit result is rewritten.
"""
import asyncio
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
import sqlite3

from fastapi import APIRouter, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

router = APIRouter(tags=["thematic-msp-reassessment"])
ELIGIBLE_CODE = "223-SME-BID-SECURITY-OVER-TWO-PERCENT"
REVISION = "v385-msp-scope-1"
TABLE = "thematic_finding_reassessments"
ACTIVE = "confirmed"


def scope_display(items):
    """Current thematic reassessment overrides only presentation, not metadata."""
    events = [item["reassessment"] for item in items if item.get("reassessment")]
    if not events:
        return None
    event = max(events, key=lambda value: value["id"])
    if event.get("stale"):
        return "Требуется повторная проверка: источники ЕИС изменились"
    return {"yes": "Да, только МСП (по перепроверке)",
            "no": "Нет, участие не ограничено МСП (по перепроверке)",
            "unknown": "Не установлено по официальным сведениям (по перепроверке)",
            "conflict": "Противоречивые сведения — требуется проверка"}.get(
                event["scope"]["state"], "Требуется проверка")


# Evidence readers also run in installations without the optional accounts
# package. Load account dependencies only for the authenticated write route.
def connect():
    from scripts.account_api_v1 import connect as account_connect
    return account_connect()


def current_account(request):
    from scripts.account_api_v1 import current_account as account
    return account(request)


def require_origin(request):
    from scripts.account_api_v1 import require_origin as check
    return check(request)


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return sha256(packed(value).encode()).hexdigest()


def initialize(db):
    db.execute("""CREATE TABLE IF NOT EXISTS thematic_finding_reassessments(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        actor_id TEXT NOT NULL,batch_id TEXT NOT NULL,reg TEXT NOT NULL,
        finding_key TEXT NOT NULL,effective_status TEXT NOT NULL
          CHECK(effective_status IN ('confirmed','superseded','requires_review')),
        source_fingerprint TEXT NOT NULL,source_token TEXT NOT NULL DEFAULT '',detector_revision TEXT NOT NULL,
        finding_snapshot TEXT NOT NULL,scope_json TEXT NOT NULL,
        sources_json TEXT NOT NULL,reason TEXT NOT NULL,created_at TEXT NOT NULL
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_msp_reassessment_finding "
               "ON thematic_finding_reassessments(batch_id,reg,finding_key,id)")
    if "source_token" not in {row[1] for row in db.execute("PRAGMA table_info(" + TABLE + ")")}:
        db.execute("ALTER TABLE " + TABLE + " ADD COLUMN source_token TEXT NOT NULL DEFAULT ''")
    for action in ("UPDATE", "DELETE"):
        db.execute("CREATE TRIGGER IF NOT EXISTS msp_reassessment_no_" + action.lower() +
                   " BEFORE " + action + " ON " + TABLE +
                   " BEGIN SELECT RAISE(ABORT,'MSP reassessment journal is append-only'); END")


def exists(db):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                           (TABLE,)).fetchone())


def public_event(row):
    return {key: row[key] for key in (
        "id", "actor_id", "created_at", "effective_status", "source_fingerprint", "reason")
    } | {"scope": json.loads(row["scope_json"])}


def apply_reassessments(db, batch, reg, items, *, same_incident=False):
    """Annotate exact canonical keys; never drop submitted historical evidence."""
    latest = {}
    snapshots = {}
    current = None
    if exists(db):
        for row in db.execute("SELECT a.* FROM thematic_finding_reassessments a JOIN "
                              "(SELECT MAX(id) AS id FROM thematic_finding_reassessments "
                              "WHERE batch_id=? AND reg=? GROUP BY finding_key) current "
                              "ON current.id=a.id ORDER BY a.id", (batch, reg)):
            event = public_event(row)
            if row["source_token"]:
                if current is None:
                    current = current_source_token(db, reg)
                if row["source_token"] != current:
                    event.update(effective_status="requires_review", stale=True,
                        original_effective_status=row["effective_status"],
                        reason="После перепроверки источники ЕИС изменились или стали недоступны. Требуется повторная проверка применимости правил МСП; первоначальный вывод и прежние решения сохранены.")
            latest[row["finding_key"]] = event
            snapshots[row["finding_key"]] = json.loads(row["finding_snapshot"])
    result = []
    for item in items:
        event = latest.get(item["finding_key"])
        if event is None and same_incident and item.get("code") == ELIGIBLE_CODE:
            # A bounded staff list can omit the best/oldest duplicate source.
            # Reuse only a proven SAME incident under the existing deduper,
            # exact rule, batch and procurement; never match by category alone.
            from v352_dashboard_truth import _common_event
            equivalent = [latest[key] for key, old in snapshots.items()
                          if old.get("code") == ELIGIBLE_CODE and _common_event(old, item)]
            if equivalent:
                event = max(equivalent, key=lambda value: value["id"])
        status = event["effective_status"] if event else ACTIVE
        warnings = list(item.get("warnings") or [])
        if event and status != ACTIVE:
            warnings.append(event["reason"])
        result.append({**item, "effective_status": status,
                       "reassessment": event, "warnings": warnings})
    return result


def revision(db, batch):
    if not exists(db):
        return (0, "")
    rows = db.execute("SELECT reg,MAX(id) FROM thematic_finding_reassessments "
                      "WHERE batch_id=? GROUP BY reg", (batch,)).fetchall()
    return (max((int(row[1]) for row in rows), default=0),
            digest([(row[0], current_source_token(db, row[0])) for row in rows]))


def current_source_token(db, reg):
    try:
        return source_token(db, reg)
    except (sqlite3.Error, HTTPException):
        return "unavailable"


def source_token(db, reg):
    """Detect metadata/original-record changes while slow bytes are extracted."""
    from audit_source_version import source_version
    db.row_factory = sqlite3.Row
    purchase = db.execute("SELECT * FROM purchases WHERE reg_number=?", (reg,)).fetchone()
    if purchase is None:
        raise HTTPException(404, "Закупка не найдена")
    p = dict(purchase)
    fields = ("reg_number", "method", "price", "price_num", "card_fields_json")
    doc_fields = ("id", "name", "url", "doc_type", "published_at", "local_path", "status", "metadata_json", "final_url")
    docs = [{key: dict(row).get(key) for key in doc_fields} for row in db.execute(
        "SELECT * FROM docs WHERE reg_number=? ORDER BY id", (reg,))]
    snapshots = []
    for table in ("v341_card_facts", "v338_card_price"):
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            snapshots.append([table, [dict(row) for row in db.execute(
                "SELECT * FROM " + table + " WHERE reg=?", (reg,))]])
    return digest({"purchase": {key: p.get(key) for key in fields},
                   "documents": docs, "card_snapshots": snapshots,
                   "stored_originals": source_version(db, reg)})


def read_source_token(ns, reg):
    db = ns["conn"]()
    try:
        return source_token(db, reg)
    finally:
        db.close()


async def collect_sources(ns, reg):
    """Read receipt-verified local originals and current official card facts only."""
    from v332_all_procurements_audit import existing, as_procurement, stored_docs, merge_docs
    from v298_eis_only import offline_audit
    from v341_eis_facts import enrich
    from v331_broad_legal import verified
    before = await asyncio.to_thread(read_source_token, ns, reg)
    old, rows = await asyncio.to_thread(existing, ns, reg)
    purchase = as_procurement(old)
    saved, unreadable = await stored_docs(ns, reg, rows)
    originals = []
    problems = []
    try:
        result = await offline_audit(ns, reg, collect_only=True)
        originals = result.get("docs") or []
    except (ValueError, OSError) as exc:
        # Missing official archive/manifest is a limitation, not a false flag.
        problems.append(type(exc).__name__)
    docs = [doc for doc in merge_docs(originals, saved) if verified(doc)]
    await asyncio.to_thread(enrich, ns, purchase, docs)
    after = await asyncio.to_thread(read_source_token, ns, reg)
    if before != after:
        raise HTTPException(409, "Сведения ЕИС изменились во время проверки. Повторите проверку.")
    purchase["_msp_source_token"] = after
    purchase["_msp_source_limits"] = {"unreadable_documents": unreadable,
        "verified_documents": len(docs), "archive_read_problems": problems}
    return purchase, docs


def _target(db, batch, reg, key):
    from scripts.v384_thematic_findings import read_thematic_items
    item = next((item for item in read_thematic_items(db, batch, reg)
                 if item["finding_key"] == key), None)
    if item is None:
        raise HTTPException(409, "Доказательство изменилось; обновите тематическую карточку")
    if item.get("code") != ELIGIBLE_CODE:
        raise HTTPException(422, "Эта проверка доступна только для вывода о пределе 2% в закупке МСП")
    return item


def record_reassessment(ns, actor, batch, reg, key, purchase, docs):
    from v383_msp_scope import resolve
    from v331_broad_legal import verified
    from scripts.v384_thematic_findings import BATCH, REG
    if not BATCH.fullmatch(batch) or not REG.fullmatch(reg) or not re.fullmatch(r"[0-9a-f]{64}", key):
        raise HTTPException(422, "Некорректный номер проверки, закупки или доказательства")
    if str(purchase.get("reg") or purchase.get("reg_number") or "") != reg:
        raise HTTPException(409, "Источники относятся к другой закупке")
    verified_docs = [doc for doc in docs if isinstance(doc, dict) and verified(doc)]
    scope = resolve(purchase, verified_docs)
    status = "superseded" if scope["value"] is False else ACTIVE if scope["value"] is True else "requires_review"
    reasons = {
        "superseded": "Официальные сведения не ограничивают участие только МСП. Вывод о специальном пределе 2% снят; первоначальное доказательство сохранено.",
        ACTIVE: "Участие только МСП подтверждено. Первоначальный вывод не изменён; остальные условия нарушения здесь не переоцениваются.",
        "requires_review": "Принадлежность закупки к закупкам только МСП не подтверждена или источники противоречат друг другу. Вывод о пределе 2% требует проверки и не учитывается как подтверждённое нарушение.",
    }
    sources = {"documents": sorted([{"name": doc.get("name"),
        "source_url": doc["meta"].get("source_url"), "sha256": doc["meta"].get("sha256")}
        for doc in verified_docs], key=packed), "limitations": purchase.get("_msp_source_limits", {})}
    fingerprint = digest({"scope": scope, "sources": sources,
                          "source_token": purchase.get("_msp_source_token"), "revision": REVISION})
    db = ns["conn"]()
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA busy_timeout=2000")
        initialize(db)
        db.execute("BEGIN IMMEDIATE")
        item = _target(db, batch, reg, key)
        if purchase.get("_msp_source_token") and source_token(db, reg) != purchase["_msp_source_token"]:
            raise HTTPException(409, "Источники ЕИС изменились. Повторите проверку.")
        previous = db.execute("SELECT * FROM thematic_finding_reassessments "
            "WHERE batch_id=? AND reg=? AND finding_key=? ORDER BY id DESC LIMIT 1",
            (batch, reg, key)).fetchone()
        if previous and previous["source_fingerprint"] == fingerprint and previous["effective_status"] == status:
            db.commit()
            return {"effective_status": status, "already_reassessed": True,
                    "reassessment": public_event(previous)}
        # Keep original identity/evidence independent of all presentation flags.
        snapshot = {k: v for k, v in item.items() if k not in ("effective_status", "reassessment")}
        created = datetime.now(timezone.utc).isoformat()
        cursor = db.execute("INSERT INTO thematic_finding_reassessments "
            "(actor_id,batch_id,reg,finding_key,effective_status,source_fingerprint,source_token,detector_revision,"
            "finding_snapshot,scope_json,sources_json,reason,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (str(actor), batch, reg, key, status, fingerprint, purchase.get("_msp_source_token") or "", REVISION,
             packed(snapshot), packed(scope), packed(sources), reasons[status], created))
        event = db.execute("SELECT * FROM thematic_finding_reassessments WHERE id=?", (cursor.lastrowid,)).fetchone()
        db.commit()
        return {"effective_status": status, "already_reassessed": False,
                "reassessment": public_event(event)}
    except Exception:
        if db.in_transaction:
            db.rollback()
        raise
    finally:
        db.close()


def _engine(request):
    ns = getattr(request.app.state, "eis223_manual_ns", None)
    if ns is None:
        raise HTTPException(503, "База результатов ЕИС временно недоступна")
    return ns


def authorize_target(request, batch, reg, key):
    from scripts.v384_thematic_findings import require_batch_owner, thematic_items
    owner = current_account(request)[0]
    with connect() as db:
        require_batch_owner(db, owner, batch)
    item = next((item for item in thematic_items(_engine(request), batch, reg)
                 if item["finding_key"] == key), None)
    if item is None:
        raise HTTPException(409, "Доказательство изменилось; обновите тематическую карточку")
    if item.get("code") != ELIGIBLE_CODE:
        raise HTTPException(422, "Это доказательство не относится к пределу 2% для МСП")
    return owner


@router.post("/me/thematic/{batch}/{reg}/findings/{finding_key}/msp-recheck")
async def recheck_msp(batch: str, reg: str, finding_key: str, request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    require_origin(request)
    try:
        owner = await run_in_threadpool(authorize_target, request, batch, reg, finding_key)
        ns = _engine(request)
        purchase, docs = await collect_sources(ns, reg)

        def finish():
            from scripts.v384_thematic_findings import require_batch_owner
            from scripts.v368_manager_workflow import _audit
            with connect() as db:
                # Same employee lock as submission/decision: no new approval
                # can race past a reassessment that invalidates its premise.
                db.execute("SELECT id FROM account_users WHERE id=%s FOR UPDATE", (owner,)).fetchone()
                require_batch_owner(db, owner, batch)
                result = record_reassessment(ns, owner, batch, reg, finding_key, purchase, docs)
                event_id = batch + ":" + str(result["reassessment"]["id"])
                # Retrying after a cross-database commit failure repairs the
                # account journal without repeating the evidence event.
                if not db.execute("SELECT 1 FROM account_manager_actions WHERE actor_id=%s "
                    "AND entity_type='msp_reassessment' AND entity_id=%s AND action='reassess'",
                    (owner, event_id)).fetchone():
                    _audit(db, owner, owner, "msp_reassessment", event_id, "reassess",
                           reg + ": " + result["reassessment"]["reason"])
                return result
        return await run_in_threadpool(finish)
    except HTTPException:
        raise
    except Exception as exc:
        # Collection/storage/account failures cannot become a guessed "no".
        # The durable local journal may have committed before the account
        # journal; an explicit retry repairs that case idempotently.
        print("V385 MSP_REASSESSMENT_ERROR reg=" + reg + " type=" + type(exc).__name__, flush=True)
        raise HTTPException(503, "Не удалось полностью завершить перепроверку. Исходные доказательства сохранены; обновите карточку или повторите попытку.") from exc
