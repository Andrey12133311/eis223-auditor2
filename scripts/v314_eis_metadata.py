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
import zipfile

from v295_eis_originals import allowed_documents, saved_document, uid_from_url, root
from v311_verified_customer import _name
from v342_identity import valid_inn, subject, incomplete_customer_name, customer_name_extension, unfinished_customer_quote

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
    r"^(?:наименование (?:(?:объекта|предмета) )?закупки|предмет закупки|объект закупки|предмет договора)"
    r"(?:\s*\([^)]*\))?[\s:]*$", re.I)
INN_LABEL = re.compile(r"\bИНН\s*(?:/\s*КПП\s*)?[:№]?\s*(\d{10,12})(?!\d)", re.I)
STOP_NAME = re.compile(
    r"\b(?:ИНН|КПП|ОГРН|Место нахождения|Почтовый адрес|Юридический адрес|"
    r"Адрес электронной|Контактное лицо|Телефон|Факс)\b|(?<!\d)\d{6}(?!\d)", re.I)
STATE_TITLE = re.compile(
    r"^(?:подача заявок|при[её]м заявок|начало подачи заявок|окончание подачи заявок|извещение|закупка №.*|статус закупки|статус:?.*|подведение итогов|закупка завершена|закупка отменена)$", re.I)


def compact(value):
    return " ".join(str(value or "").replace("\x00", " ").split())


def company(value):
    s = compact(value).strip(" |:;,")
    s = re.sub(r"^(?:наименование\s*\(\s*полное\s*и\s*сокращ[её]нное\s*\)|полное\s+наименование|наименование\s+заказчика)\s*[:：-]\s*", "", s, flags=re.I)
    s = STOP_NAME.split(s, maxsplit=1)[0].strip(" |:;,")
    s = re.sub(r"\(далее\s*[-–—]\s*([^)]*)\)",r"(\1)",s,flags=re.I)
    # The full and abbreviated names may share a cell. Keep the full name
    # plus its parenthesized abbreviation, but trim a separate short-name line.
    s = re.split(r"(?<=[»\"])\s+(?=(?:ООО|ПАО|ОАО|ЗАО|ФГБОУ|ГАУЗ)\b)", s)[0]
    return _name(s) if LEGAL.search(s) else ""


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


def _continue_buyer_name(value, following):
    """Join only explicit quoted continuations, stopping at other fields/roles."""
    result = compact(value)
    for raw in following[:12]:
        line = compact(raw)
        if (not line or '|' in line or STOP_NAME.search(line) or
                re.match(r"^(?:Поставщик|Исполнитель|Организатор|Участник|Предмет|"
                         r"Наименование|Сведения|Информация|Заказчик)\b", line, re.I)):
            break
        if not incomplete_customer_name(result):
            break
        # A bare form may only acquire an explicitly quoted name. Once its
        # quote is open, subsequent lines belong to that same quoted name.
        open_quote = unfinished_customer_quote(result)
        if not open_quote and not line.startswith(('«', '"', '„', '“')):
            break
        result += ' ' + line
        if len(result) > 2000:
            return compact(value)
    return result


def table_pairs(text):
    """Unwrap antiword's fixed-width table rows; preserve DOCX cell labels."""
    groups = []
    current = None
    source_lines = text.splitlines()
    for line_index, raw in enumerate(source_lines):
        line = raw.strip()
        if "|" not in line:
            if current and not current[1] and line and not re.fullmatch(r"\d+[.)]?", line):
                current[1] = line
                if BUYER_LABEL.fullmatch(current[0]):
                    current[1] = _continue_buyer_name(line, source_lines[line_index+1:])
                    current[2] += "\n" + current[1]
            continue
        cells = [compact(x) for x in line.strip("|").split("|")]
        if len(cells) == 1 and re.match(r"^(?:сведения|информация)\s+о\s+", cells[0], re.I):
            current = [cells[0], "", raw]
            groups.append(current)
            continue
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
        if current and (BUYER_LABEL.fullmatch(current[0]) or
                        re.fullmatch(r"наименование[:\s]*", current[0], re.I)):
            completed = _continue_buyer_name(current[1], source_lines[line_index+1:])
            if completed != current[1]:
                current[2] += "\n" + completed[len(compact(current[1])):].strip()
            current[1] = completed
    return groups


