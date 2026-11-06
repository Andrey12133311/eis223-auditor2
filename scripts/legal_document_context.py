"""Source-role and passage context, independent of any legal conclusion.

A receipt proves where bytes came from; it does not turn a template, quoted
rule or superseded attachment into a current procurement act. These helpers
only limit claims. They never establish legal compliance or choose a revision
from its filename/date, and never mutate the original payload.
"""
from __future__ import annotations

import re
from bisect import bisect_right


_TEMPLATE = re.compile(r"\b(?:шаблон\w*|образец\w*|пример\w*)\b|"
                       r"\bформ[аы]\s+(?:заполнени\w*\s+)?протокол\w*", re.I)
_INSTRUCTION = re.compile(r"\b(?:инструкци\w*|методическ\w*\s+(?:рекомендаци\w*|указани\w*))\b|"
                          r"\b(?:порядок|правила)\s+(?:составлени\w*|заполнени\w*)\s+протокол\w*", re.I)
_PROJECT_CONTRACT = re.compile(r"\bпроект\w*\s+(?:договора|контракта)\b", re.I)
_DRAFT = re.compile(r"\b(?:проект\w*|черновик\w*)\s+(?:протокол\w*|извещени\w*|документаци\w*)\b", re.I)
_LOCAL_EXAMPLE = re.compile(
    r"\bпример\w*\s*(?:\d{1,3}\s*)?[:.]|"
    r"\bпример\w*\s+(?:заполнени\w*|формулировк\w*|нарушени\w*|оформлени\w*)\b|"
    r"\bобразец\w*\s+(?:протокол\w*|заполнени\w*|формулировк\w*)\b|\bшаблон\w*\b|"
    r"\b(?:как|в\s+качестве)\s+(?:пример\w*|образц\w*)\b|\bнапример\b|"
    r"\b(?:условн\w*|гипотетическ\w*|иллюстративн\w*)\s+(?:ситуаци\w*|формулировк\w*|текст\w*|случа\w*|образец\w*)\b|"
    r"\b(?:для\s+иллюстраци\w*|в\s+качестве\s+иллюстраци\w*|рассмотрим\s+ситуаци\w*)\b", re.I)
_LOCAL_QUOTE = re.compile(
    r"\b(?:цитата|цитируем\w*|цитируемая\s+норма|нормативн\w*\s+цитат\w*)\b|"
    r"\b(?:выдержка|фрагмент)\s+из\s+(?:закона|статьи|положения|нормативн\w*\s+акта)\b|"
    r"\b(?:в\s+законе\s+(?:указано|сказано)|закон\s+предусматривает\s+следующ\w*\s+формулировк\w*)", re.I)
_LOCAL_INSTRUCTION = re.compile(
    r"\b(?:не\s+следует|нельзя|не\s+допускается|запрещается)\s+"
    r"(?:оформлять|составлять|заполнять|писать|указывать|использовать\s+формулировк\w*)\b|"
    r"\b(?:правильн\w*|неправильн\w*|недопустим\w*)\s+(?:заполнени\w*|формулировк\w*)\b", re.I)
_LOCAL_HISTORICAL = re.compile(
    r"\bв\s+(?:ранее\s+)?отмен[её]нн\w*\s+протокол\w*|"
    r"\bцитат\w*\s+из\s+предыдущ\w*\s+редакци\w*", re.I)
_HISTORICAL_REQUIREMENT_SECTION = re.compile(
    r"\bв\s+(?:предыдущ\w*|прежн\w*|ранее\s+действовавш\w*|ранн\w*)\s+"
    r"редакци\w*\s+(?:документаци\w*|извещени\w*)\s+"
    r"(?:был[аои]?\s+)?(?:установлен\w*|предусмотрен\w*|указан\w*)\s*[:：]", re.I)
_CURRENT_REQUIREMENT_SECTION = re.compile(
    r"(?:^|[\n\r])\s*(?:(?:действующ\w*|текущ\w*|настоящ\w*)\s+редакци\w*\s+"
    r"(?:документаци\w*|извещени\w*)|требовани\w*\s+"
    r"(?:действующ\w*|текущ\w*|настоящ\w*)\s+(?:документаци\w*|извещени\w*))\s*[:：]", re.I)
_TEMPLATE_HEADING = re.compile(r"\b(?:шаблон\w*|образец\w*|форм[аы])\s+"
                               r"(?:(?:заполнени\w*|типов\w*)\s+)?протокол\w*", re.I)
