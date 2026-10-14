"""Recover buyer metadata from this procurement's verified EIS originals.

No network requests, supplier/organizer guessing, or changes to audit results.
The same parser runs before a new offline audit and repairs existing records.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import re
import sqlite3

from v295_eis_originals import allowed_documents, saved_document, uid_from_url
from v311_verified_customer import _name

LOG = logging.getLogger(__name__)
REG = re.compile(r"3[0-9]{10}$")
LEGAL = re.compile(
    r"\b(?:ООО|АО|ОАО|ПАО|ЗАО|ФГУП|ФГБУ|ФГБОУ|ФБУЗ|ГАУЗ|ГАУ|ГБУ|ГБУЗ|"
    r"КГАУ|МБУ|МУП|ГУП|АНО|ИП)\b|(?:общество|учреждение|предприятие|"
    r"индивидуальный предприниматель)", re.I)
BUYER_LABEL = re.compile(
    r"^(?:наименование.*?заказчик[а-яё]*|заказчик)"
    r"(?:\s*\([^)]*\))?[\s:]*$", re.I)
SUBJECT_LABEL = re.compile(
    r"^(?:наименование (?:объекта )?закупки|предмет закупки|объект закупки)"
    r"(?:\s*\([^)]*\))?[\s:]*$", re.I)
INN_LABEL = re.compile(r"\bИНН\s*(?:/\s*КПП\s*)?[:№]?\s*(\d{10,12})(?!\d)", re.I)
STOP_NAME = re.compile(
    r"\b(?:ИНН|КПП|ОГРН|Место нахождения|Почтовый адрес|Юридический адрес|"
    r"Адрес электронной|Контактное лицо|Телефон|Факс)\b|(?<!\d)\d{6}(?!\d)", re.I)
STATE_TITLE = re.compile(
    r"^(?:подача заявок|при[её]м заявок|начало подачи заявок|окончание подачи заявок|извещение|закупка №.*|статус закупки|статус:?.*|подведение итогов|закупка завершена|закупка отменена)$", re.I)


def compact(value):
    return " ".join(str(value or "").replace("\x00", " ").split())


def valid_inn(value):
    s = str(value or "").strip()
    if not re.fullmatch(r"\d{10}|\d{12}", s) or len(set(s)) == 1:
        return ""
    digits = list(map(int, s))
    check = lambda weights: sum(a*b for a, b in zip(weights, digits)) % 11 % 10
    if len(s) == 10:
        ok = check((2, 4, 10, 3, 5, 9, 4, 6, 8)) == digits[9]
    else:
        ok = (check((7, 2, 4, 10, 3, 5, 9, 4, 6, 8)) == digits[10] and
              check((3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)) == digits[11])
    return s if ok else ""


def company(value):
    s = compact(value).strip(" |:;,")
    s = re.sub(r"^(?:наименование\s*\(\s*полное\s*и\s*сокращ[её]нное\s*\)|полное\s+наименование|наименование\s+заказчика)\s*[:：-]\s*", "", s, flags=re.I)
    s = STOP_NAME.split(s, maxsplit=1)[0].strip(" |:;,")
    s = re.sub(r"\(далее\s*[-–—]\s*([^)]*)\)",r"(\1)",s,flags=re.I)
    # The full and abbreviated names may share a cell. Keep the full name
    # plus its parenthesized abbreviation, but trim a separate short-name line.
    s = re.split(r"(?<=[»\"])\s+(?=(?:ООО|ПАО|ОАО|ЗАО|ФГБОУ|ГАУЗ)\b)", s)[0]
    return _name(s) if LEGAL.search(s) else ""


def subject(value):
    s = compact(value).strip(" |:;,")[:800]
    return s if len(s) >= 10 and not STATE_TITLE.fullmatch(s) else ""


def buyer_inns(text, name):
    normalize = lambda s: re.sub(r"[^а-яёa-z0-9]","",str(s).casefold())
    aliases = [name,re.sub(r"\s*\([^)]*\)\s*$","",name)]
    aliases += [m[1] for m in re.finditer(r"\(([^)]*)\)",name) if LEGAL.search(m[1])]
    aliases = [normalize(s) for s in aliases if len(normalize(s))>=8]
    matches_name = lambda s: any(alias in normalize(s) for alias in aliases)
    result = set()
    # Fixed-width contracts have buyer and supplier columns side by side.
    # Search ONLY the column actually headed by this buyer's name/role.
    active = None
    columns = []
    def flush():
        if active is not None and active<len(columns):
            result.update(valid_inn(m[1]) for m in INN_LABEL.finditer(columns[active]))
    for line in text.splitlines():
        if "|" not in line:
            flush();active=None;columns=[]
            continue
        cells = [compact(x) for x in line.strip("|").split("|")]
        if len(cells)<2:
            continue
        if not columns:
            columns=[""]*len(cells)
        for i,cell in enumerate(cells):
            if matches_name(cell) or re.fullmatch(r"(?:заказчик|покупатель)\s*:",cell,re.I):
                active=i
            if i<len(columns):columns[i]+="\n"+cell
    flush()
    # Also accept an INN directly following the explicit buyer in prose.
    # A intervening supplier/participant/recipient heading ends that scope.
    for match in INN_LABEL.finditer(text):
        window=text[max(0,match.start()-900):match.start()]
        if (matches_name(window) and not re.search(
                r"(?:Поставщик|Исполнитель|Участник|Получатель)\s*:",window,re.I)):
            result.add(valid_inn(match[1]))
    result.discard("")
    return result


def table_pairs(text):
    """Unwrap antiword's fixed-width table rows; preserve DOCX cell labels."""
    groups = []
    current = None
    for raw in text.splitlines():
        line = raw.strip()
        if "|" not in line:
            if current and not current[1] and line and not re.fullmatch(r"\d+[.)]?", line):
                current[1] = line
            continue
        cells = [compact(x) for x in line.strip("|").split("|")]
        numbered = len(cells) >= 3 and bool(re.fullmatch(r"[\d.]*[.)]?", cells[0]))
        if numbered:
            index, label, value = cells[0], cells[1], " ".join(cells[2:])
            continuation = not index
        elif len(cells) == 2 and re.fullmatch(r"\d+[.)]?", cells[0]):
            index, label, value, continuation = cells[0], cells[1], "", False
        elif len(cells) >= 2:
            label, value = cells[0], " ".join(cells[1:])
            continuation = not label or label.startswith("(полное")
        else:
            continue
        if continuation and current:
            current[0] = compact(current[0] + " " + label)
            current[1] = compact(current[1] + " " + value)
            current[2] += "\n" + raw
        else:
            current = [label, value, raw]
            groups.append(current)
    return groups