def parse_text(text):
    text = str(text or "")[:1_000_000]
    result = {}
    labelled_inns = set()
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
                result.update(title=title, title_evidence=compact(block)[:1000], title_kind="label")
        if re.fullmatch(r"ИНН\s+заказчика|ИНН(?:\s*/\s*КПП)?", label, re.I):
            if buyer_scope or re.search(r"заказчика", label, re.I):
                inn = valid_inn(value)
                if inn:
                    labelled_inns.add(inn)
    if len(labelled_inns) == 1:
        result["customer_inn"] = next(iter(labelled_inns))
    if not result.get("customer"):
        # Explicit buyer prose, never an arbitrary first legal entity in a file.
        m = re.search(r"(?im)^\s*Заказчик\s*:\s*([^\n]{6,2000})", text)
        if m:
            raw_name = _continue_buyer_name(m[1], text[m.end():].lstrip("\r\n").splitlines())
            if company(raw_name):
                result.update(customer=company(raw_name), customer_evidence=compact("Заказчик: "+raw_name)[:2500])
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
                    result.update(title=title,title_evidence=compact(line),title_kind="cover")
                    break
    return result


def _docx_body_without_auxiliary_parts(path):
    """Read the real body when a dangling Office relationship blocks python-docx.

    Some published DOCX files reference absent styles, images or printer
    settings. These parts are not needed to read the existing document body.
    Never recover a missing/malformed body or follow external relationships.
    """
    from lxml import etree
    from docx.oxml.ns import qn

    limit = 16 * 1024 * 1024
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 2000 or sum(i.file_size for i in entries) > 80 * 1024 * 1024:
                raise ValueError("DOCX expanded size exceeds the extraction limit")
            bodies = [i for i in entries if i.filename == "word/document.xml"]
            if len(bodies) != 1 or bodies[0].file_size > limit:
                raise ValueError("DOCX main document is missing, ambiguous or too large")
            with archive.open(bodies[0]) as stream:
                data = stream.read(limit + 1)
            if len(data) > limit:
                raise ValueError("DOCX main document exceeds the extraction limit")
    except (zipfile.BadZipFile, KeyError) as exc:
        raise ValueError("DOCX main document could not be read") from exc
    parser = etree.XMLParser(resolve_entities=False, no_network=True,
                             load_dtd=False, huge_tree=False, recover=False)
    try:
        document = etree.fromstring(data, parser=parser)
    except etree.XMLSyntaxError as exc:
        raise ValueError("DOCX main document contains invalid XML") from exc
    if document.getroottree().docinfo.doctype or document.tag != qn("w:document"):
        raise ValueError("DOCX main document has an unsupported XML structure")
    body = document.find(qn("w:body"))
    if body is None:
        raise ValueError("DOCX main document has no body")
    return body


def document_text(path, extractor):
    if Path(path).suffix.lower() == ".docx":
        from v347_text_cache import cached_extract
        return cached_extract(path, lambda p: _document_text_uncached(p, extractor),
                              namespace="eis223-docx-evidence-separators-20261020")
    return str(extractor(path) or "")


