"""Labelled facts from the actual EIS card and verified original documents.

Card snapshots are authoritative for display. Price meanings remain distinct;
old amounts are retained in the card history. Missing facts never imply a
legal infringement. No remote EIS requests are performed in this module.
"""
from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from datetime import date, datetime, timezone

from fastapi import HTTPException, Request
from v295_eis_originals import auth
from v311_verified_customer import REG, _official_source
from v331_broad_legal import verified
from v335_price_recovery import money
from v314_eis_metadata import table_pairs
from v330_amendments_223 import day

LABELS = {
    "nmcd": "НМЦД",
    "maximum_contract_value": "Максимальное значение цены договора",
    "subject_cost": "Стоимость предмета закупки",
    "contract_price": "Цена договора",
    "lot_price": "Цена лота",
    "total_unit_prices": "Начальная сумма цен единиц",
    "unit_price": "Цена единицы",
}
PATTERNS = {
    "nmcd": r"(?:начальная\s*\(\s*максимальная\s*\)\s*цена(?:\s+договора|\s+контракта)|начальная\s+максимальная\s+цена(?:\s+договора|\s+контракта)|начальная\s+цена\s+(?:договора|контракта)|нмц[дк])(?:\s*\(цена\s+лота\))?",
    "maximum_contract_value": r"максимальн\w*\s+(?:значени\w*\s+)?цен\w*\s+договора",
    "subject_cost": r"(?:стоимост\w*\s+(?:предмет\w*\s+)?закупк\w*|стоимост\w*\s+предмет\w*\s+договора|сумма\s+закупк\w*)",
    "contract_price": r"(?:(?:общая\s+)?(?:цена|стоимость|сумма)\s+договора|цена\s+контракта)",
    "lot_price": r"(?:цена|стоимость)\s+лота",
    "total_unit_prices": r"(?:начальная\s*(?:\(\s*максимальная\s*\))?\s*)?сумма\s+цен\s+(?:всех\s+)?единиц(?:\s+(?:товар(?:а|ов)?|работ|услуг)(?:[,\s]+(?:работ|услуг))*)?",
    "unit_price": r"(?:начальная\s*(?:\(\s*максимальная\s*\))?\s*)?(?:цена|стоимость)\s+(?:за\s+единицу|единицы)(?:\s+(?:товара|работы|услуги)(?:[,\s]+(?:работы|услуги))*)?",
}
KINDS = {key: re.compile("^(?:"+pattern+")$", re.I) for key, pattern in PATTERNS.items()}
PRIORITY = tuple(LABELS)
DATE_LABELS = {
    "submission_start": r"(?:дата\s+(?:и\s+время\s+)?начала\s+(?:срока\s+)?(?:подачи|при[её]ма|приема)\s+заявок|начало\s+(?:срока\s+)?(?:подачи|при[её]ма|приема)\s+заявок)",
    "submission_end": r"(?:дата\s+(?:и\s+время\s+)?окончания\s+(?:срока\s+)?(?:подачи|при[её]ма)\s+заявок|окончание\s+(?:срока\s+)?(?:подачи|при[её]ма)\s+заявок|срок\s+окончания\s+подачи\s+заявок|(?:дата\s+)?окончания\s+приема\s+заявок|(?:крайний\s+)?срок\s+подачи\s+заявок|при[её]м\s+заявок\s+до)",
    "published": r"(?:дата\s+(?:и\s+время\s+)?(?:первоначального\s+)?(?:размещения|публикации)\s+извещения(?:\s+в\s+ЕИС)?|дата\s+публикации\s+закупки)",
}
MONTHS = {"января":1,"февраля":2,"марта":3,"апреля":4,"мая":5,"июня":6,
          "июля":7,"августа":8,"сентября":9,"октября":10,"ноября":11,"декабря":12}
DATE_TOKEN = (r"(?:20\d{2}-\d{2}-\d{2}|\d{1,2}[.]\d{1,2}[.]20\d{2}|"
              r"[«\"]?\d{1,2}[»\"]?\s+(?:"+"|".join(MONTHS)+r")\s+20\d{2})")
