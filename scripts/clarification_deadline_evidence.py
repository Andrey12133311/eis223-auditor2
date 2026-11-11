"""Pure evidence selection for the bid deadline applicable to one received request.

The latest purchase-card deadline is diagnostic only. A historical request needs
its own explicit as-of statement or an already verified notice-revision snapshot.
This module never reads files, fetches EIS, counts workdays or changes input data.
"""
from __future__ import annotations

import re
from datetime import date, datetime

from legal_document_context import document_context, local_context, source_scope_context
from v331_broad_legal import verified


_MONTHS = {name: index for index, name in enumerate(
    ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
     "сентября", "октября", "ноября", "декабря"), 1)}
_DATE = (r"(?:20\d{2}-[01]\d-[0-3]\d|[0-3]?\d\.[01]?\d\.20\d{2}|"
         r"[«\"]?[0-3]?\d[»\"]?\s+(?:" + "|".join(_MONTHS) + r")\s+20\d{2})")
_DATE_TOKEN = re.compile(_DATE, re.I)
_SEPARATOR = r"\s*[:：|=–—\-\s]{0,15}"
_RECEIPT = re.compile(
    r"(?:дата\s+(?:и\s+время\s+)?(?:поступления|получения|регистрации)"
    r"(?:\s*\(\s*(?:поступления|получения|регистрации)\s*\))?\s+"
    r"(?:(?:заказчиком|заказчику)\s+)?запроса[^:;!?]{0,170}?|"
    r"запрос\b[^:;!?]{0,170}?\s+(?:поступил|получен|зарегистрирован))"
    + _SEPARATOR + r"(?P<date>" + _DATE + r")", re.I)
_DEADLINE = (r"(?:дата\s+(?:и\s+время\s+)?окончания\s+(?:срока\s+)?"
             r"(?:подачи|при[её]ма)\s+заявок|срок\s+(?:окончания\s+)?"
             r"(?:подачи|при[её]ма)\s+заявок)")
_REQUEST_TIME = (r"(?:на\s+(?:дату|момент)\s+(?:(?:поступления|получения|регистрации)\s+)?"
                 r"(?:настоящего\s+|данного\s+)?запроса)")
_AS_OF_DEADLINE = re.compile(
    r"(?:" + _DEADLINE + r"\s*(?:,\s*)?(?:"
    r"(?:по\s+(?:извещению|редакции\s+извещения)\s*,?\s*)?действовавш\w*\s+"
    + _REQUEST_TIME + r"|" + _REQUEST_TIME + r")|"
    + _REQUEST_TIME + r"\s*(?:[,;:]\s*)?" + _DEADLINE + r"|"
    r"по\s+действовавш\w*\s+" + _REQUEST_TIME + r"\s+редакции\s+извещения\s+"
    + _DEADLINE + r")" + _SEPARATOR + r"(?P<date>" + _DATE + r")", re.I)
_REQUEST_HEADING = re.compile(
    r"(?:^|[\r\n])[ \t]*(?:(?:ответ|разъяснение)\s+на\s+)?запрос\s*(?:№|N)\s*"
    r"[\w./-]{1,80}", re.I)
_REQUEST_ID = re.compile(
    r"\bзапрос[а-я]*(?:\s+(?:на\s+)?разъяснени[а-я]*)?\s*(?:№|N)\s*"
    r"([\w./-]{1,80})", re.I)
_NON_ACTUAL = re.compile(
    r"\b(?:планов\w*|планируем\w*|предлагаем\w*|предполагаем\w*|"
    r"рекомендуем\w*|расч[её]тн\w*|не\s+подтвержд[её]н\w*|"
    r"если|например|допустим|предположим|проект)\s*[:：—–-]?\s*$", re.I)
_NOTICE_TYPE = re.compile(r"purchaseNotice(?:OK|OA|ZK|EP|AE|AE94FZ|KESMBO|AESMBO|ZPESMBO|ZKESMBO)?$")
_SHA = re.compile(r"[0-9a-f]{64}$")
_MAX_BODY = 750000
_MAX_REVISIONS = 200


