"""Saved 19-criterion inspection sheets, filled during the common legal audit.

Exports never invoke extraction/analyzers and never borrow the example complaint
from the user's form. Document evidence is retained outside the comments column.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from lxml import etree as ET
from zipfile import ZipFile, ZIP_DEFLATED

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response, RedirectResponse
from v295_eis_originals import root
from legal_audit_revision import engine_signature, EngineSignatureError

REG = re.compile(r"^[0-9]{11,20}$")
# Exact legacy report family observed in the restored runtime. Other purchase
# endpoints and the inspection-card page retain their existing behavior.
LEGACY_DOWNLOAD = re.compile(r"^/api/purchases/([^/]+)/inspection(?:-sheet)?(?:\.([A-Za-z0-9]+))?$")
TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
REVISION = "vp-inspection-19-20261105"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
OT = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
OP = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
PLACEHOLDER = re.compile(r"\{\{([A-Z_]+)\}\}")


def _reg(p):
    value = str(p.get("reg_number") or p.get("reg") or p.get("registrationNumber") or p.get("number") or "")
    if not REG.fullmatch(value):
        raise ValueError("Invalid inspection registration number")
    return value


def snapshot_path(reg):
    if not REG.fullmatch(str(reg)):
        raise ValueError("Invalid inspection registration number")
    return root() / "eis223_inspection_sheets" / (str(reg) + ".json")


def _value(p, *keys):
    return next((str(p[k]).strip() for k in keys if p.get(k) not in (None, "")), "")


def metadata(p, detail=None, card=None):
    """Map only current procurement facts; complaint facts are not guessed."""
    detail = detail if isinstance(detail, dict) else {}
    card = card if isinstance(card, dict) else {}
    values = {**detail, **{k: v for k, v in p.items() if v not in (None, "")}}
    price = _value(values, "price", "price_num", "initialSum")
    price_kind = _value(values, "price_kind")
    if price_kind in ("framework", "unit_price", "formula"):
        price += " (" + {"framework": "рамочный договор", "unit_price": "цена единицы", "formula": "формула цены"}[price_kind] + ")"
    return {
        "CASE_NUMBER": "", "COMPLAINT_RECEIVED": "", "HEARING_DATE": "",
        "COMPLAINANT": "", "COMPLAINT_ARGUMENT": "",
        "CUSTOMER": _value(values, "customer", "customer_name"),
        "ORGANIZER": _value(values, "organizer", "organizer_name"),
        "PLATFORM": _value(values, "platform", "etp", "electronicPlatformName"),
        "REG_NUMBER": _reg(p), "METHOD": _value(values, "method", "purchaseMethodName"),
        "TITLE": _value(values, "title", "purchaseName"),
        "BIDS": _value(values, "bids_summary", "applications_summary"),
        "PRICE": price,
        "STAGE": _value(values, "stage", "purchase_status", "purchaseStatusName"),
        "PUBLISHED": _value(values, "published", "published_at", "publicationDate"),
        "SUBMISSION_START": _value(values, "submission_start", "submissionStart"),
        "SUBMISSION_END": _value(values, "submission_end", "deadline", "submissionEnd"),
        "RESULT_DATE": _value(values, "result_date", "summarizing_date", "summarizingDate"),
    }


def source_fingerprint(p, docs):
    sources = []
    for d in docs:
        if not isinstance(d, dict):
            continue
        m = d.get("meta") if isinstance(d.get("meta"), dict) else {}
        sources.append((str(d.get("name") or ""), str(m.get("sha256") or ""),
                        str(m.get("source_url") or d.get("url") or ""),
                        hashlib.sha256(str(d.get("text") or "").encode()).hexdigest()))
    payload = json.dumps({"purchase": p, "sources": sorted(sources)}, ensure_ascii=False,
                         sort_keys=True, default=str).encode()
    return hashlib.sha256(payload).hexdigest()


def save_snapshot(ns, p, docs, findings, checks, detail=None, card=None):
    """Run after the audit's evidence stage, with all SQLite locks released."""
    from vp_inspection_engine import evaluate
    reg = _reg(p)
    result = evaluate(p, docs, findings, checks, detail=detail, card=card)
    snapshot = {**result, "reg_number": reg, "revision": REVISION,
                "legal_engine_signature": engine_signature(),
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "source_fingerprint": source_fingerprint(p, docs),
                "fields": metadata(p, detail, card)}
    # Defense in depth: no upstream notes may populate the requested blank column.
    for row in snapshot.get("rows", []):
        row["comment"] = ""
    target = snapshot_path(reg)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str).encode()
    descriptor, scratch = tempfile.mkstemp(prefix=".inspection-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(scratch, target)
    finally:
        if os.path.exists(scratch):
            os.unlink(scratch)
    return snapshot


def read_snapshot(reg):
    path = snapshot_path(reg)
    if not path.is_file():
        raise HTTPException(409, "Лист ещё не сформирован. Дождитесь завершения проверки закупки.")
    try:
        if path.stat().st_size > 4 * 1024 * 1024:
            raise ValueError("oversized snapshot")
        result = json.loads(path.read_text("utf-8"))
        if not isinstance(result, dict):
            raise ValueError("invalid snapshot")
        if not isinstance(result.get("rows"), list) or not all(isinstance(r, dict) for r in result["rows"]):
            raise ValueError("invalid checklist")
        if result.get("reg_number") != reg or result.get("revision") != REVISION:
            raise ValueError("stale snapshot")
        try:
            current_signature = engine_signature()
        except EngineSignatureError as exc:
            raise HTTPException(503, "Не удалось проверить актуальность правил проверки. Повторите запрос позже.") from exc
        if result.get("legal_engine_signature") != current_signature:
            raise ValueError("outdated legal engine")
        from vp_inspection_criteria import CRITERIA
        if [r.get("key") for r in result.get("rows", [])] != [c.key for c in CRITERIA]:
            raise ValueError("wrong checklist")
        for row in result["rows"]:
            row["comment"] = ""
        return result
    except (ValueError, TypeError, OSError) as exc:
        raise HTTPException(409, "Лист требуется сформировать повторной проверкой закупки.") from exc


def _replace_tokens(tree, fields):
    for node in tree.iter():
        if node.text:
            node.text = PLACEHOLDER.sub(lambda m: str(fields.get(m[1]) or ""), node.text)
        if node.tail:
            node.tail = PLACEHOLDER.sub(lambda m: str(fields.get(m[1]) or ""), node.tail)


def _docx_cell(cell, value):
    for child in list(cell):
        if child.tag != W + "tcPr":
            cell.remove(child)
    p = ET.SubElement(cell, W + "p")
    ppr = ET.SubElement(p, W + "pPr")
    ET.SubElement(ppr, W + "jc", {W + "val": "center"})
    r = ET.SubElement(p, W + "r")
    ET.SubElement(r, W + "t").text = str(value or "")


def _odt_cell(cell, value):
    for child in list(cell):
        cell.remove(child)
    cell.attrib.pop(OT + "formula", None)
    ET.SubElement(cell, OP + "p", {OP + "style-name": "VPInspectionMark"}).text = str(value or "")


def _status(row):
    if row.get("has_violation"):
        return "Есть нарушение"
    if row.get("no_violation"):
        return "Нет нарушения по проверенному обстоятельству"
    if row.get("status") == "not_applicable" or row.get("applicable") is False:
        return "Не применяется"
    return "Требует проверки"


def _display_stamp(value):
    try:
        from zoneinfo import ZoneInfo
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return str(value or "")
        return parsed.astimezone(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y %H:%M МСК")
    except (ValueError, TypeError):
        return str(value or "")


def _appendix(snapshot):
    lines = ["Основания результатов проверки",
             "Отметки относятся к проверенным обстоятельствам доступных оригиналов. Пустые отметки означают, что вывод не подтверждён либо критерий не применяется. Графа «Комментарий» оставлена пустой.",
             "Сформировано: " + _display_stamp(snapshot.get("generated_at"))]
    for number, row in enumerate(snapshot["rows"], 1):
        lines.append(str(number) + ". " + str(row.get("criterion") or row.get("key")) + ": " + _status(row))
        if row.get("reason"):
            lines.append(str(row["reason"]))
        for reason in dict.fromkeys(row.get("methodology_reasons") or []):
            lines.append("Проверка обоснованности: " + str(reason))
        if row.get("review_required") and row.get("required_evidence"):
            lines.append("Для завершения проверки: " + str(row["required_evidence"]))
        # The engine may retain multiple episodes under one criterion.
        events = row.get("results") or row.get("findings") or row.get("evidence_items") or []
        if not isinstance(events, list):
            events = []
        if not events and row.get("evidence"):
            events = [row]
        # An unsupported candidate is retained as a review question with its
        # stated source and norm, never silently lost or endorsed as a finding.
        events = events + [item for item in row.get("unresolved", [])
                           if isinstance(item, dict) and item not in events]
        for event in events:
            if not isinstance(event, dict):
                continue
            if event.get("status"):
                label = {"violation": "Есть нарушение", "compliant": "Нет нарушения по проверенному обстоятельству", "unknown": "Требует проверки"}.get(str(event["status"]), "Требует проверки")
                lines.append(label + (": " + str(event["title"]) if event.get("title") else ""))
            if event.get("law"):
                prefix = "Норма, требующая подтверждения: " if event.get("legal_basis_verified") is False else "Норма: "
                lines.append(prefix + str(event["law"]))
            if event.get("decision_kind") == "document_requirement":
                lines.append("Проверено условие документа. Фактическое отклонение заявки этим выводом не устанавливается.")
            for reason in dict.fromkeys(event.get("methodology_reasons") or []):
                lines.append("Проверка обоснованности: " + str(reason))
            if event.get("reason"):
                lines.append(str(event["reason"]))
            if event.get("evidence"):
                lines.append(str(event["evidence"]))
            if event.get("source_excerpt") and event["source_excerpt"] != event.get("evidence"):
                lines.append("Фрагмент оригинала: " + str(event["source_excerpt"]))
            source = " · ".join(str(event.get(k) or "") for k in ("document", "archive_member", "location", "source_url") if event.get(k))
            if source:
                lines.append("Источник: " + source)
            if event.get("sha256"):
                lines.append("SHA-256 оригинала: " + str(event["sha256"]))
            if event.get("note"):
                lines.append(str(event["note"]))
    return lines


def render(snapshot, extension="odt"):
    """Fill a sanitized copy of the user's geometry-preserving form."""
    if extension not in ("odt", "docx"):
        raise ValueError("Unsupported inspection format")
    path = TEMPLATES / ("vp-inspection-2026." + extension)
    with ZipFile(path) as z:
        parts = {item.filename: z.read(item.filename) for item in z.infolist()}
    part = "content.xml" if extension == "odt" else "word/document.xml"
    tree = ET.fromstring(parts[part])
    _replace_tokens(tree, snapshot.get("fields") or {})
    if extension == "docx":
        body = tree.find(W + "body")
        tables = body.findall(W + "tbl")
        rows = tables[1].findall(W + "tr")
        for target, row in zip(rows[1:20], snapshot["rows"]):
            cells = target.findall(W + "tc")
            for cell, value in zip(cells[1:], (row.get("has_violation"), row.get("no_violation"), row.get("koap"), "")):
                _docx_cell(cell, value)
        # Keep the original sheet intact; explanatory evidence goes on its own page.
        section = body.find(W + "sectPr")
        at = list(body).index(section) if section is not None else len(body)
        for i, line in enumerate(_appendix(snapshot)):
            p = ET.Element(W + "p")
            if i == 0:
                pr = ET.SubElement(p, W + "pPr")
                ET.SubElement(pr, W + "pageBreakBefore")
            r = ET.SubElement(p, W + "r")
            ET.SubElement(r, W + "t").text = line
            body.insert(at, p)
            at += 1
    else:
        office = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
        style = "{urn:oasis:names:tc:opendocument:xmlns:style:1.0}"
        fo = "{urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0}"
        styles = tree.find(office + "automatic-styles")
        if styles is None:
            styles = ET.SubElement(tree, office + "automatic-styles")
        mark = ET.SubElement(styles, style + "style", {style + "name": "VPInspectionMark", style + "family": "paragraph"})
        ET.SubElement(mark, style + "paragraph-properties", {fo + "text-align": "center"})
        body = tree.find(office + "body/" + office + "text")
        tables = body.findall(OT + "table")
        rows = tables[1].findall(".//" + OT + "table-row")
        for target, row in zip(rows[1:20], snapshot["rows"]):
            cells = target.findall(OT + "table-cell")
            for cell, value in zip(cells[1:], (row.get("has_violation"), row.get("no_violation"), row.get("koap"), "")):
                _odt_cell(cell, value)
        for line in _appendix(snapshot):
            ET.SubElement(body, OP + "p").text = line
    parts[part] = ET.tostring(tree, encoding="utf-8", xml_declaration=True)
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as z:
        if "mimetype" in parts:
            z.writestr("mimetype", parts.pop("mimetype"), compress_type=0)
        for name, value in parts.items():
            z.writestr(name, value)
    return buffer.getvalue()


def install(ns):
    ns["_vp_save_snapshot"] = lambda p, docs, findings, checks, detail=None, card=None: save_snapshot(ns, p, docs, findings, checks, detail, card)
    app = ns["app"]

    def require_login(request):
        if os.getenv("EIS223_AUTH_ENFORCE", "").lower() == "true" and request.scope.get("eis223_account_id") is None:
            raise HTTPException(401, "Требуется авторизация")

    async def download(reg, extension, request):
        require_login(request)
        if not REG.fullmatch(reg) or extension not in ("odt", "docx", "json"):
            raise HTTPException(404, "Лист проверки не найден")
        snapshot = await asyncio.to_thread(read_snapshot, reg)
        if extension == "json":
            response = JSONResponse(snapshot, headers={"Cache-Control": "private,no-store"})
        else:
            data = await asyncio.to_thread(render, snapshot, extension)
            mime = {"odt": "application/vnd.oasis.opendocument.text", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}[extension]
            response = Response(data, media_type=mime, headers={"Cache-Control": "private,no-store",
                            "Content-Disposition": 'attachment; filename="inspection_' + reg + '.' + extension + '"'})
        if request.method == "HEAD":
            response.body = b""
        return response

    @app.api_route("/api/inspection/{reg}.{extension}", methods=["GET", "HEAD"])
    async def inspection_file(reg: str, extension: str, request: Request):
        return await download(reg, extension, request)

    @app.middleware("http")
    async def current_inspection_downloads(request, call_next):
        # The restored runtime has middleware that can serve its old template
        # before route dispatch. This narrowly scoped outer boundary handles
        # only its observed report family, after the production account gate.
        match = LEGACY_DOWNLOAD.fullmatch(request.url.path)
        if request.method not in ("GET", "HEAD") or match is None:
            return await call_next(request)
        try:
            require_login(request)
            reg, extension = match[1], match[2] or "odt"
            if not REG.fullmatch(reg):
                raise HTTPException(404, "Лист проверки не найден")
            if extension == "pdf":
                return RedirectResponse("/api/inspection/" + reg + ".odt", status_code=303,
                                        headers={"Cache-Control": "private,no-store"})
            return await download(reg, extension, request)
        except HTTPException as exc:
            response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code,
                                    headers={**(exc.headers or {}), "Cache-Control": "private,no-store"})
            if request.method == "HEAD":
                response.body = b""
            return response

    print("VP_INSPECTION_EXPORT_READY criteria=19 comments=blank formats=odt,docx saved_only=True", flush=True)