METHOD = re.compile(r"(?:конкурс|аукцион|запрос\s+котировок|запрос\s+предложений|закупка\s+у\s+единственного\s+поставщика)", re.I)
NOTICE = re.compile(r"извещени|документаци|информационн\w*\s+карт", re.I)


def now():
    return datetime.now(timezone.utc).isoformat()


def compact(value):
    return " ".join(str(value or "").replace("\xa0", " ").split()).strip(" :：;|\t")


def normalize_label(value):
    return re.sub(r"\s*\((?:руб\.?|рублей|RUB|с\s+НДС|без\s+НДС)\)\s*$", "", compact(value), flags=re.I)


def date_from_labelled_value(value):
    """Find an explicit date, never a duration or unrelated document date."""
    match = re.search(DATE_TOKEN, str(value or ""), re.I)
    return parse_day(match.group(0)) if match else None


def parse_day(value):
    parsed = day(value)
    if parsed:
        return parsed
    m = re.search(r"[«\"]?(\d{1,2})[»\"]?\s+("+"|".join(MONTHS)+r")\s+(20\d{2})", str(value or ""), re.I)
    if not m:
        return None
    try:
        return date(int(m[3]), MONTHS[m[2].casefold()], int(m[1]))
    except ValueError:
        return None


def validate_prices(items):
    if not isinstance(items, list) or len(items) > 50:
        raise ValueError("Invalid price list")
    output = []
    seen = set()
    for raw in items:
        if not isinstance(raw, dict):
            raise ValueError("Invalid monetary field")
        kind = str(raw.get("price_kind") or "")
        label = normalize_label(raw.get("price_label"))
        parsed = money(raw.get("price"))
        if kind not in KINDS or not KINDS[kind].fullmatch(label) or not parsed:
            raise ValueError("Unconfirmed price meaning or amount")
        if raw.get("currency", "RUB") != "RUB":
            raise ValueError("A foreign currency cannot be stored as roubles")
        key = (kind, parsed[0])
        if key in seen:
            continue
        seen.add(key)
        output.append({"price": parsed[0], "price_num": parsed[1], "price_kind": kind,
                       "price_label": label, "currency": "RUB"})
    return output


def saved_price_kind(card,reg):
    """Preserve saved meaning, rechecking old browser labels before NMCD use."""
    if not isinstance(card,dict):return None
    summary=card.get("summary") or {}
    kind=summary.get("price_kind") if isinstance(summary,dict) else None
    evidence=card.get("price_evidence") or {}
    if (isinstance(evidence,dict) and evidence.get("source")=="official_eis_card" and
            _official_source(evidence.get("source_url") or evidence.get("evidence_page"),reg)):
        evidence_kind=evidence.get("price_kind")
        if evidence_kind=="nmcd" and not KINDS["nmcd"].fullmatch(normalize_label(evidence.get("price_label"))):
            return "unknown"
        if kind and evidence_kind and kind!=evidence_kind:return "unknown"
        kind=kind or evidence_kind
    return kind


def chosen_price(prices):
    # A malformed/generic saved sibling remains an unresolved alternative.
    # Do not erase a former conflict by filtering it out of known types.
    if any(x.get("price_kind")=="unknown" for x in prices):
        return {"price":"","price_num":None,"price_kind":"unknown"}
    for kind in PRIORITY:
        candidates = [x for x in prices if x["price_kind"] == kind]
        if candidates:
            # Different lot values cannot become one fabricated total.
            return candidates[0] if len({x["price"] for x in candidates}) == 1 else None
    return None


def initialize(ns):
    db = ns["conn"]()
    try:
        db.execute("""CREATE TABLE IF NOT EXISTS v341_card_facts(
            reg TEXT PRIMARY KEY,source_page TEXT NOT NULL,
            captured_at TEXT NOT NULL,payload TEXT NOT NULL)""")
        db.commit()
    finally:
        db.close()