def _day(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value or "").strip()
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        match = re.fullmatch(r"([0-3]?\d)\.([01]?\d)\.(20\d{2})", raw)
        if match:
            try:
                return date(int(match[3]), int(match[2]), int(match[1]))
            except ValueError:
                return None
        match = re.fullmatch(r"[«\"]?([0-3]?\d)[»\"]?\s+([а-я]+)\s+(20\d{2})", raw, re.I)
        if match and match[2].casefold() in _MONTHS:
            try:
                return date(int(match[3]), _MONTHS[match[2].casefold()], int(match[1]))
            except ValueError:
                return None
    return None


def _bounds(value, length):
    return (isinstance(value, (tuple, list)) and len(value) == 2 and
            all(isinstance(part, int) and not isinstance(part, bool) for part in value) and
            0 <= value[0] < value[1] <= length)


def _result(state, reason_code, reason, evidence=(), deadline=None, current=None):
    records = [dict(item) for item in evidence][:20]
    descriptions = []
    for item in records:
        if item["kind"] == "request_document":
            descriptions.append("Оригинал «" + item["document"] + "»: " + item["quote"])
        else:
            descriptions.append("XML извещения ЕИС, версия " + item["version"] +
                                ", публикация " + item["publication"] +
                                ", срок подачи " + item["deadline"] +
                                (", изменение XML " + item["modified"] if item.get("modified") else "") +
                                ", SHA-256 " + item["sha256"])
    return {"state": state, "deadline": deadline.isoformat() if deadline else None,
            "reason_code": reason_code, "reason": reason, "evidence": records,
            "evidence_text": "; ".join(descriptions)[:6000],
            "current_card_deadline": current.isoformat() if current else None}


def _same_scope(left, right):
    return (right["eligible_actual"] and
            all(left[key] == right[key] for key in ("purchase_ids", "lots", "lot_ids")))