_TEMPLATE_TITLE = re.compile(r"(?:^|[\[(–—-])\s*(?:\d+[.)]?\s*)?(?:шаблон\w*|образец\w*|пример\w*)\b|"
                             r"\bпротокол\w*.{0,100}\b(?:шаблон\w*|образец\w*)\b", re.I)
_INSTRUCTION_TITLE = re.compile(r"^\s*(?:\d+[.)]?\s*)?(?:инструкци\w*|методическ\w*\s+(?:рекомендаци\w*|указани\w*))\b|"
                                r"\b(?:порядок|правила|инструкци\w*)\s+(?:по\s+)?(?:составлени\w*|заполнени\w*)\s+протокол\w*", re.I)
_EXAMPLE_SECTION = re.compile(
    r"(?:^|[\n\r.!?]\s*)\s*(?:например[ \t]*:|(?:пример(?:\s+заполнени\w*)?|образец(?:\s+заполнени\w*)?|"
    r"шаблон\w*|цитата\s+из\s+закона|условн\w*\s+ситуаци\w*)[^\n:]{0,100}:)", re.I)
_EXAMPLE_END = re.compile(
    r"\b(?:пример|образец|иллюстрация)\s+(?:заверш[её]н[ао]?|окончен[ао]?)\b[.!:]?|"
    r"\bконец\s+(?:примера|образца|иллюстрации)\b[.!:]?", re.I)
_SENTENCE_END = re.compile(r"[!?;]|(?<!\d)\.(?!\d)|(?<=\d)\.(?!\d)")
_ACTUAL_SECTION = re.compile(
    r"(?:^|[\n\r])\s*(?:фактическ\w*\s+решени\w*\s+комисси\w*|решени\w*\s+комисси\w*|"
    r"результат\w*\s+рассмотрени\w*\s+заявок|основной\s+текст\s+протокола|"
    r"протокол\s*(?:№|N)\s*[\w/-]+)\s*[:.]?", re.I)
_LOT_HEADING = re.compile(r"(?:^|[\n\r])[ \t]*Лот\b[ \t]*(?:(?:№|N|номер)[ \t]*([\w/-]{1,40})|"
                          r"(\d{1,10})(?!\w))", re.I)
_VARIANT_HEADING = re.compile(
    r"(?:^|[\n\r])[ \t]*(?:редакция|вариант)[ \t]*(?:№|N)?[ \t]*(\d{1,3})(?!\d)|"
    r"\([ \t]*(?:редакция|вариант)[ \t]*(?:№|N)?[ \t]*(\d{1,3})(?!\d)[ \t]*\)", re.I)
_VARIANT_SELECTION = re.compile(
    r"(?:^|[\n\r])[ \t]*(?:(?:для\s+(?:настоящего\s+договора|данной\s+закупки))\s+)?"
    r"(?:применяется|выбрана|выбран)\s+(?:редакция|вариант)\s*(?:№|N)?\s*(\d{1,3})\s*(?:[.:;]|$)", re.I)
_NUMBERED_SECTION = re.compile(
    r"(?:^|[\n\r])[ \t]*\d{1,2}(?:\.\d{1,2})*[.)]?[ \t]+(?P<title>[А-ЯЁA-Z][^\n\r]{2,140})(?=\r?\n|$)", re.I)
_SUBJECT_SECTION = re.compile(
    r"(?:^|[\n\r])[ \t]*(?:обеспечение\s+(?:заявки|исполнения\s+договора)|"
    r"порядок\s+расч[её]тов|(?:сроки|условия)\s+оплаты|состав\s+заявки|требования\s+к\s+участникам)\s*[:.]?(?=\r?\n|$)", re.I)
_CONTRACT_HEADING = re.compile(
    r"(?:^|[\n\r])[ \t]*(?:проект\s+)?(?:договор|контракт)\s*(?:№|N)\s*[^\s,;]+", re.I)
_FOLLOWING_EXPLANATION = re.compile(
    r"[\n\r][ \t]*(?:пример(?:\s+заполнени\w*|\s+формулировк\w*)?|например|"
    r"образец\w*|шаблон\w*|цитата\s+из\s+закона)\b[^\n\r]*", re.I)
_BACKWARD_REFERENCE = re.compile(
    r"\b(?:выше|предыдущ\w*|(?:данн\w*|этот|эта|привед[её]нн\w*|указанн\w*)\s+"
    r"(?:текст|услови\w*|формулировк\w*|фрагмент\w*))\b", re.I)