def _document_text_uncached(path, extractor):
    if Path(path).suffix.lower() == ".docx":
        # Maintain section/table order, including nested information cards.
        from docx import Document
        from docx.oxml.ns import qn
        from lxml import etree
        lines = []
        try:
            content = Document(path).element.body
        except KeyError as exc:
            # python-docx loads every related part, even styles/images that
            # have no bearing on the document's actual textual evidence.
            content = _docx_body_without_auxiliary_parts(path)
            LOG.warning("Recovered DOCX body with missing auxiliary part file=%s reason=%s",
                        Path(path).name, compact(exc)[:240])
        except (etree.XMLSyntaxError, zipfile.BadZipFile) as exc:
            raise ValueError("DOCX package contains invalid XML or ZIP data") from exc
        def value(el):
            # A manual break/tab is an actual field boundary in OOXML. Joining
            # only w:t nodes turns "№ 1<break/>Дата..." into "№ 1Дата...",
            # destroying an otherwise explicit event identity/date label.
            # Traverse each token once, including text inside nested content;
            # iterating each paragraph's descendants separately repeats the
            # nested paragraph text in text boxes.
            parts = []
            paragraph = qn("w:p")
            whitespace = {qn("w:br"): "\n", qn("w:cr"): "\n", qn("w:tab"): "\t"}
            for event, token in etree.iterwalk(el, events=("start", "end")):
                if event == "end":
                    if token.tag == paragraph:
                        parts.append("\n")
                elif token.tag == paragraph:
                    if parts and not parts[-1].endswith("\n"):
                        parts.append("\n")
                elif token.tag == qn("w:t"):
                    parts.append(token.text or "")
                elif token.tag in whitespace:
                    parts.append(whitespace[token.tag])
            return "".join(parts).removesuffix("\n")
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


def from_documents(reg, documents, known_customer=""):
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
        if fields.get("title_kind") == "cover" and not re.search(
                r"извещени|документаци|информационн.*карт|техническ.*задани",doc.get("name") or "",re.I):
            fields.pop("title",None)
            fields.pop("title_evidence",None)
        if known_customer and not fields.get("customer"):
            inns = buyer_inns(str(doc.get("text") or ""), known_customer)
            if len(inns) == 1:
                fields.update(customer=known_customer, customer_inn=next(iter(inns)))
        if fields:
            candidates.append({**fields, "source_url":url,
                               "document":doc.get("name", ""), "sha256":meta.get("sha256", "")})
    from v324_official_price import verified_nmcd
    price_evidence=verified_nmcd(documents)
    if not candidates and not price_evidence:
        return {}
    # Notices/documentation provide the identity; contracts can add its INN.
    chosen = next((v for v in candidates if v.get("customer")), candidates[0]).copy() if candidates else {}
    # Prefer a unique source-backed completion of an incomplete name.
    # Conflicting full names or INNs remain unresolved instead of guessing.
    extensions = [v for v in candidates if customer_name_extension(
        chosen.get("customer"), v.get("customer")) and not (
            chosen.get("customer_inn") and v.get("customer_inn") and
            chosen["customer_inn"] != v["customer_inn"])]
    extension_inns = {v["customer_inn"] for v in extensions if v.get("customer_inn")}
    if len({compact(v["customer"]).casefold() for v in extensions}) == 1 and len(extension_inns) <= 1:
        chosen = {**chosen, **extensions[0]}
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
        chosen["customer_identity_conflict"] = True
    if price_evidence.get("price_num") is not None:
        chosen.update(price=price_evidence["price"],price_num=price_evidence["price_num"],
                      price_source_url=price_evidence["source_url"],
                      price_document=price_evidence["document"],
                      price_kind=price_evidence.get("price_kind","nmcd"),
                      price_evidence=price_evidence["evidence"])
    elif price_evidence.get("price_conflict"):
        chosen["price_conflict"]=True
    return chosen


def _metadata_snapshot(db, reg):
    """Materialize only the three rows used by the repair; no writer reservation."""
    purchase = db.execute("SELECT * FROM purchases WHERE reg_number=?", (reg,)).fetchone()
    if purchase is None:
        return None
    buyer_table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='v311_verified_buyers'").fetchone()
    buyer = (db.execute("SELECT * FROM v311_verified_buyers WHERE reg=?", (reg,)).fetchone()
             if buyer_table else None)
    table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='v314_eis_metadata'").fetchone()
    saved = (db.execute("SELECT metadata_json,captured_at FROM v314_eis_metadata WHERE reg=?",
                        (reg,)).fetchone() if table else None)
    return (dict(purchase), dict(buyer) if buyer is not None else None,
            dict(saved) if saved is not None else None, bool(table))