def resolve_clarification_deadline(p, request_doc, *, received, request_span=None, notice_revisions=None):
    """Select a source-specific historical deadline, never the newest card value.

    ``request_span`` is the existing REQUEST match's (start, end). If omitted,
    only one explicit receipt label matching ``received`` may bind the request.
    ``notice_revisions`` defaults to p['_verified_notice_revisions'] and must be
    the already verified snapshot for this purchase (the shape returned by
    v298_eis_only.official_notice_revisions). The in-memory _current_soap_context
    is also accepted after identity/hash checks. These hashes are provenance,
    not a substitute for upstream XML/receipt verification. Absent snapshots do
    not trigger I/O; the caller owns collection and snapshot reuse.
    """
    p = p if isinstance(p, dict) else {}
    received = _day(received)
    current = _day(p.get("submission_end") or p.get("deadline"))
    def review(code, reason, evidence=()):
        return _result("requires_review", "clarification_deadline_" + code, reason, evidence, current=current)
    if not received or not isinstance(request_doc, dict) or not verified(request_doc):
        return review("request_unverified", "Не подтверждены оригинал и дата получения конкретного запроса.")
    body = str(request_doc.get("text") or "")[:_MAX_BODY].replace("\xa0", " ")
    receipts = list(_RECEIPT.finditer(body))
    if request_span is None:
        if len(receipts) != 1 or _day(receipts[0]["date"]) != received:
            return review("request_ambiguous", "Не установлена однозначная привязка срока к конкретному запросу.")
        start, end = receipts[0].span()
    elif _bounds(request_span, len(body)):
        start, end = request_span
        matching = [item for item in receipts if item.start() == start and item.end() == end
                    and _day(item["date"]) == received]
        if len(matching) != 1:
            return review("request_ambiguous", "Позиция запроса не подтверждает указанную дату получения.")
    else:
        return review("request_ambiguous", "Не определён фрагмент конкретного запроса в оригинале.")
    context = document_context(request_doc)
    # A formerly effective original can prove its historical deadline. A draft,
    # cancelled act, example or instruction cannot prove that a request occurred.
    if any(reason != "explicitly_superseded_revision" for reason in context["reasons"]):
        return review("source_ineligible", "Проект, образец или отменённый документ не подтверждает исторический срок запроса.")
    request_scope = source_scope_context(p, request_doc, start)
    if not request_scope["eligible_actual"]:
        return review("source_scope", "Исходный запрос не привязан однозначно к этой закупке и лоту.")
    left = max(0, start - 1500) if len(receipts) == 1 else start
    right = min(len(body), end + 1500)
    right = min([item.start() for item in receipts if item.start() > start] + [right])
    headings = list(_REQUEST_HEADING.finditer(body))
    left = max([item.start() for item in headings if item.end() <= start] + [left])
    right = min([item.start() for item in headings if item.start() > start] + [right])
    evidence, text_values = [], set()
    identifiers = {item[1].casefold().rstrip(".") for item in _REQUEST_ID.finditer(body, left, right)}
    for match in _AS_OF_DEADLINE.finditer(body, left, right):
        value = _day(match["date"])
        if not value:
            return review("invalid_date", "В явном историческом сроке указана некорректная дата.", evidence)
        quoted = {"kind": "request_document", "document": str(request_doc.get("name") or ""),
                  "source_url": str((request_doc.get("meta") or {}).get("source_url") or request_doc.get("url") or ""),
                  "sha256": (request_doc.get("meta") or {}).get("sha256"),
                  "quote": match[0], "position": match.start(), "deadline": value.isoformat()}
        evidence.append(quoted)
        local = local_context(body, match.start(), match.end())
        if (len(identifiers) > 1 or _NON_ACTUAL.search(body[max(left, match.start()-100):match.start()]) or
                any(reason != "historical_reference" for reason in local["reasons"]) or
                not _same_scope(request_scope, source_scope_context(p, request_doc, match.start()))):
            return review("source_ambiguous", "Явный срок относится к неоднозначному запросу, лоту или условному примеру.", evidence)
        text_values.add(value)
    if len(text_values) > 1:
        return review("conflict", "Для конкретного запроса указаны противоречивые исторические сроки подачи заявок.", evidence)
    snapshot = p.get("_verified_notice_revisions", ()) if notice_revisions is None else notice_revisions
    if not isinstance(snapshot, (tuple, list)) or len(snapshot) > _MAX_REVISIONS:
        return review("snapshot_unavailable", "Не получен полный ограниченный снимок проверенных редакций извещения.", evidence)
    snapshot = list(snapshot)
    reg = str(p.get("reg") or p.get("reg_number") or "")
    soap = p.get("_current_soap_context")
    if isinstance(soap, dict):
        record, fields = soap.get("record"), soap.get("fields")
        if (isinstance(record, dict) and isinstance(fields, dict) and record.get("xml_family") == "notice" and
                record.get("reg_number") == reg and _SHA.fullmatch(str(record.get("xml_sha256") or "")) and
                _NOTICE_TYPE.fullmatch(str(record.get("xml_type") or ""))):
            publication, published_field = _day(record.get("publication")), _day(fields.get("published"))
            if publication and published_field and publication != published_field:
                return review("conflict", "Проверенный текущий XML содержит противоречивые сведения о публикации.", evidence)
            snapshot.insert(0, {**record, "sha256": record["xml_sha256"],
                                "publication": record.get("publication") or fields.get("published"),
                                "submission_end": fields.get("submission_end")})
    revisions, hashes, version_records, changed_later = [], {}, {}, []
    for item in snapshot:
        if not isinstance(item, dict):
            continue
        sha = str(item.get("sha256") or item.get("xml_sha256") or "")
        if (item.get("verified") is False or not _SHA.fullmatch(sha) or
                not _NOTICE_TYPE.fullmatch(str(item.get("xml_type") or ""))):
            continue
        declared_reg = str(item.get("reg_number") or item.get("reg") or "")
        if declared_reg and declared_reg != reg:
            continue
        scope_doc = {"meta": {key: item[key] for key in ("lot", "lot_number", "lot_id") if key in item}}
        scope = source_scope_context(p, scope_doc)
        if (not scope["eligible_actual"] or any(scope[key] and request_scope[key] and scope[key] != request_scope[key]
                                               for key in ("lots", "lot_ids"))):
            continue
        publication, value = _day(item.get("publication")), _day(item.get("submission_end"))
        if not publication or not value:
            return review("revision_incomplete", "В проверенной редакции отсутствует дата публикации или срок подачи; её применимость не установлена.", evidence)
        marker = (publication, value)
        if sha in hashes and hashes[sha] != marker:
            return review("conflict", "Один XML-оригинал представлен с противоречивыми датами.", evidence)
        if sha in hashes:
            continue
        hashes[sha] = marker
        record = {"kind": "verified_notice_revision", "sha256": sha, "version": str(item.get("version") or ""),
                  "publication": publication.isoformat(), "deadline": value.isoformat(),
                  "source_url": str(item.get("source_url") or "")}
        modified = _day(item.get("modified"))
        if modified:
            record["modified"] = modified.isoformat()
        if modified and publication <= received < modified:
            changed_later.append(record)
            continue
        version = item.get("version")
        if not isinstance(version, bool) and re.fullmatch(r"[0-9]{1,9}", str(version)):
            number = int(version)
            if number in version_records and version_records[number][:2] != (publication, value):
                return review("conflict", "Один номер редакции извещения содержит противоречивые сроки или даты публикации.",
                              evidence + [version_records[number][2], record])
            version_records[number] = (publication, value, record)
        revisions.append((publication, value, record))
    if changed_later and not text_values:
        return review("revision_modified_later", "Известная редакция XML изменена после получения запроса; прежний срок по ней не доказан.",
                      evidence + changed_later)
    ordered_versions = sorted(version_records)
    for earlier, later in zip(ordered_versions, ordered_versions[1:]):
        left_revision, right_revision = version_records[earlier], version_records[later]
        if right_revision[0] < left_revision[0]:
            return review("conflict", "Нумерация проверенных редакций противоречит порядку их публикации.",
                          evidence + [left_revision[2], right_revision[2]])
        if later > earlier + 1 and left_revision[0] < received <= right_revision[0]:
            return review("revision_gap", "В истории извещения есть пропущенная редакция, которая могла действовать на дату запроса.",
                          evidence + [left_revision[2], right_revision[2]])
    before = [item for item in revisions if item[0] < received]
    same_day = [item for item in revisions if item[0] == received]
    latest = max(item[0] for item in before) if before else None
    selected = [item for item in before if item[0] == latest]
    xml_values = {item[1] for item in selected}
    evidence.extend(item[2] for item in selected)
    if len(xml_values) > 1 or (text_values and xml_values and text_values != xml_values):
        return review("conflict", "Оригинал запроса и действовавшие редакции извещения содержат разные сроки подачи заявок.", evidence)
    values = text_values or xml_values
    if same_day and (not values or {item[1] for item in same_day} != values):
        evidence.extend(item[2] for item in same_day)
        return review("same_day_revision", "Изменение размещено в день получения запроса; по календарным датам действовавший срок не установлен однозначно.", evidence)
    if not values:
        if revisions:
            evidence.extend(item[2] for item in revisions[:10])
            return review("later_revision", "Найдена только редакция, опубликованная не ранее получения запроса; она не доказывает прежний срок.", evidence)
        return review("unproven", "Не подтверждён срок подачи заявок по извещению, действовавшему на дату этого запроса; текущей карточки недостаточно.", evidence)
    if (not text_values and current and current not in values and
            not any(publication > received and value == current for publication, value, _ in revisions)):
        return review("current_revision_unordered", "Срок в текущей карточке отличается; не доказано, что это изменение произошло после получения запроса.", evidence)
    return _result("confirmed", "", "Срок привязан к извещению, действовавшему на дату конкретного запроса.",
                   evidence, next(iter(values)), current)