REASON_LABELS = {
    "missing_document": "нет исходного документа",
    "template_or_example": "образец или шаблон",
    "instruction_source": "инструкция по составлению документа",
    "project_is_not_completed_act": "проект договора не подтверждает совершённое действие",
    "draft_source": "проект или черновик документа",
    "explicitly_superseded_revision": "редакция явно помечена как заменённая",
    "explicitly_cancelled_document": "документ явно помечен как отменённый",
    "invalid_fragment": "не определён фрагмент документа",
    "illustrative_passage": "иллюстративный или условный пример",
    "quoted_norm": "цитируется нормативная формулировка",
    "instruction_example": "инструкция о недопустимом оформлении",
    "historical_reference": "ссылка на прежнюю редакцию документа или прежний протокол",
    "source_other_purchase": "реквизиты источника относятся к другой закупке",
    "source_other_lot": "реквизиты источника относятся к другому лоту",
    "source_lot_ambiguous": "лот исходного условия не определён однозначно",
    "alternative_not_selected": "для этого раздела прямо выбран другой вариант условия",
    "alternative_unselected": "применимый вариант из альтернативных редакций не установлен",
}


def _true(value):
    return value is True or (isinstance(value, str) and value.strip().lower() in {"true", "1", "yes", "да"})


def document_context(doc):
    """Describe source role and explicit revision limitations.

    ``eligible_actual`` is only a contextual permission to assess an actual
    act/requirement. Callers still need receipt, scope, proof and rule checks.
    Regulations/protocols remain distinct roles; a tender-requirement rule must
    choose the relevant role itself. A draft contract is a contract-term source,
    never evidence that a contract was signed or a bid actually rejected.
    """
    if not isinstance(doc, dict):
        return {"role": "unknown", "eligible_actual": False,
                "version_status": "unknown", "reasons": ["missing_document"]}
    meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
    title = " ".join(str(value or "") for value in (doc.get("name"), meta.get("filename"))).replace("_", " ")
    body = str(doc.get("text") or "").replace("\xa0", " ")
    heading = body.lstrip()[:450]
    labels = title + " " + heading
    role = "unknown"
    reasons = []
    if _TEMPLATE_TITLE.search(title) or _TEMPLATE_HEADING.search(title) or _TEMPLATE.match(heading) or _TEMPLATE_HEADING.search(heading):
        role = "template"
        reasons.append("template_or_example")
    elif _INSTRUCTION_TITLE.search(title) or _INSTRUCTION.match(heading):
        role = "instruction"
        reasons.append("instruction_source")
    elif _PROJECT_CONTRACT.search(title) or _PROJECT_CONTRACT.match(heading):
        role = "project_contract"
        reasons.append("project_is_not_completed_act")
    elif _DRAFT.search(title) or _DRAFT.match(heading):
        role = "draft"
        reasons.append("draft_source")
    elif re.search(r"положени\w*\s+о\s+закупк", labels, re.I):
        role = "regulation"
    elif re.search(r"\bпротокол\w*", title, re.I) or re.match(r"протокол\w*\s", heading, re.I):
        role = "protocol"
    elif re.search(r"документаци\w*|техническ\w*\s+задани\w*", labels, re.I):
        role = "documentation"
    elif re.search(r"извещени\w*", labels, re.I):
        role = "notice"
    elif re.search(r"\b(?:договор|контракт)\b", labels, re.I):
        role = "contract"
    version_status = "unspecified"
    explicitly_current = False
    for record in (doc, meta):
        declared = str(record.get("version_status") or record.get("revision_status") or
                       record.get("document_status") or "").strip().lower()
        if (declared in {"current", "active", "effective", "действующий", "актуальная"} or
                _true(record.get("is_current_version")) or _true(record.get("is_current_revision")) or
                _true(record.get("is_current"))):
            explicitly_current = True
        if (_true(record.get("superseded")) or _true(record.get("is_superseded")) or
                _true(record.get("obsolete")) or _true(record.get("is_obsolete")) or
                declared in {"superseded", "obsolete", "replaced", "утратил силу", "заменен", "заменён"} or
                record.get("is_current_version") is False or record.get("is_current_revision") is False or
                record.get("is_current") is False):
            version_status = "superseded"
            reasons.append("explicitly_superseded_revision")
        if (_true(record.get("document_cancelled")) or _true(record.get("is_cancelled")) or
                _true(record.get("cancelled")) or _true(record.get("canceled")) or
                declared in {"cancelled", "canceled", "аннулирован", "отменен", "отменён"}):
            version_status = "cancelled"
            reasons.append("explicitly_cancelled_document")
    if version_status == "unspecified" and explicitly_current:
        version_status = "current"
    reasons = list(dict.fromkeys(reasons))
    return {"role": role, "eligible_actual": not reasons,
            "version_status": version_status, "reasons": reasons,
            "reason_labels": [REASON_LABELS[value] for value in reasons]}