def _metadata_plan(ns, snapshot, metadata, stamp):
    """Parse identity/price/JSON before acquiring SQLite's single writer."""
    p, prior, saved, _ = snapshot
    changes, buyer_insert, buyer_changes = {}, None, {}
    name = company(metadata.get("customer")) or _name(metadata.get("customer"))
    current_name = _name(p.get("customer"))
    extension = (customer_name_extension(current_name, name) and
                 not metadata.get("customer_identity_conflict"))
    if extension:
        incoming_inn = valid_inn(metadata.get("customer_inn"))
        existing_inns = {valid_inn(p.get("customer_inn"))}
        if prior:
            existing_inns.add(valid_inn(prior["inn"]))
            if not (customer_name_extension(prior["name"], name) or
                    compact(prior["name"]).casefold() == compact(name).casefold()):
                extension = False
        existing_inns.discard("")
        if len(existing_inns) > 1 or (incoming_inn and existing_inns and incoming_inn not in existing_inns):
            extension = False
    if name and (not current_name or extension or (
            current_name != compact(p.get("customer")) and name == current_name)):
        changes["customer"] = name
        if callable(ns.get("org_form")):
            changes["org_form"] = ns["org_form"](name)
    identity = lambda value: re.sub(r"[^а-яёa-z0-9]", "", re.sub(
        r"\s*\([^)]*\)\s*$", "", _name(value)).casefold())
    same = bool(name and identity(current_name) == identity(name))
    if name and ("customer" in changes or same):
        inn = valid_inn(metadata.get("customer_inn"))
        if inn and not valid_inn(p.get("customer_inn")):
            changes["customer_inn"] = inn
        # Keep the existing official-card precedence and provenance. Only an
        # actual missing-INN repair writes an already-known buyer again.
        if prior is None:
            buyer_insert = (name, inn, metadata.get("source_url", ""), "official_eis_document", stamp)
        elif prior["name"] == name and prior["inn"] == "" and inn:
            buyer_changes["inn"] = inn
    if extension and "customer" in changes and prior:
        buyer_changes.update(name=name, evidence_page=metadata.get("source_url", ""),
                             evidence_source="official_eis_document", captured_at=stamp)
    title = subject(metadata.get("title"))
    if title and not subject(p.get("title")):
        changes["title"] = title
    from v335_price_recovery import price_changes, money
    if money(metadata.get("price")):
        changes.update(price_changes(p, metadata))
    if changes:
        try:
            card = json.loads(p.get("card_fields_json") or "{}")
            summary = card.setdefault("summary", {})
            for key in ("customer", "customer_inn", "title", "price", "price_num"):
                if key in changes:
                    summary[key] = changes[key]
            if "price" in changes and money(changes["price"]) == money(metadata.get("price")):
                summary["price_kind"] = metadata.get("price_kind") or "nmcd"
            card["metadata_evidence"] = {"source": "official_eis_document", **metadata}
            changes["card_fields_json"] = json.dumps(card, ensure_ascii=False)
        except (ValueError, TypeError, AttributeError):
            pass
    # Repeated audits must not reserve a writer just to refresh captured_at.
    # A changed evidence payload is still durably saved even without a row repair.
    payload = json.dumps(metadata, ensure_ascii=False)
    try:
        same_metadata = saved is not None and json.loads(saved["metadata_json"]) == metadata
    except (ValueError, TypeError):
        same_metadata = False
    return changes, buyer_insert, buyer_changes, None if same_metadata else payload