def read_card(ns, reg):
    db = ns["conn"]()
    try:
        row = db.execute("SELECT payload FROM v341_card_facts WHERE reg=?", (reg,)).fetchone()
        result = json.loads(row[0]) if row else {}
        if not isinstance(result,dict) or not _official_source(result.get("source_page"),reg):return {}
        normalized=[]
        for item in result.get("prices") or []:
            try:normalized.extend(validate_prices([item]))
            except (ValueError,TypeError,AttributeError):
                parsed=money(item.get("price")) if isinstance(item,dict) else None
                normalized.append({"price":parsed[0] if parsed else "","price_num":parsed[1] if parsed else None,
                                   "price_kind":"unknown","price_label":"Вид цены не подтверждён"})
        result["prices"]=normalized
        return result
    except (sqlite3.OperationalError, ValueError, TypeError):
        return {}
    finally:
        db.close()


def document_facts(docs):
    """Only unique, explicitly labelled facts in receipt-verified notice texts."""
    candidates = {}
    for doc in docs[:650]:
        if not verified(doc) or not NOTICE.search(str(doc.get("name") or "")):
            continue
        text = str(doc.get("text") or "")[:750000]
        pairs = table_pairs(text)
        # DOCX paragraphs often place the value on the following line.
        lines = text.splitlines()
        for i, line in enumerate(lines):
            m = re.match(r"^\s*(.{3,150}?)\s*[:：]\s*(.*)$", line)
            if m:
                pairs.append([compact(m[1]), compact(m[2]) or compact(lines[i+1] if i+1 < len(lines) else ""), line])
            elif i+1 < len(lines):
                pairs.append([compact(line), compact(lines[i+1]), line+" "+lines[i+1]])
        # Dates must be explicitly labelled; never infer from unrelated dates.
        for i, line in enumerate(lines):
            if not re.search(r"срок(?:и|а)?\s+(?:подачи|при[её]ма)\s+заявок", line, re.I):
                continue
            segment = " ".join(lines[i:i+5])[:900]
            for key, marker in (("submission_start", r"(?:начал[оа]|с)\s*(?:подачи|при[её]ма)?"),
                                ("submission_end", r"(?:окончани[ея]|до)\s*(?:подачи|при[её]ма)?")):
                match = re.search(marker+r"\s*[:：–—-]?\s*(?P<date>"+DATE_TOKEN+r")", segment, re.I)
                if not match:
                    continue
                parsed_date = parse_day(match["date"])
                if parsed_date:
                    candidates.setdefault(key, {})[parsed_date.isoformat()] = {
                        "value": parsed_date.isoformat(), "document": doc.get("name"),
                        "source_url": (doc.get("meta") or {}).get("source_url"),
                        "evidence": compact(match[0])[:650], "source": "official_eis_document"}
        for label, value, excerpt in pairs:
            key = None
            parsed = None
            for field, pattern in DATE_LABELS.items():
                if re.fullmatch(pattern, label, re.I):
                    match = re.search(DATE_TOKEN, value)
                    date_value = parse_day(match[0]) if match else None
                    if date_value:
                        key, parsed = field, date_value.isoformat()
                    break
            if re.fullmatch(r"(?:наименование\s+способа\s+закупки|способ(?:\s+осуществления|\s+проведения|\s+размещения)?\s+закупки|способ\s+и\s+форма\s+проведения\s+закупки|форма\s+торгов)", label, re.I):
                if METHOD.search(value) and len(value) <= 220:
                    key, parsed = "method", compact(value)
            if key and parsed is not None:
                candidates.setdefault(key, {})[str(parsed)] = {
                    "value": parsed, "document": doc.get("name"),
                    "source_url": (doc.get("meta") or {}).get("source_url"),
                    "evidence": compact(excerpt)[:650], "source": "official_eis_document"}
        # Information cards often combine beginning/end in a single table
        # cell and use Russian month names. Extract only an explicit dated
        # subfield; do not replace a deadline with a narrative duration.
        for key, pattern in DATE_LABELS.items():
            suffix = (r"(?:\s+на\s+участие\s+в\s+[а-яё\s-]{1,110})?"
                      r"(?:\s*\([^)]{0,80}\))?\s*[:：|=–—\-\s]{0,15}")
            for m in list(re.finditer(pattern+suffix+r"(?P<date>"+DATE_TOKEN+r")", text, re.I))[:30]:
                parsed = parse_day(m["date"])
                if parsed:
                    candidates.setdefault(key, {})[parsed.isoformat()] = {
                        "value": parsed.isoformat(), "document": doc.get("name"),
                        "source_url": (doc.get("meta") or {}).get("source_url"),
                        "evidence": compact(m[0])[:650], "source": "official_eis_document"}
    result = {key: next(iter(values.values())) for key, values in candidates.items() if len(values) == 1}
    from v383_msp_scope import resolve
    scope = resolve({}, docs)
    if isinstance(scope["value"],bool):
        result["sme_only"] = scope["evidence"][0]
    return result