def _scope_values(records, keys):
    values = set()
    for record in records:
        for key in keys:
            raw = record.get(key)
            if raw is None or isinstance(raw, (dict, list, tuple, bool)):
                continue
            value = re.sub(r"^(?:№\s*|N(?=\s*\d)\s*)", "", str(raw).strip(), flags=re.I).casefold().rstrip(".;)")
            if value and set(value) - set("_-/ "):
                values.add(str(int(value)) if value.isdecimal() else value)
    return values


def prepare_context_index(text):
    """Prepare bounded source headings once for many clause/scope candidates."""
    body = str(text or "")
    lots = list(_LOT_HEADING.finditer(body))
    variants = list(_VARIANT_HEADING.finditer(body))
    selections = list(_VARIANT_SELECTION.finditer(body))
    boundaries = [match.start() for pattern in (_CONTRACT_HEADING, _SUBJECT_SECTION)
                  for match in pattern.finditer(body)]
    boundaries.extend(match.start() for match in lots)
    boundaries.extend(match.start() for match in _NUMBERED_SECTION.finditer(body)
                      if not re.search(r"[;!?]|\d|\b(?:составляет|установлен\w*|требуется|производ\w*)\b",
                                       match["title"], re.I))
    return {"lots": lots, "lot_ends": [match.end() for match in lots],
            "variants": variants, "variant_starts": [match.start() for match in variants],
            "selections": selections, "selection_starts": [match.start() for match in selections],
            "boundaries": sorted(set(boundaries) | {0, len(body)})}


def source_scope_context(purchase, doc, position=None, *, context_index=None):
    """Bind explicit source identities; filenames and dates never select a lot.

    Missing identities do not prove a mismatch. Callers joining two scoped
    sources must additionally establish a common lot; this helper only reports
    explicit identities and vetoes contradictions with the selected purchase.
    """
    purchase = purchase if isinstance(purchase, dict) else {}
    doc = doc if isinstance(doc, dict) else {}
    meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
    requested = _scope_values((purchase,), ("reg", "reg_number"))
    declared = _scope_values((doc, meta), ("reg_number", "purchase_reg_number", "procurement_reg_number"))
    requested_lots = _scope_values((purchase,), ("lot_number", "lot"))
    requested_ids = _scope_values((purchase,), ("lot_id",))
    lots = _scope_values((doc, meta), ("lot_number", "lot"))
    lot_ids = _scope_values((doc, meta), ("lot_id",))
    body = str(doc.get("text") or "")[:750000]
    headings = list(_LOT_HEADING.finditer(body)) if context_index is None else context_index["lots"]
    if position is not None:
        offset = bisect_right([heading.end() for heading in headings] if context_index is None
                              else context_index["lot_ends"], position)
        headings = headings[offset-1:offset] if offset else []
    lots.update(_scope_values(({"lot": heading[1] or heading[2]} for heading in headings), ("lot",)))
    reasons = []
    if requested and declared and requested != declared:
        reasons.append("source_other_purchase")
    if ((requested_lots and lots and requested_lots != lots) or
            (requested_ids and lot_ids and requested_ids != lot_ids)):
        reasons.append("source_other_lot")
    if len(lots) > 1 or len(lot_ids) > 1 or len(requested_lots) > 1 or len(requested_ids) > 1:
        reasons.append("source_lot_ambiguous")
    return {"eligible_actual": not reasons, "reasons": reasons,
            "reason_labels": [REASON_LABELS[value] for value in reasons],
            "purchase_ids": sorted(declared), "lots": sorted(lots), "lot_ids": sorted(lot_ids),
            "requested_lots": sorted(requested_lots), "requested_lot_ids": sorted(requested_ids)}