def apply_metadata(ns, reg, metadata):
    if not metadata or not REG.fullmatch(reg):
        return False
    from audit_write_guard import guarded_transaction
    # Freeze caller-owned dictionaries before preflight/rebase work.
    metadata = json.loads(json.dumps(metadata, ensure_ascii=False))
    for _ in range(4):
        db = ns["conn"](); db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN")  # Short consistent read snapshot, never IMMEDIATE.
            snapshot = _metadata_snapshot(db, reg)
        finally:
            db.rollback()
            db.close()
        if snapshot is None:
            return False
        stamp = datetime.now(timezone.utc).isoformat()
        changes, buyer_insert, buyer_changes, payload = _metadata_plan(ns, snapshot, metadata, stamp)
        if not changes and buyer_insert is None and not buyer_changes and payload is None:
            return False
        db = ns["conn"](); db.row_factory = sqlite3.Row
        try:
            with guarded_transaction(db):
                # Intake can win between the read and the writer. Recompute
                # outside the writer, never overwrite its fresher correction.
                if _metadata_snapshot(db, reg) != snapshot:
                    db.rollback()
                    continue
                if not snapshot[3]:
                    db.execute("""CREATE TABLE IF NOT EXISTS v314_eis_metadata(
                        reg TEXT PRIMARY KEY, metadata_json TEXT NOT NULL, captured_at TEXT NOT NULL)""")
                if buyer_insert is not None:
                    db.execute("""INSERT INTO v311_verified_buyers
                        (reg,name,inn,evidence_page,evidence_source,captured_at) VALUES(?,?,?,?,?,?)""",
                        (reg, *buyer_insert))
                if buyer_changes:
                    db.execute("UPDATE v311_verified_buyers SET " +
                               ",".join(k + "=?" for k in buyer_changes) + " WHERE reg=?",
                               (*buyer_changes.values(), reg))
                if changes:
                    db.execute("UPDATE purchases SET " + ",".join(k + "=?" for k in changes) +
                               " WHERE reg_number=?", (*changes.values(), reg))
                if payload is not None:
                    db.execute("""INSERT INTO v314_eis_metadata(reg,metadata_json,captured_at) VALUES(?,?,?)
                        ON CONFLICT(reg) DO UPDATE SET metadata_json=excluded.metadata_json,
                            captured_at=excluded.captured_at""", (reg, payload, stamp))
                db.commit()
                return bool(changes)
        finally:
            if db.in_transaction:
                db.rollback()
            db.close()
    # Existing stage-only retry handles an unusually hot row. Never record a
    # successful repair from an uncommitted/stale plan or retry legal analysis.
    raise sqlite3.OperationalError("database is locked")


def stored_metadata(ns, reg):
    extractor = ns.get("extract_text")
    if not callable(extractor):
        raise RuntimeError("Document extractor unavailable")
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
    data_root=root().resolve()
    for row in rows[:100]:
        d = dict(row)
        stage = "metadata"
        try:
            meta = json.loads(d.get("metadata_json") or "{}")
            if not isinstance(meta,dict):continue
            provenance=meta.get("source")
            if provenance not in ("official_eis_original","official_eis_archive_member"):
                continue
            uid=uid_from_url(meta.get("source_url") or d.get("url",""))
            if not uid or uid not in allowed:continue
            receipt=saved_document(reg,uid)
            if receipt is None:continue
            original_path,original_meta=receipt
            file=Path(d.get("local_path") or "").resolve()
            if not file.is_file() or not file.is_relative_to(data_root) or file.stat().st_size>25*1024*1024:
                continue
            if file.suffix.lower() not in (".docx",".doc",".pdf",".odt",".rtf",".xlsx",".xls",".ods",".xml"):
                continue
            if provenance=="official_eis_original":
                if file!=original_path.resolve():continue
                expected=original_meta["sha256"]
            else:
                # The extracted member must belong to a verified ZIP/7Z/RAR
                # attachment, not to arbitrary text from an untrusted file.
                if meta.get("archive_sha256")!=original_meta["sha256"]:continue
                expected=meta.get("sha256")
            if not expected or not re.fullmatch(r"[0-9a-f]{64}",str(expected)):
                continue
            stage = "hash"
            with file.open("rb") as stream:
                if hashlib.file_digest(stream,"sha256").hexdigest()!=expected:
                    continue
            stage = "text"
            text=document_text(file,extractor)
            if len(str(text or "").strip()) < 3:
                continue
            # Legacy rows can carry the URL only in docs.url. Preserve the
            # verified receipt's hash and source for downstream provenance.
            meta = {**meta, "sha256":expected,
                    "source_url":meta.get("source_url") or d.get("url") or ""}
            docs.append({"name":d.get("name") or file.name,
                         "url":d.get("url") or meta["source_url"],"status":"checked",
                         "type":d.get("doc_type") or "",
                         "text":text,"meta":meta})
        except (OSError,ValueError,TypeError,KeyError) as exc:
            LOG.warning("Cannot read verified metadata reg=%s doc=%s stage=%s type=%s reason=%s",
                        reg,d.get("id"),stage,type(exc).__name__,compact(exc)[:240])
    db = ns["conn"]()
    try:
        row = db.execute("SELECT customer FROM purchases WHERE reg_number=?", (reg,)).fetchone()
        known_customer = _name(row[0]) if row else ""
    finally:
        db.close()
    return from_documents(reg,docs,known_customer)