def parse_text(text):
    text = str(text or "")[:1_000_000]
    result = {}
    buyer_scope = False
    for label, value, block in table_pairs(text):
        if re.search(r"^(?:сведения|информация) о заказчике", label, re.I):
            buyer_scope = True
        elif re.search(r"^(?:сведения|информация) о (?:закупке|организаторе|участнике)", label, re.I):
            buyer_scope = False
        match = BUYER_LABEL.fullmatch(label)
        if match or (buyer_scope and re.fullmatch(r"наименование[:\s]*", label, re.I)):
            name = company(value)
            if name and not result.get("customer"):
                result.update(customer=name, customer_evidence=compact(block)[:1000])
                nums = {valid_inn(m[1]) for m in INN_LABEL.finditer(value)} - {""}
                if len(nums) == 1:
                    result["customer_inn"] = nums.pop()
        if not result.get("title") and SUBJECT_LABEL.fullmatch(label):
            title = subject(value)
            if title:
                result.update(title=title, title_evidence=compact(block)[:1000])
    if not result.get("customer"):
        # Explicit buyer prose, never an arbitrary first legal entity in a file.
        m = re.search(r"(?im)^\s*Заказчик\s*:\s*([^\n]{6,500})", text)
        if m and company(m[1]):
            result.update(customer=company(m[1]), customer_evidence=compact(m[0])[:1000])
    if not result.get("customer"):
        flat = compact(text)
        m = re.search(
            r"((?:Государственное|Краевое|Федеральное|Муниципальное|Открытое|"
            r"Публичное|Акционерное|Общество|ООО|АО|ОАО|ПАО|ГАУЗ|ГАУ|КГАУ)\b"
            r".{5,450}?),\s*именуем(?:ое|ый|ая)\s+в дальнейшем\s*[«\"]"
            r"(?:Заказчик|Покупатель)[»\"]", flat, re.I)
        if m and company(m[1]):
            result.update(customer=company(m[1]), customer_evidence=m[0][:1000])
    if result.get("customer") and not result.get("customer_inn"):
        nums = buyer_inns(text,result["customer"])
        if len(nums) == 1:
            result["customer_inn"] = nums.pop()
    if not result.get("title"):
        # A procurement subject on the cover of a notice/documentation;
        # never a lifecycle status or a section heading from the contract body.
        for line in text[:4000].splitlines():
            if re.match(r"^\s*(?:Поставка|Оказание услуг|Выполнение работ|Закупка)\s+",line,re.I):
                title = subject(line)
                if title:
                    result.update(title=title,title_evidence=compact(line))
                    break
    return result