def card_predates_notice(captured_at, record):
    """Only two explicit, timezone-aware instants establish source order.

    Date-only or timezone-less values cannot prove an intra-day ordering.
    A later capture is not proof that a different legal revision is current.
    """
    def instant(value):
        raw=str(value or "")
        if not re.search(r"[T ]\d{2}:\d{2}",raw):return None
        try:
            value=datetime.fromisoformat(raw.replace("Z","+00:00"))
            return value if value.utcoffset() is not None else None
        except (ValueError,TypeError,OverflowError):return None
    capture=instant(captured_at)
    # Publication establishes that this exact verified notice was available.
    # A later XML modification time alone does not prove an earlier field was
    # legally superseded, so use it only if publication is absent.
    notice=instant(record.get("publication") or record.get("modified"))
    return bool(capture is not None and notice is not None and capture<notice)


def reconcile_notice_context(purchase, direct):
    """Keep current SOAP facts; conflicting unordered card versions are review."""
    context=purchase.get("_current_soap_context")
    if not isinstance(context,dict):return
    record=context.get("record") or {}
    if (record.get("reg_number")!=str(purchase.get("reg") or "") or
        record.get("xml_family")!="notice" or
        not re.fullmatch(r"[0-9a-f]{64}",str(record.get("xml_sha256") or ""))):return
    proof=purchase.setdefault("fact_evidence",{})
    conflicts={}
    fields=direct.get("fields") or {}
    older=card_predates_notice(direct.get("captured_at"),record)
    def evidence(value):
        return {"value":value,"source":"official_eis_xml",
                "source_url":purchase.get("url") or "","document":record.get("xml_filename"),
                "xml_sha256":record["xml_sha256"],"archive_sha256":record.get("archive_sha256"),
                "version":record.get("version"),"evidence":str(value)}
    def equal(key, left, right):
        if key in ("published","submission_start","submission_end"):
            return parse_day(left) is not None and parse_day(left)==parse_day(right)
        return compact(left).casefold()==compact(right).casefold()
    for key,value in (context.get("fields") or {}).items():
        if key not in ("method","published","submission_start","submission_end"):continue
        card_value=fields.get(key)
        if card_value not in (None,"") and not equal(key,value,card_value) and not older:
            conflicts[key]={"source":"official_eis_context_conflict","value":None,
                "evidence":"Не установлена действующая редакция противоречащих сведений ЕИС",
                "alternatives":[evidence(value),{"value":card_value,"source":"official_eis_card",
                    "source_url":direct.get("field_source_page",direct.get("source_page")),
                    "captured_at":direct.get("captured_at")}]}
            purchase[key]=None
            proof[key]=conflicts[key]
        else:
            purchase[key]=value
            proof[key]=evidence(value)
    xml_price=context.get("price") or {}
    selected=chosen_price(direct.get("prices") or [])
    if xml_price:
        xml_kind=xml_price.get("price_kind") or "unknown"
        same=bool(selected and xml_kind!="unknown" and selected.get("price_kind")==xml_kind and
                  money(xml_price.get("price"))==money(selected.get("price")))
        price_older=card_predates_notice(direct.get("price_captured_at",direct.get("captured_at")),record)
        if selected and not same and not price_older:
            conflicts["price"]={"source":"official_eis_context_conflict","value":None,
                "evidence":"Не установлена действующая редакция или вид противоречащей цены ЕИС",
                "alternatives":[{**evidence(xml_price.get("price")),"price_kind":xml_kind},
                    {"value":selected.get("price"),"source":"official_eis_card",
                     "price_kind":selected.get("price_kind"),
                     "source_url":direct.get("source_page"),
                     "captured_at":direct.get("price_captured_at",direct.get("captured_at"))}]}
            purchase.update(price="",price_num=None,price_kind="unknown")
            purchase.pop("verified_price_source",None)
            proof["price"]=conflicts["price"]
        else:
            purchase.update(price=xml_price.get("price") or "",price_num=xml_price.get("price_num"),
                            price_kind=xml_kind)
            if not same:purchase.pop("verified_price_source",None)
            proof["price"]={**evidence(xml_price.get("price")),"price_kind":xml_kind}
    purchase["_official_context_conflicts"]=conflicts
    # Missing publication context must not activate modern competitive rules;
    # a known method cannot resolve an unordered historical-edition conflict.
    if "method" in conflicts or "published" in conflicts:
        purchase["method"]=""
        purchase.pop("purchase_method",None)
    purchase["deadline"]=purchase.get("submission_end") or ""


