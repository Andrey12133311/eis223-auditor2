"""Source-role and passage context, independent of any legal conclusion.

A receipt proves where bytes came from; it does not turn a template, quoted
rule or superseded attachment into a current procurement act. These helpers
only limit claims. They never establish legal compliance or choose a revision
from its filename/date, and never mutate the original payload.
"""
from __future__ import annotations

import re


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
_TEMPLATE_HEADING = re.compile(r"\b(?:шаблон\w*|образец\w*|форм[аы])\s+"
                               r"(?:(?:заполнени\w*|типов\w*)\s+)?протокол\w*", re.I)
_TEMPLATE_TITLE = re.compile(r"(?:^|[\[(–—-])\s*(?:\d+[.)]?\s*)?(?:шаблон\w*|образец\w*|пример\w*)\b|"
                             r"\bпротокол\w*.{0,100}\b(?:шаблон\w*|образец\w*)\b", re.I)
_INSTRUCTION_TITLE = re.compile(r"^\s*(?:\d+[.)]?\s*)?(?:инструкци\w*|методическ\w*\s+(?:рекомендаци\w*|указани\w*))\b|"
                                r"\b(?:порядок|правила|инструкци\w*)\s+(?:по\s+)?(?:составлени\w*|заполнени\w*)\s+протокол\w*", re.I)
_EXAMPLE_SECTION = re.compile(
    r"(?:^|[\n\r.!?]\s*)\s*(?:пример(?:\s+заполнени\w*)?|образец(?:\s+заполнени\w*)?|"
    r"шаблон\w*|цитата\s+из\s+закона|условн\w*\s+ситуаци\w*)[^\n:]{0,100}:", re.I)
_ACTUAL_SECTION = re.compile(
    r"(?:^|[\n\r])\s*(?:фактическ\w*\s+решени\w*\s+комисси\w*|решени\w*\s+комисси\w*|"
    r"результат\w*\s+рассмотрени\w*\s+заявок|основной\s+текст\s+протокола|"
    r"протокол\s*(?:№|N)\s*[\w/-]+)\s*[:.]?", re.I)

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
    "historical_reference": "ссылка на отменённую редакцию или прежний протокол",
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
    excerpt = body[left:right]
    reasons = [reason for pattern, reason in (
        (_LOCAL_EXAMPLE, "illustrative_passage"),
        (_LOCAL_QUOTE, "quoted_norm"),
        (_LOCAL_INSTRUCTION, "instruction_example"),
        (_LOCAL_HISTORICAL, "historical_reference"),
    ) if pattern.search(excerpt)]
    # An explicit example section can span several paragraphs/pages. Retain
    # that scope until a following actual-act heading; do not inspect an
    # unbounded whole document or infer a section from an isolated date.
    preceding = body[max(0, start-5000):start]
    headings = list(_EXAMPLE_SECTION.finditer(preceding))
    context_heading = ""
    if headings:
        last = headings[-1]
        if not _ACTUAL_SECTION.search(preceding[last.end():]):
            context_heading = last.group(0).lstrip("\r\n.!? ")
            reasons.append("quoted_norm" if "цитата" in context_heading.casefold() else "illustrative_passage")
    reasons = list(dict.fromkeys(reasons))
    return {"eligible_actual": not reasons, "reasons": reasons,
            "reason_labels": [REASON_LABELS[value] for value in reasons],
            "context_heading": context_heading,
            "excerpt": excerpt, "start": left, "end": right}