def document_text(path, extractor):
    if Path(path).suffix.lower() == ".docx":
        # Maintain section/table order, including nested information cards.
        from docx import Document
        from docx.oxml.ns import qn
        lines = []
        content = Document(path).element.body
        value = lambda el: "\n".join(
            "".join(t.text or "" for t in p.iter(qn("w:t")))
            for p in el.iter(qn("w:p")))
        for node in content:
            if node.tag == qn("w:p"):
                lines.append(value(node))
            elif node.tag == qn("w:tbl"):
                for row in node.iter(qn("w:tr")):
                    cells = [value(c) for c in row.findall(qn("w:tc"))]
                    # Some information cards put the label and value in the
                    # same second cell, on two successive paragraphs.
                    if len(cells)==2 and re.fullmatch(r"\d+[.)]?",compact(cells[0])):
                        lines.append(compact(cells[0])+" | "+cells[1])
                    else:
                        lines.append(" | ".join(compact(c) for c in cells))
        return "\n".join(lines)
    return str(extractor(path) or "")


def from_documents(reg, documents):
    """Input is the already hash-verified original list from offline_audit."""
    candidates = []
    for doc in documents:
        meta = doc.get("meta") or {}
        if meta.get("source") not in ("official_eis_original", "official_eis_archive_member"):
            continue
        url = meta.get("source_url") or doc.get("url") or ""
        if not uid_from_url(url):
            continue
        fields = parse_text(doc.get("text", ""))
        if fields:
            candidates.append({**fields, "source_url":url,
                               "document":doc.get("name", ""), "sha256":meta.get("sha256", "")})
    from v324_official_price import verified_nmcd
    price_evidence=verified_nmcd(documents)
    if not candidates and not price_evidence:
        return {}
    # Notices/documentation provide the identity; contracts can add its INN.
    chosen = next((v for v in candidates if v.get("customer")), candidates[0]).copy() if candidates else {}
    if not chosen.get("title"):
        title = next((v for v in candidates if v.get("title")), {})
        if title:
            chosen.update(title=title["title"], title_evidence=title["title_evidence"],
                          title_source_url=title["source_url"])
    identity = lambda n: re.sub(r"\s*\([^)]*\)\s*$", "", compact(n)).casefold()
    inns = {v["customer_inn"] for v in candidates if v.get("customer_inn") and
            identity(v.get("customer")) == identity(chosen.get("customer"))}
    if len(inns) == 1:
        chosen["customer_inn"] = inns.pop()
    elif len(inns) > 1:
        chosen.pop("customer_inn", None)
    if price_evidence.get("price_num") is not None:
        chosen.update(price=price_evidence["price"],price_num=price_evidence["price_num"],
                      price_source_url=price_evidence["source_url"],
                      price_document=price_evidence["document"],
                      price_evidence=price_evidence["evidence"])
    elif price_evidence.get("price_conflict"):
        chosen["price_conflict"]=True
    return chosen