def enrich(ns, purchase, docs, card=None, detail=None):
    reg = str(purchase.get("reg") or purchase.get("reg_number") or "")
    proof = document_facts(docs)
    direct = read_card(ns, reg)
    for key, value in direct.get("fields", {}).items():
        proof[key] = {"value": value, "document": "Карточка ЕИС",
                      "source_url": direct["source_page"], "source": "official_eis_card",
                      "evidence": str(direct.get("field_evidence", {}).get(key) or value)[:650],
                      "captured_at": direct.get("captured_at")}
    # Read legacy conflicts even when a newer-format snapshot has prices.
    # v338 retains its first capture time when recording an alternative, so
    # that timestamp cannot prove the unresolved conflict predates SOAP.
    db = ns["conn"](); db.row_factory = sqlite3.Row
    try:
        row = db.execute("SELECT * FROM v338_card_price WHERE reg=?", (reg,)).fetchone()
        if row and _official_source(row["source_page"], reg):
            if row["conflict"]:
                direct={**direct,"source_page":row["source_page"],
                        "field_source_page":direct.get("source_page"),"price_captured_at":"",
                        "price_conflict":True,
                        "prices":[{"price":"","price_num":None,"price_kind":"unknown"}]}
            elif not direct.get("prices"):
                direct = {**direct, "source_page": row["source_page"],
                          "field_source_page": direct.get("source_page"),
                          "price_captured_at": row["captured_at"],
                          "prices": [{"price": row["price"], "price_num": row["price_num"],
                                      "price_kind": row["price_kind"] if row["price_kind"] in KINDS and
                                          KINDS[row["price_kind"]].fullmatch(normalize_label(row["price_label"])) else "unknown",
                                      "price_label": row["price_label"]}]}
    except sqlite3.OperationalError:
        pass
    finally:
        db.close()
    # Never replace a saved authoritative card flag with document inference.
    # Resolve the current document separately so disagreement remains visible.
    existing = (purchase.get("fact_evidence") or {}).get("sme_only") or {}
    if existing.get("source") == "official_eis_card" and (proof.get("sme_only") or {}).get("source") != "official_eis_card":
        proof.pop("sme_only", None)
    purchase["fact_evidence"] = {**(purchase.get("fact_evidence") or {}), **proof}
    for key, item in proof.items():
        if not purchase.get(key) or item["source"] == "official_eis_card":
            purchase[key] = item["value"]
    from v383_msp_scope import resolve, saved_card
    # Preserve current structured source plus contradictory document evidence.
    if card is not None:
        purchase["card_fields_json"] = {**saved_card(purchase), **card}
    scope = resolve(purchase, docs)
    purchase["sme_scope"] = scope["state"]
    purchase["sme_only"] = scope["value"]
    purchase["fact_evidence"]["sme_scope"] = scope
    if purchase.get("submission_end"):
        purchase["deadline"] = purchase["submission_end"]
    selected = chosen_price(direct.get("prices") or [])
    if selected:
        purchase.update(price=selected["price"], price_num=selected["price_num"],
                        price_kind=selected["price_kind"], price_source_url=direct["source_page"])
        purchase["verified_price_source"] = {"kind": selected["price_kind"],
            "price": selected["price"], "source": direct["source_page"], "label":selected.get("price_label")}
    reconcile_notice_context(purchase,direct)
    if direct.get("price_conflict"):
        conflict={"source":"official_eis_context_conflict","value":None,
                  "evidence":"Не разрешено противоречие вида или значения цены в сохранённых сведениях ЕИС"}
        purchase.setdefault("_official_context_conflicts",{}).setdefault("price",conflict)
        purchase.update(price="",price_num=None,price_kind="unknown")
        purchase.pop("verified_price_source",None)
        purchase["fact_evidence"]["price"]=purchase["_official_context_conflicts"]["price"]
    conflicts=purchase.get("_official_context_conflicts") or {}
    if purchase.get("_current_soap_context"):
        # Only resolved summary values flow to later wrappers. The raw source
        # snapshots and their alternatives remain available as evidence.
        current_card=dict(saved_card(purchase))
        current_summary=dict(current_card.get("summary") or {})
        current_summary.update({key:purchase.get(key) for key in
            ("method","published","submission_start","submission_end","price","price_num","price_kind")})
        current_card["summary"]=current_summary
        purchase["card_fields_json"]=current_card
        scope=resolve(purchase,docs)
        purchase["sme_scope"],purchase["sme_only"]=scope["state"],scope["value"]
        purchase["fact_evidence"]["sme_scope"]=scope
    if card is not None:
        summary=card.setdefault("summary", {})
        if conflicts:
            for key in conflicts:summary[key]=purchase.get(key)
            if "method" in conflicts or "published" in conflicts:summary["method"]=""
            if "price" in conflicts:summary["price_num"]=None
        if not purchase.get("price_kind") and summary.get("price_kind"):
            purchase["price_kind"]=summary["price_kind"]
        summary.update({k: purchase[k] for k in
            ("method", "published", "submission_start", "submission_end", "price", "price_num", "price_kind")
            if purchase.get(k) is not None and purchase.get(k) != ""})
        card["fact_evidence"] = purchase["fact_evidence"]
        if direct.get("prices"):
            card["eis_card_prices"] = direct["prices"]
        if direct.get("price_formula"):
            card["price_formula"] = direct["price_formula"]
    if detail is not None:
        detail.update(purchaseMethodName=purchase.get("method"), publicationDate=purchase.get("published"),
                      submissionStart=purchase.get("submission_start"), submissionEnd=purchase.get("submission_end"),
                      initialSum=purchase.get("price"))
    return purchase


