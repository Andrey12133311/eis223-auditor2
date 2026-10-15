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
    "nmcd": r"(?:начальная\s*\(\s*максимальная\s*\)\s*цена(?:\s+договора|\s+контракта)?|начальная\s+максимальная\s+цена(?:\s+договора|\s+контракта)?|начальная\s+цена\s+(?:договора|контракта|лота)|нмц[дк])(?:\s*\(цена\s+лота\))?",
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


def chosen_price(prices):
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
        return result if result and _official_source(result.get("source_page"), reg) else {}
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
            if re.fullmatch(r"(?:участники\s+закупки|категория\s+участников|участие\s+МСП)", label, re.I):
                if re.search(r"(?:только|исключительно)\s+(?:субъекты|для\s+субъектов)\s+малого\s+и\s+среднего\s+предпринимательства", value, re.I):
                    key, parsed = "sme_only", True
                elif re.search(r"(?:не\s+ограничено|не\s+ограничивается)\s+(?:субъектами\s+)?(?:МСП|малого\s+и\s+среднего)", value, re.I):
                    key, parsed = "sme_only", False
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
    return {key: next(iter(values.values())) for key, values in candidates.items() if len(values) == 1}


def enrich(ns, purchase, docs, card=None, detail=None):
    reg = str(purchase.get("reg") or purchase.get("reg_number") or "")
    proof = document_facts(docs)
    direct = read_card(ns, reg)
    for key, value in direct.get("fields", {}).items():
        proof[key] = {"value": value, "document": "Карточка ЕИС",
                      "source_url": direct["source_page"], "source": "official_eis_card",
                      "evidence": str(direct.get("field_evidence", {}).get(key) or value)[:650]}
    # A saved EIS card is useful even if it captured a price before v341.
    if not direct.get("prices"):
        db = ns["conn"](); db.row_factory = sqlite3.Row
        try:
            row = db.execute("SELECT * FROM v338_card_price WHERE reg=? AND conflict=0", (reg,)).fetchone()
            if row and _official_source(row["source_page"], reg):
                direct = {**direct, "source_page": row["source_page"],
                          "prices": [{"price": row["price"], "price_num": row["price_num"],
                                      "price_kind": row["price_kind"], "price_label": row["price_label"]}]}
        except sqlite3.OperationalError:
            pass
        finally:
            db.close()
    purchase.setdefault("fact_evidence", {}).update(proof)
    for key, item in proof.items():
        if not purchase.get(key) or item["source"] == "official_eis_card":
            purchase[key] = item["value"]
    if purchase.get("submission_end"):
        purchase["deadline"] = purchase["submission_end"]
    selected = chosen_price(direct.get("prices") or [])
    if selected:
        purchase.update(price=selected["price"], price_num=selected["price_num"],
                        price_kind=selected["price_kind"], price_source_url=direct["source_page"])
        purchase["verified_price_source"] = {"kind": selected["price_kind"],
            "price": selected["price"], "source": direct["source_page"]}
    if card is not None:
        summary=card.setdefault("summary", {})
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
                      submissionStart=purchase.get("submission_start"), submissionEnd=purchase.get("submission_end"))
    return purchase


def persist_facts(ns, purchase):
    reg = purchase.get("reg")
    evidence = purchase.get("fact_evidence") or {}
    if not evidence:
        return
    db = ns["conn"](); db.row_factory = sqlite3.Row
    try:
        row = db.execute("SELECT * FROM purchases WHERE reg_number=?", (reg,)).fetchone()
        if not row:
            return
        row = dict(row)
        values = {}
        for key, item in evidence.items():
            column = "published_at" if key == "published" else key
            if column in row and (not row[column] or item.get("source") == "official_eis_card"):
                values[column] = item["value"]
        if values.get("submission_end") and "deadline" in row:
            values["deadline"] = values["submission_end"]
        try:
            card = json.loads(row.get("card_fields_json") or "{}")
        except (TypeError, ValueError):
            card = {}
        if not isinstance(card, dict):
            card = {}
        card.setdefault("fact_evidence", {}).update(evidence)
        card.setdefault("summary", {}).update({k: purchase.get(k) for k in evidence})
        if "card_fields_json" in row:
            values["card_fields_json"] = json.dumps(card, ensure_ascii=False)
        if values:
            db.execute("UPDATE purchases SET "+",".join(k+"=?" for k in values)+" WHERE reg_number=?", (*values.values(), reg))
        db.commit()
    finally:
        db.close()


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