def clause_variant_context(text, start, end, *, context_index=None):
    """An explicit selection applies only within the same lot/section frame.

    This limiter never infers which revision is newer. Parenthesized variants
    and numbered alternative headings are not independently operative terms.
    """
    body = str(text or "")
    index = prepare_context_index(body) if context_index is None else context_index
    boundaries = index["boundaries"]
    section = min(bisect_right(boundaries, start), len(boundaries)-1)
    left, right = boundaries[max(0, section-1)], boundaries[section]
    variants = index["variants"][bisect_right(index["variant_starts"], left-1):
                                  bisect_right(index["variant_starts"], right-1)]
    preceding = [match for match in variants if match.end() <= end]
    current = (preceding[-1][1] or preceding[-1][2]) if preceding else ""
    selected = index["selections"][bisect_right(index["selection_starts"], left-1):
                                    bisect_right(index["selection_starts"], right-1)]
    selections = {match[1] for match in selected}
    numbers = {match[1] or match[2] for match in variants}
    reasons = []
    if current and len(selections) == 1 and current not in selections:
        reasons.append("alternative_not_selected")
    elif current and (len(selections) > 1 or not selections and (len(numbers) > 1 or "(" in preceding[-1][0])):
        reasons.append("alternative_unselected")
    return {"eligible_actual": not reasons, "reasons": reasons,
            "reason_labels": [REASON_LABELS[value] for value in reasons],
            "variant": current, "selected_variants": sorted(selections),
            "variants": sorted(numbers), "section_start": left, "section_end": right}


def local_context(text, start, end):
    """Assess nearby explanatory context without treating every condition as fiction.

    Prospective procurement clauses using ``если``/``при непредставлении`` and
    quotes of an actual commission decision remain assessable. Only explicit
    example/norm quotation/instruction/history markers restrict the claim.
    Paragraph boundaries keep an unrelated example from suppressing a later act.
    """
    body = str(text or "")
    start, end = max(0, int(start)), min(len(body), int(end))
    if start > end or start >= len(body):
        return {"eligible_actual": False, "reasons": ["invalid_fragment"],
                "excerpt": "", "start": start, "end": end}
    left = max(0, start-260)
    right = min(len(body), end+260)
    before = body.rfind("\n\n", left, start)
    if before >= 0:
        left = before+2
    after = body.find("\n\n", end, right)
    if after >= 0:
        right = after
    # DOCX paragraphs commonly arrive as single line breaks. An independent
    # following example/form heading does not qualify the preceding operative
    # requirement. A backward reference still annotates the quoted condition.
    following = _FOLLOWING_EXPLANATION.search(body, end, right)
    if following and not _BACKWARD_REFERENCE.search(following[0]):
        right = following.start()
    excerpt = body[left:right]
    reasons = [reason for pattern, reason in (
        (_LOCAL_QUOTE, "quoted_norm"),
        (_LOCAL_INSTRUCTION, "instruction_example"),
        (_LOCAL_HISTORICAL, "historical_reference"),
    ) if pattern.search(excerpt)]
    # An explicit example section can span several paragraphs/pages. Retain
    # that scope until a following actual-act heading; do not inspect an
    # unbounded whole document or infer a section from an isolated date.
    prefix_start = max(0, start-5000)
    preceding = body[prefix_start:start]
    completed = list(_EXAMPLE_END.finditer(preceding))
    example_floor = prefix_start + completed[-1].end() if completed else 0
    for marker in _LOCAL_EXAMPLE.finditer(excerpt):
        marker_start, marker_end = left + marker.start(), left + marker.end()
        if marker_start < example_floor and marker_end <= start:
            continue
        if marker[0].casefold() == "например" and marker_end <= start:
            following = body[marker_end:start]
            # A completed inline example qualifies its own sentence. An
            # explicit colon heading still opens a section spanning paragraphs.
            if re.match(r"[ \t]*,", following) and _SENTENCE_END.search(following):
                continue
        reasons.append("illustrative_passage")
        break
    headings = list(_EXAMPLE_SECTION.finditer(preceding,
                    max(0, example_floor-prefix_start)))
    context_heading = ""
    if headings:
        last = headings[-1]
        if not _ACTUAL_SECTION.search(preceding[last.end():]):
            context_heading = last.group(0).lstrip("\r\n.!? ")
            reasons.append("quoted_norm" if "цитата" in context_heading.casefold() else "illustrative_passage")
    # A documentation attachment may quote an obsolete requirement before
    # stating its current text. An explicit former-edition introduction is
    # historical context, not an operative requirement of this procurement.
    historical = list(_HISTORICAL_REQUIREMENT_SECTION.finditer(preceding))
    if historical:
        last = historical[-1]
        if not _CURRENT_REQUIREMENT_SECTION.search(preceding[last.end():]):
            context_heading = last.group(0)
            reasons.append("historical_reference")
    reasons = list(dict.fromkeys(reasons))
    return {"eligible_actual": not reasons, "reasons": reasons,
            "reason_labels": [REASON_LABELS[value] for value in reasons],
            "context_heading": context_heading,
            "excerpt": excerpt, "start": left, "end": right}