def apply_metadata(ns, reg, metadata):
    if not metadata or not REG.fullmatch(reg):
        return False
    db = ns["conn"]()
    db.row_factory = sqlite3.Row
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""CREATE TABLE IF NOT EXISTS v314_eis_metadata(
            reg TEXT PRIMARY KEY, metadata_json TEXT NOT NULL, captured_at TEXT NOT NULL)""")
        stamp = datetime.now(timezone.utc).isoformat()
        row = db.execute("SELECT * FROM purchases WHERE reg_number=?", (reg,)).fetchone()
        if row is None:
            db.commit()
            return False
        p = dict(row)
        changes = {}
        name = company(metadata.get("customer")) or _name(metadata.get("customer"))
        if name and (not _name(p.get("customer")) or
                     compact(p.get("customer")).casefold().startswith("наименование (полное и сокращ")):
            changes["customer"] = name
            if callable(ns.get("org_form")):
                changes["org_form"] = ns["org_form"](name)
        same = compact(p.get("customer")).casefold() == compact(name).casefold()
        if name and ("customer" in changes or same):
            inn = valid_inn(metadata.get("customer_inn"))
            if inn and not valid_inn(p.get("customer_inn")):
                changes["customer_inn"] = inn
            # Official card evidence remains authoritative; keep document provenance.
            db.execute("""INSERT INTO v311_verified_buyers
                (reg,name,inn,evidence_page,evidence_source,captured_at) VALUES(?,?,?,?,?,?)
                ON CONFLICT(reg) DO UPDATE SET
                    inn=CASE WHEN v311_verified_buyers.inn='' THEN excluded.inn
                             ELSE v311_verified_buyers.inn END
                WHERE v311_verified_buyers.name=excluded.name""",
                (reg,name,inn,metadata.get("source_url", ""),"official_eis_document",stamp))
        title = subject(metadata.get("title"))
        if title and not subject(p.get("title")):
            changes["title"] = title
        # Only an explicit number extracted from an actual EIS original can
        # fill a missing price; keep the earlier valid NMCD untouched.
        price=metadata.get("price")
        number=metadata.get("price_num")
        if price and isinstance(number,(int,float)) and 0<=number<1e15:
            existing_price=str(p.get("price") or "").strip()
            if not existing_price or existing_price in ("—","-","не установлена","нет данных"):
                changes["price"]=price
                if p.get("price_num") is None:changes["price_num"]=number
            elif p.get("price_num") is None:
                try:
                    from v311_verified_customer import _verified_money
                    if _verified_money(existing_price)[1]==number:
                        changes["price_num"]=number
                except ValueError:
                    pass
        if changes:
            try:
                card = json.loads(p.get("card_fields_json") or "{}")
                summary = card.setdefault("summary", {})
                for key in ("customer", "customer_inn", "title", "price", "price_num"):
                    if key in changes:
                        summary[key] = changes[key]
                card["metadata_evidence"] = {"source":"official_eis_document", **metadata}
                changes["card_fields_json"] = json.dumps(card,ensure_ascii=False)
            except (ValueError,TypeError,AttributeError):
                pass
            db.execute("UPDATE purchases SET " + ",".join(k+"=?" for k in changes) +
                       " WHERE reg_number=?", (*changes.values(),reg))
        db.execute("""INSERT INTO v314_eis_metadata(reg,metadata_json,captured_at) VALUES(?,?,?)
            ON CONFLICT(reg) DO UPDATE SET metadata_json=excluded.metadata_json,
                captured_at=excluded.captured_at""",(reg,json.dumps(metadata,ensure_ascii=False),stamp))
        db.commit()
        return bool(changes)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def stored_metadata(ns, reg):
    db = ns["conn"]()
    db.row_factory = sqlite3.Row
    try:
        rows = db.execute("SELECT * FROM docs WHERE reg_number=? ORDER BY "
                          "CASE doc_type WHEN 'notice' THEN 0 WHEN 'documentation' THEN 1 ELSE 2 END,id",
                          (reg,)).fetchall()
    finally:
        db.close()
    allowed = allowed_documents(reg)
    docs = []
    for row in rows[:100]:
        d = dict(row)
        try:
            meta = json.loads(d.get("metadata_json") or "{}")
            uid = uid_from_url(d.get("url", ""))
            if meta.get("source") != "official_eis_original" or uid not in allowed:
                continue
            saved = saved_document(reg, uid)
            if saved is None:
                continue
            path, receipt = saved
            if str(path) != d.get("local_path") or path.stat().st_size > 25*1024*1024:
                continue
            with path.open("rb") as stream:
                if hashlib.file_digest(stream,"sha256").hexdigest() != receipt["sha256"]:
                    continue
            if path.suffix.lower() not in (".docx", ".doc", ".pdf", ".odt", ".rtf"):
                continue
            text = document_text(path,ns["extract_text"])
            docs.append({"name":d["name"],"url":d["url"],"text":text,"meta":meta})
        except (OSError,ValueError,TypeError,KeyError):
            LOG.warning("Cannot read verified metadata reg=%s doc=%s",reg,d.get("id"))
    return from_documents(reg,docs)


def reconcile(ns):
    db = ns["conn"]()
    try:
        rows = db.execute("""SELECT reg_number FROM purchases
            WHERE customer IS NULL OR TRIM(customer)='' OR LOWER(customer) LIKE '%уточня%'
                OR LOWER(customer) LIKE 'наименование (полное и сокращ%' OR customer_inn IS NULL OR TRIM(customer_inn)='' OR title IS NULL OR LOWER(TRIM(title)) IN ('подача заявок','приём заявок','прием заявок','статус закупки') OR price_num IS NULL
            ORDER BY last_seen DESC LIMIT 2000""").fetchall()
    finally:
        db.close()
    corrected = 0
    for row in rows:
        reg = str(row[0])
        try:
            if REG.fullmatch(reg) and apply_metadata(ns,reg,stored_metadata(ns,reg)):
                corrected += 1
        except Exception:
            LOG.exception("Metadata repair failed reg=%s",reg)
    result = {"reviewed":len(rows),"corrected":corrected,"source":"verified_eis_originals"}
    print(f"V314 CUSTOMER_METADATA corrected={corrected} reviewed={len(rows)} remote_requests=0",flush=True)
    return result


def install(ns):
    app = ns["app"]
    status = {"reviewed":0,"corrected":0,"source":"verified_eis_originals"}

    @app.on_event("startup")
    async def backfill():
        status.update(await asyncio.to_thread(reconcile,ns))
        async def periodic():
            while True:
                await asyncio.sleep(300)
                status.update(await asyncio.to_thread(reconcile,ns))
        asyncio.create_task(periodic())

    @app.get("/api/v314/customer-metadata/status")
    async def metadata_status():
        return status