def _fact_updates(row, purchase, evidence):
    """Derive the small cache update outside SQLite's single-writer section.

    Equal JSON with different formatting/order is not changed evidence. Keep
    every existing source-precedence rule and unrelated card field intact.
    """
    values = {}
    for key, item in evidence.items():
        column = "published_at" if key == "published" else key
        # Official intake owns XML-labelled source scalars. A recheck must not
        # mutate its own source version by writing the derived cache back.
        if item.get("source") == "official_eis_xml":
            continue
        if (column in row and
                (not row[column] or item.get("source") == "official_eis_card") and
                row[column] != item["value"]):
            values[column] = item["value"]
    end = evidence.get("submission_end") or {}
    if (end.get("source") != "official_eis_xml" and end.get("value") and
            "submission_end" in row and "deadline" in row and
            (not row["submission_end"] or end.get("source") == "official_eis_card") and
            row["deadline"] != end["value"]):
        values["deadline"] = end["value"]
    try:
        previous = json.loads(row.get("card_fields_json") or "{}")
    except (TypeError, ValueError):
        previous = None
    card = json.loads(json.dumps(previous)) if isinstance(previous, dict) else {}
    if not isinstance(card.get("fact_evidence"), dict):
        card["fact_evidence"] = {}
    if not isinstance(card.get("summary"), dict):
        card["summary"] = {}
    card["fact_evidence"].update(evidence)
    card["summary"].update({key: purchase.get(key) for key in evidence})
    if "price" in evidence:
        card["summary"].update({key: purchase.get(key) for key in ("price", "price_num", "price_kind")})
    if "card_fields_json" in row and previous != card:
        values["card_fields_json"] = json.dumps(card, ensure_ascii=False)
    return values