def reconcile(ns):
    db = ns["conn"]()
    try:
        # Bounded rotating batch. Missing prices are common in old records;
        # rescanning thousands of DOCX/PDF originals on every restart can
        # stall Railway readiness and starve the actual legal-audit queue.
        pending = [row for row in db.execute("SELECT reg_number,customer,customer_inn,title,price_num "
            "FROM purchases ORDER BY last_seen DESC")
            if not _name(row[1]) or incomplete_customer_name(row[1]) or _name(row[1]) != compact(row[1]) or
            not valid_inn(row[2]) or not subject(row[3]) or row[4] is None]
        total=len(pending)
        offset=int(ns.get("_v314_metadata_offset") or 0)%max(1,total)
        rows=pending[offset:offset+55]
        ns["_v314_metadata_offset"]=(offset+len(rows))%max(1,total)
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
        async def periodic():
            # Do not block service boot/healthchecks on historical PDF/OCR.
            await asyncio.sleep(25)
            while True:
                try:
                    status.update(await asyncio.to_thread(reconcile,ns))
                except Exception:
                    LOG.exception("V324 metadata background sweep failed")
                await asyncio.sleep(300)
        asyncio.create_task(periodic())

    @app.get("/api/v314/customer-metadata/status")
    async def metadata_status():
        return status

    @app.get("/api/v342/metadata-evidence/{reg}",include_in_schema=False)
    async def metadata_evidence(reg:str):
        from fastapi import HTTPException
        from v332_all_procurements_audit import existing,stored_docs,is_official
        if not REG.fullmatch(reg):
            raise HTTPException(400,"Неверный номер закупки")
        try:
            purchase,rows=await asyncio.to_thread(existing,ns,reg)
        except LookupError:
            raise HTTPException(404,"Закупка не найдена")
        rows.sort(key=lambda row:0 if re.search(
            r"извещени|документаци|информационн.*карт",row.get("name") or "",re.I) else 1)
        docs,unreadable=await stored_docs(ns,reg,rows[:12])
        evidence=[]
        for doc in docs:
            if not is_official(doc):
                continue
            body=str(doc.get("text") or "")
            excerpts=[]
            for pattern in (r"\bИНН\b",r"(?:предмет|наименование|объект)\s+(?:закупки|договора)",
                            r"(?:наименование\s+заказчика|заказчик\s*[:|])"):
                for match in list(re.finditer(pattern,body,re.I))[:4]:
                    excerpts.append(body[max(0,match.start()-50):match.end()+500])
            evidence.append({"document":doc.get("name"),"source_url":(doc.get("meta") or {}).get("source_url"),
                             "extracted":parse_text(body),"labelled_excerpts":excerpts})
        return {"reg_number":reg,"current":{k:purchase.get(k) for k in ("customer","customer_inn","title")},
                "documents":evidence,"unreadable":unreadable,"source":"receipt_verified_EIS_originals"}

