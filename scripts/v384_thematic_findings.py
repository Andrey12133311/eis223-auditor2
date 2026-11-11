"""Source-exact thematic evidence shared by cards and manager submissions."""
from hashlib import sha256
import json
import re
import sqlite3

from fastapi import HTTPException

BATCH = re.compile(r"^[0-9a-f]{24}$")
REG = re.compile(r"^3[0-9]{10}$")


def _identity_key(reg, title, law, evidence, document, url):
    return sha256(json.dumps([reg, title, law, evidence, document, url],
        ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def canonical_item(reg, item):
    from v365_consultant_refs import references
    title, law, evidence, document, url = (
        str(item.get(key) or "").strip()
        for key in ("title", "law", "evidence", "document", "source_url"))
    key = _identity_key(reg, title, law, evidence, document, url)
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
    db = ns["conn"]()
    try:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=1500")
        db.execute("PRAGMA query_only=ON")
        return read_thematic_items(db, batch, reg)
    finally:
        db.close()


def thematic_exact_items(ns, batch, reg, keys=None):
    """Read source-exact keys after the caller has checked the batch owner.

    A submitted source must remain addressable even when public grouping chose
    another current representation of its legal episode. No approval or status
    is copied from one exact source key to another.
    """
    db = ns["conn"]()
    try:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=1500")
        db.execute("PRAGMA query_only=ON")
        return read_thematic_exact_items(db, batch, reg, keys)
    finally:
        db.close()


def _source_groups(db, batch, reg):
    """Filter the shared append-only stores with the existing campaign scope."""
    if not BATCH.fullmatch(batch) or (reg is not None and not REG.fullmatch(reg)):
        raise HTTPException(422, "Некорректный номер проверки или закупки")
    row = db.execute("SELECT categories FROM thematic_batches WHERE id=?", (batch,)).fetchone()
    if row is None or (reg is not None and not db.execute(
            "SELECT 1 FROM thematic_items WHERE batch_id=? AND reg=?", (batch, reg)).fetchone()):
        raise HTTPException(404, "Закупка не найдена в тематической проверке")
    try:
        categories = json.loads(row[0])
    except (TypeError, ValueError) as exc:
        raise HTTPException(409, "Направления тематической проверки повреждены") from exc
    if not isinstance(categories, list) or not all(isinstance(x, str) for x in categories):
        raise HTTPException(409, "Направления тематической проверки повреждены")
    from thematic_proof_variants import raw_rows
    grouped = {}
    for item in raw_rows(db, batch, reg):
        if item["category"] in categories:
            grouped.setdefault(item["reg"], []).append(item)
    return grouped


def _classify_sources(db, batch, number, items):
    """Apply both current overlays to each exact source before any grouping."""
    from payment_finding_reassessment import apply_thematic_reassessments
    try:
        from scripts.v385_msp_reassessment import apply_reassessments
    except ModuleNotFoundError as exc:
        if exc.name != "scripts":raise
        from v385_msp_reassessment import apply_reassessments
    canonical = [canonical_item(number, item) for item in items]
    classified = apply_reassessments(db, batch, number, canonical, same_incident=True)
    # The SME overlay defaults unrelated rules to confirmed. The complete
    # legal journal remains authoritative for the exact current proof key.
    return apply_thematic_reassessments(db, batch, number, classified)


def collapse_exact_claims(items):
    """Choose current authority for one immutable public material claim.

    Internal detector codes are not part of public submission identity. A
    current registered confirmation wins over its old internal-code alias;
    an explicit review wins over an unassessed legacy default. These inputs
    have just received reader-generated overlays, never caller verdict flags.
    Distinct norms, quotes, documents or URLs still have distinct identities.
    """
    def rank(item):
        explicit = any(isinstance(item.get(key), dict) and item[key]
                       for key in ("legal_reassessment", "payment_reassessment", "reassessment"))
        if not explicit:
            return 0
        return 2 if item.get("effective_status") == "confirmed" else 1
    selected = {}
    for item in items:
        reg = str(item.get("reg_number") or item.get("reg") or "")
        fields = [str(item.get(key) or "").strip() for key in
                  ("title", "law", "evidence", "document", "source_url")]
        key = _identity_key(reg, *fields)
        current = selected.get(key)
        if current is None or rank(item) > rank(current):
            selected[key] = {**item, "finding_key": key}
    return list(selected.values())


def read_thematic_items(db, batch, reg=None):
    """Public episode grouping, retaining revised history and current sources."""
    from v352_dashboard_truth import collapse_legal_events
    grouped = _source_groups(db, batch, reg)
    result = []
    for number, raw in grouped.items():
        # A stale legal or SME assessment of source A must not absorb source B
        # before either exact proof has received its current classification.
        classified = collapse_exact_claims(_classify_sources(db, batch, number, raw))
        collapsed = collapse_legal_events(classified)
        # Recompute representation counts and source lists without changing
        # the selected original's immutable public identity.
        classified = collapse_exact_claims(_classify_sources(db, batch, number, collapsed))
        result.extend({**item, "batch_id": batch} for item in classified)
    return result


def read_thematic_exact_items(db, batch, reg, keys=None):
    """Resolve saved submissions/reassessments before public episode collapse."""
    groups = _source_groups(db, batch, reg)
    if keys is not None:
        if (not isinstance(keys, (list, tuple, set, frozenset)) or
                not all(isinstance(key, str) and re.fullmatch(r"[0-9a-f]{64}", key) for key in keys)):
            raise HTTPException(422, "Некорректный идентификатор доказательства")
        keys = set(keys)
    result, seen = [], set()
    for number, raw in groups.items():
        for item in collapse_exact_claims(_classify_sources(db, batch, number, raw)):
            key = item["finding_key"]
            if key in seen or keys is not None and key not in keys:
                continue
            seen.add(key)
            result.append({**item, "batch_id": batch})
    return result


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
                loaded[batch] = thematic_exact_items(ns, batch, reg)
            except HTTPException as exc:
                if exc.status_code not in (404, 409, 422): raise
                loaded[batch] = []
        item = next((x for x in loaded[batch] if x["finding_key"] == key), None)
        if item:
            result.append({**item, "source_batch": batch,
                "source_card": "/thematic/card/" + batch + "/" + reg})
    return result