def persist_facts(ns, purchase):
    """Read-only no-op; short revalidated transaction for an actual repair.

    Parsing/copying large card JSON while holding BEGIN IMMEDIATE previously
    reserved the writer on every audit even when no fact changed. Materialize
    and close a reader, compute outside the writer, then compare the source row
    under the existing lease guard. Never upgrade a stale read snapshot.
    """
    evidence = purchase.get("fact_evidence") or {}
    if not evidence:
        return
    reg = purchase.get("reg")
    # Caller-owned dictionaries must not change between planning and commit.
    purchase = json.loads(json.dumps({key: purchase.get(key) for key in
        set(evidence) | {"reg", "fact_evidence", "price", "price_num", "price_kind"}}, ensure_ascii=False))
    evidence = purchase["fact_evidence"]
    from audit_write_guard import guarded_transaction
    for _ in range(4):
        db = ns["conn"](); db.row_factory = sqlite3.Row
        try:
            found = db.execute("SELECT * FROM purchases WHERE reg_number=?", (reg,)).fetchone()
            snapshot = dict(found) if found is not None else None
        finally:
            db.close()
        if snapshot is None:
            return
        values = _fact_updates(snapshot, purchase, evidence)
        if not values:
            return
        db = ns["conn"](); db.row_factory = sqlite3.Row
        try:
            with guarded_transaction(db):
                found = db.execute("SELECT * FROM purchases WHERE reg_number=?", (reg,)).fetchone()
                current = dict(found) if found is not None else None
                if current != snapshot:
                    db.rollback()
                    continue
                db.execute("UPDATE purchases SET " + ",".join(key + "=?" for key in values) +
                           " WHERE reg_number=?", (*values.values(), reg))
                db.commit()
                return
        finally:
            if db.in_transaction:
                db.rollback()
            db.close()
    # Let the existing stage-only retry handle a continuously changing row.
    # No success is recorded and legal analysis itself is not repeated here.
    raise sqlite3.OperationalError("database is locked")


def save_snapshot(ns, data):
    reg = data["reg_number"]
    db = ns["conn"](); db.row_factory = sqlite3.Row
    try:
        db.execute("BEGIN IMMEDIATE")
        previous = db.execute("SELECT payload FROM v341_card_facts WHERE reg=?", (reg,)).fetchone()
        old = json.loads(previous[0]) if previous else {}
        # Documents tabs frequently omit the money/date block. An empty
        # supplementary page must not erase the common-information snapshot.
        merged = {**old, **data, "fields": {**old.get("fields", {}), **data.get("fields", {})},
                  "field_evidence": {**old.get("field_evidence", {}), **data.get("field_evidence", {})}}
        if not data.get("prices"):
            merged["prices"] = old.get("prices", [])
            if old.get("prices"):
                merged["source_page"] = old["source_page"]
        if not data.get("price_formula"):
            merged["price_formula"] = old.get("price_formula", "")
        merged["captured_at"] = now()
        db.execute("INSERT INTO v341_card_facts VALUES(?,?,?,?) ON CONFLICT(reg) DO UPDATE SET source_page=excluded.source_page,captured_at=excluded.captured_at,payload=excluded.payload",
                   (reg, merged["source_page"], now(), json.dumps(merged, ensure_ascii=False)))
        selected = chosen_price(merged["prices"])
        row = db.execute("SELECT * FROM purchases WHERE reg_number=?", (reg,)).fetchone()
        if selected:
            db.execute("""INSERT INTO v338_card_price(reg,price,price_num,price_kind,price_label,source_page,captured_at,conflict)
                VALUES(?,?,?,?,?,?,?,0) ON CONFLICT(reg) DO UPDATE SET
                price=excluded.price,price_num=excluded.price_num,price_kind=excluded.price_kind,
                price_label=excluded.price_label,source_page=excluded.source_page,captured_at=excluded.captured_at,
                conflict=0,alternative_price=''""", (reg, selected["price"], selected["price_num"],
                selected["price_kind"], selected["price_label"], merged["source_page"], now()))
        if row:
            row = dict(row)
            try:
                card = json.loads(row.get("card_fields_json") or "{}")
            except (ValueError, TypeError):
                card = {}
            if not isinstance(card, dict):
                card = {}
            values = {}
            if selected:
                if money(row.get("price")) and money(row["price"]) != money(selected["price"]):
                    history = card.setdefault("previous_prices", [])
                    prior = {"price": row["price"], "price_num": row.get("price_num"),
                             "price_kind": (card.get("summary") or {}).get("price_kind"), "replaced_at": now()}
                    if not history or history[-1].get("price") != prior["price"]:
                        history.append(prior)
                    card["previous_prices"] = history[-20:]
                values.update(price=selected["price"], price_num=selected["price_num"])
                card.setdefault("summary", {}).update({k: selected[k] for k in ("price", "price_num", "price_kind")})
                card["price_evidence"] = {**selected, "source": "official_eis_card", "source_url": merged["source_page"]}
            card["eis_card_prices"] = merged["prices"]
            card["price_formula"] = merged["price_formula"]
            values["card_fields_json"] = json.dumps(card, ensure_ascii=False)
            db.execute("UPDATE purchases SET "+",".join(k+"=?" for k in values)+" WHERE reg_number=?", (*values.values(), reg))
            if data != {k: old.get(k) for k in data}:
                db.execute("INSERT INTO v338_price_rechecks(reg,state,changed_at) VALUES(?,'pending',?) ON CONFLICT(reg) DO UPDATE SET state='pending',changed_at=excluded.changed_at", (reg, now()))
        db.commit()
    except Exception:
        if db.in_transaction:
            db.rollback()
        raise
    finally:
        db.close()
    if row:
        p = {"reg": reg}
        enrich(ns, p, [])
        persist_facts(ns, p)
    return {"accepted": True, "reg_number": reg, "prices": len(merged["prices"]), "fields": len(merged["fields"])}


def validate_snapshot(data):
    reg = str(data.get("reg_number") or "")
    source = str(data.get("source_page") or "")
    if not REG.fullmatch(reg) or not _official_source(source, reg):
        raise ValueError("A verified EIS card is required")
    fields = data.get("fields") or {}
    excerpts = data.get("field_evidence") or {}
    if not isinstance(fields, dict) or not isinstance(excerpts, dict):
        raise ValueError("Invalid facts")
    normalized = {}
    for key, value in fields.items():
        if key in DATE_LABELS:
            parsed = parse_day(value)
            if not parsed:
                raise ValueError("Invalid EIS date")
            normalized[key] = parsed.isoformat()
        elif key == "method" and METHOD.search(str(value)) and len(str(value)) <= 220:
            normalized[key] = compact(value)
        elif key == "sme_only" and isinstance(value, bool):
            normalized[key] = value
        else:
            raise ValueError("Unrecognised card fact")
        if not compact(excerpts.get(key)) or len(str(excerpts[key])) > 700:
            raise ValueError("A labelled excerpt is required")
    return {"reg_number": reg, "source_page": source, "fields": normalized,
            "field_evidence": {k: compact(excerpts[k]) for k in normalized},
            "prices": validate_prices(data.get("prices") or []),
            "price_formula": compact(data.get("price_formula"))[:1000]}


def install(ns):
    app = ns["app"]

    @app.post("/api/v341/eis223/card-facts")
    async def card_facts(request: Request):
        auth(request)
        if request.headers.get("origin") != "https://zakupki.gov.ru":
            raise HTTPException(403, "Сведения принимаются из карточки ЕИС")
        body = await request.body()
        if len(body) > 30000:
            raise HTTPException(413, "Слишком много сведений")
        try:
            data = validate_snapshot(json.loads(body))
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(422, "Не подтверждены поля карточки ЕИС")
        result = await asyncio.to_thread(save_snapshot, ns, data)
        print("V341 CARD_FACTS "+json.dumps(result, ensure_ascii=False), flush=True)
        return result

    @app.on_event("startup")
    async def start():
        await asyncio.to_thread(initialize, ns)

    print("V341 CARD_FACTS_READY price_kinds=7 dates=labelled method=source_verified", flush=True)
