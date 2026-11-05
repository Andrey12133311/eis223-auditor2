"""Closed, source-backed SME evaluation-document requirement check.

Article 3.4(19.2) permits information/documents for evaluation, but their
absence cannot ground bid rejection. This rule establishes only an explicit
documentation requirement; it never infers a rejected bid from a draft clause.
The provision has applied since 2021-04-01 (Federal Law 452-FZ of 2020-12-22).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from functools import wraps
import json
import os
import re

from legal_applicability import competitive_rule_scope
from v331_broad_legal import verified
from v383_msp_scope import SME, resolve as resolve_sme
from v341_eis_facts import parse_day

CODE = "223-3.4-19.2-SME-EVALUATION-DOCS-REJECTION"
LAW = "223-ФЗ, ст. 3.4 ч. 19.2"
LAW_URL = "https://www.consultant.ru/document/cons_doc_LAW_116964/a3b63487ad7d07ce045a07b0f1e7a9d9b064de95/"
EFFECTIVE_FROM = date(2021, 4, 1)
MAX_DOCUMENTS = 650
MAX_TEXT = 750000

ABSENCE = re.compile(r"\b(?:непредставлени[еяи]|непредоставлени[еяи]|отсутстви[еяи])\b", re.I)
DOCUMENTS = re.compile(r"\b(?:документ\w*|сведени\w*|информаци\w*)\b", re.I)
EVALUATION = re.compile(
    r"\b(?:для\s+(?:осуществлени\w*\s+)?|"
    r"(?:представляем\w*|используем\w*)\s+в\s+целях\s+)"
    r"(?:оценки(?:\s+и\s+сопоставления)?|сопоставления)\s+заявк\w*\b", re.I)
REJECTION = re.compile(
    r"(?:\b(?:является|являются|служит|служат|может\s+являться)\s+"
    r"основани\w*\s+для\s+(?:отклонени\w*\s+заявк\w*|отказа\s+в\s+допуске)|"
    r"\b(?:влеч[её]т|влекут|приводит\s+к)\s+(?:отклонени\w*\s+заявк\w*|отказ\w*\s+в\s+допуске)|"
    r"\bзаявк\w*(?:\s+участник\w*)?\s+(?:подлеж\w*\s+отклонени\w*|отклоня\w*)|"
    r"\bучастник\w*\s+не\s+допуска\w*\s+к\s+участию)", re.I)
NO_REJECTION = re.compile(
    r"(?:\bне\s+(?:(?:может|могут)\s+)?(?:явля\w*|служ\w*)\s+основани\w*|"
    r"\bне\s+(?:влеч[её]т|влекут)\s+отклонени\w*|"
    r"\bзаявк\w*(?:\s+участник\w*)?\s+не\s+(?:подлеж\w*\s+отклонени\w*|отклоня\w*)|"
    r"\bне\s+допускается\s+отклонени\w*|"
    r"\b(?:присваива\w*|получа\w*)\s+(?:0|ноль|нул\w*)\s+балл\w*)", re.I)
SCORING_NEXUS = re.compile(
    r"\b(?:по|для|в\s+рамках|при\s+применении)\s+"
    r"(?:(?:неценов\w*|оценочн\w*)\s+)?критери\w*|"
    r"\b(?:неценов\w*|оценочн\w*)\s+критери\w*|"
    r"\b(?:начислени\w*|присвоени\w*|расч[её]т\w*)\s+балл\w*", re.I)
ELIGIBILITY_NEXUS = re.compile(
    r"\bлицензи\w*|\bобязательн\w*|\bСРО\b|"
    r"\bчленств\w*\s+в\s+саморегулируем\w*|"
    r"\bсоответстви\w*\s+(?:установленн\w*\s+)?требовани\w*|"
    r"\b(?:допуск\w*|правоспособност\w*)\s+(?:к\s+участию|участник\w*)", re.I)
SUBJECT_SWITCH = re.compile(r"\b(?:однако|но|при\s+этом|вместе\s+с\s+тем|за\s+исключением)\b", re.I)
ALLOWED_ROLES = frozenset({"documentation", "notice"})
EXCLUDED_ROLES = frozenset({"template", "instruction", "draft", "project_contract",
                            "regulation", "protocol", "contract"})


def _text(value):
    return " ".join(str(value or "").replace("\xa0", " ").split())


def _card(p):
    value = p.get("card_fields_json") or {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {}
    return value if isinstance(value, dict) else {}


def method_scope(p):
    """Only electronic contests/proposals; generic quotations cannot set method."""
    method = _text(p.get("method")).casefold().replace("ё", "е")
    contest = bool(re.search(r"\bконкурс\b", method))
    proposal = bool(re.search(r"\bзапрос\s+предложени\w*", method))
    other = bool(re.search(r"\bаукцион\w*|\bзапрос\s+котировок", method))
    if competitive_rule_scope(p) is not True or contest == proposal or other:
        return False, "Не подтверждён применимый способ конкурса или запроса предложений"
    secondary = _text(p.get("purchase_method")).casefold().replace("ё", "е")
    if re.search(r"\bнеэлектронн\w*|\bбумажн\w*|\bне\s+(?:в\s+)?электронн\w*", method + " " + secondary):
        return False, "Электронная форма закупки не подтверждена: указана бумажная или неэлектронная форма"
    if secondary:
        secondary_contest = bool(re.search(r"\bконкурс\b", secondary))
        secondary_proposal = bool(re.search(r"\bзапрос\s+предложени\w*", secondary))
        secondary_other = bool(re.search(r"\bаукцион\w*|\bзапрос\s+котировок", secondary))
        if (secondary_other or secondary_contest and not contest
                or secondary_proposal and not proposal):
            return False, "Сведения о способе закупки противоречат друг другу"
    electronic = bool(re.search(r"\bэлектронн\w*", method))
    xml = _card(p).get("soap_metadata") or {}
    expected = "purchaseNoticeKESMBO" if contest else "purchaseNoticeZPESMBO"
    if (isinstance(xml, dict) and xml.get("source") == "official_eis_getDocsIP"
            and xml.get("xml_type") in {"purchaseNoticeKESMBO", "purchaseNoticeZPESMBO",
                                        "purchaseNoticeAESMBO", "purchaseNoticeZKESMBO"}
            and xml.get("xml_type") != expected):
        return False, "Официальный тип извещения противоречит заявленному способу закупки"
    if (isinstance(xml, dict) and xml.get("source") == "official_eis_getDocsIP"
            and xml.get("xml_type") == expected
            and re.fullmatch(r"[0-9a-fA-F]{64}", str(xml.get("xml_sha256") or ""))):
        electronic = True
    if not electronic:
        return False, "Электронная форма применимого способа не подтверждена"
    return True, ""


def _clauses(text):
    """Keep ordinary line wraps, but never combine separate numbered paragraphs."""
    start = 0
    for index, char in enumerate(text):
        decimal = (char == "." and index and index+1 < len(text)
                   and text[index-1].isdigit() and text[index+1].isdigit())
        paragraph = (char in "\n\r" and
                     (index+1 < len(text) and text[index+1] in "\n\r"
                      or re.match(r"\s*\d+(?:[.]\d+)*[.)]\s", text[index+1:index+35])))
        if (char in ".;!?" and not decimal) or paragraph:
            yield start, index+1, text[start:index+1]
            start = index+1
    if start < len(text):
        yield start, len(text), text[start:]


def _candidates(text):
    lowered = text.casefold()
    if not any(value in lowered for value in ("непредставлен", "непредоставлен", "отсутстви")):
        return
    for start, end, clause in _clauses(text):
        if len(clause) > 2400 or NO_REJECTION.search(clause):
            continue
        absent = ABSENCE.search(clause)
        documents = DOCUMENTS.search(clause, absent.end()) if absent else None
        evaluation = EVALUATION.search(clause, documents.end()) if documents else None
        rejection = REJECTION.search(clause, evaluation.end()) if evaluation else None
        if not all((absent, documents, evaluation, rejection)):
            continue
        if rejection.end()-absent.start() > 700:
            continue
        yield start+absent.start(), start+rejection.end(), text[start+absent.start():start+rejection.end()]


def _identity(doc):
    meta = doc.get("meta") or {}
    return (str(meta.get("sha256") or ""), str(meta.get("archive_member") or meta.get("member") or ""),
            str(meta.get("source_url") or doc.get("url") or ""))


def _sme_purchase(p):
    """Do not inherit the legacy resolver's permissive XML hash premise."""
    card = dict(_card(p))
    facts = card.get("fact_evidence") or {}
    fact = facts.get("sme_only") if isinstance(facts, dict) else None
    card["fact_evidence"] = {"sme_only": fact} if isinstance(fact, dict) else {}
    xml = card.get("soap_metadata") or {}
    if (not isinstance(xml, dict) or xml.get("source") != "official_eis_getDocsIP"
            or not re.fullmatch(r"[0-9a-fA-F]{64}", str(xml.get("xml_sha256") or ""))):
        card = {key: value for key, value in card.items() if key != "soap_metadata"}
    facts = p.get("fact_evidence") or {}
    fact = facts.get("sme_only") if isinstance(facts, dict) else None
    return {**p, "card_fields_json": card,
            "fact_evidence": {"sme_only": fact} if isinstance(fact, dict) else {}}


def _belongs_to_purchase(p, doc):
    requested = str(p.get("reg_number") or p.get("reg") or "").strip()
    meta = doc.get("meta") or {}
    declared = str(meta.get("reg_number") or meta.get("purchase_reg_number") or
                   doc.get("reg_number") or "").strip()
    return not (requested and declared and requested != declared)


def _publication_scope(p):
    values = [p.get(key) for key in ("published", "published_at") if p.get(key)]
    parsed = [parse_day(value) for value in values]
    if not parsed or any(value is None for value in parsed):
        return None, "Дата закупки для определения редакции нормы не подтверждена"
    if len(set(parsed)) > 1:
        return None, "Сохранены противоречивые даты публикации извещения; редакция нормы не определена"
    return parsed[0], ""


def _requirement_reasons(quote):
    """Optional scoring cannot be inferred from generic admission evaluation."""
    reasons = []
    if not SCORING_NEXUS.search(quote):
        reasons.append("Не подтверждено, что документы запрошены именно для оценки по критерию, а не для допуска")
    if ELIGIBILITY_NEXUS.search(quote):
        reasons.append("Фрагмент содержит обязательные или допусковые требования; нужна проверка связи с оценкой по ч. 19.2")
    evaluation = EVALUATION.search(quote)
    after_evaluation = quote[evaluation.end():] if evaluation else quote
    if (SUBJECT_SWITCH.search(quote) or len(ABSENCE.findall(quote)) > 1
            or re.search(r"\bне\b", after_evaluation, re.I)):
        reasons.append("Нужно подтвердить, что отклонение связано с отсутствием тех же документов для оценки")
    return reasons


def _version_groups(documents, contexts):
    groups = defaultdict(list)
    for doc in documents:
        if contexts[id(doc)].get("role") not in ALLOWED_ROLES:
            continue
        meta = doc.get("meta") or {}
        name = os.path.basename(str(doc.get("name") or "").replace("\\", "/")).casefold()
        name = name.replace("_", " ")
        # Only group potential revisions; never infer their order/currentness
        # from a filename number or date. Even a declared current flag cannot
        # by itself bind these different originals to the active EIS edition.
        name = os.path.splitext(name)[0]
        name = re.sub(r"(?<!\w)(?:v|версия|редакция|ред\.)[\s_№-]*\d+(?:\.\d+)*", "", name)
        name = re.sub(r"\b(?:от\s*)?(?:\d{1,2}\.\d{1,2}\.\d{4}|\d{4}-\d{2}-\d{2})\b", "", name)
        name = re.sub(r"[\W_]+", "", name)
        if name:
            lot = str(meta.get("lot_number") or meta.get("lot") or "")
            groups[(name, lot)].append(doc)
    unresolved = {}
    for members in groups.values():
        if len({_identity(doc) for doc in members}) < 2:
            continue
        for doc in members:
            # A current=True flag is a declaration, not independent evidence
            # binding the active EIS revision to these particular source bytes.
            unresolved[id(doc)] = "Получены разные оригиналы с одинаковым именем; действующая редакция не определена"
    return unresolved


def _record(doc, quote, offset, *, reasons=()):
    meta = doc.get("meta") or {}
    title = "Отсутствие документов для оценки заявки МСП объявлено основанием отклонения"
    result = {
        "code": CODE, "title": title, "law": LAW, "law_url": LAW_URL,
        "document": str(doc.get("name") or ""),
        "source_url": str(meta.get("source_url") or doc.get("url") or ""),
        "evidence": "Условие документации: " + quote,
        "source_excerpt": quote, "match_text": quote,
        "source_position": offset,
        "location": "Оригинал ЕИС, позиция " + str(offset+1),
        "criterion_keys": ["sme_application_requirements"],
        "legal_fact_type": "document_requirement",
        "decision_kind": "document_requirement",
        "note": ("Проверяется требование к документам для оценки заявки, а не факт отклонения конкретной заявки. "
                 "Отсутствие документов для оценки не является основанием отклонения по ч. 19.2 ст. 3.4. "
                 + ("; ".join(reasons) if reasons else
                    "Подтверждены оригинал, режим МСП, электронный конкурс/запрос предложений и дата применения нормы.")),
    }
    if reasons:
        result.update(status="check", field_path=CODE, item=title,
                      scope="223-ФЗ / состав заявки МСП", source=result["document"])
    else:
        result["kind"] = "violation"
    return result


def _scope_documents(p, documents, contexts, versions, local_context):
    sources = []
    for doc in documents:
        context = contexts[id(doc)]
        if (context.get("role") not in ALLOWED_ROLES or
                not context.get("eligible_actual") or id(doc) in versions or
                not _belongs_to_purchase(p, doc)):
            continue
        body = str(doc.get("text") or "")[:MAX_TEXT]
        restricted = []
        for match in re.finditer(SME, body, re.I):
            passage = local_context(body, match.start(), match.end())
            if not passage.get("eligible_actual"):
                restricted.append((passage["start"], passage["end"]))
        if restricted:
            # Preserve offsets and source identity; do not let a quoted/example
            # SME declaration become an affirmative applicability premise.
            spans = sorted(restricted)
            chars = list(body)
            for start, end in spans:
                chars[start:end] = " " * (end-start)
            sources.append({**doc, "text": "".join(chars)})
        else:
            sources.append(doc)
    return sources


def evaluate(p, docs):
    """Return explicit unlawful requirements and unresolved source-backed signals."""
    from legal_document_context import document_context, local_context

    documents = [doc for doc in list(docs or ())[:MAX_DOCUMENTS]
                 if isinstance(doc, dict) and isinstance(doc.get("meta"), dict) and verified(doc)]
    contexts = {id(doc): document_context(doc) for doc in documents}
    versions = _version_groups(documents, contexts)
    # A verified receipt does not make a template, old revision or regulation a
    # declaration about this procurement. Only active tender sources can add
    # textual SME premises; saved official card/method facts remain available.
    scope_documents = _scope_documents(p, documents, contexts, versions, local_context)
    sme = resolve_sme(_sme_purchase(p), scope_documents)
    method_ok, method_reason = method_scope(p)
    published, publication_reason = _publication_scope(p)
    findings, checks, seen = [], [], set()
    for doc in documents:
        context = contexts[id(doc)]
        role = context.get("role", "unknown")
        if role in EXCLUDED_ROLES:
            continue
        body = str(doc.get("text") or "")[:MAX_TEXT]
        for start, end, quote in _candidates(body):
            local = local_context(body, start, end)
            if not local.get("eligible_actual"):
                continue
            identity = (_identity(doc), _text(quote))
            if identity in seen:
                continue
            seen.add(identity)
            reasons = _requirement_reasons(quote)
            if not _belongs_to_purchase(p, doc):
                reasons.append("Реквизиты оригинала указывают на другую закупку")
            if role not in ALLOWED_ROLES:
                reasons.append("Не подтверждено, что источник устанавливает требования документации именно этой закупки")
            if not context.get("eligible_actual"):
                reasons.extend(context.get("reason_labels") or context.get("reasons") or
                               ["Источник не подтверждён как действующий документ закупки"])
            if id(doc) in versions:
                reasons.append(versions[id(doc)])
            if sme.get("value") is False:
                # A known nonexclusive procurement cannot establish this SME rule.
                continue
            if sme.get("value") is not True:
                reasons.append("Не подтверждено участие только субъектов МСП")
            if not method_ok:
                reasons.append(method_reason)
            if published is None:
                reasons.append(publication_reason)
            elif published < EFFECTIVE_FROM:
                reasons.append("Нужна историческая редакция: ч. 19.2 действует с 01.04.2021")
            item = _record(doc, quote, start, reasons=tuple(dict.fromkeys(reasons)))
            (checks if reasons else findings).append(item)
    return findings, checks


def install(ns):
    analyze, checker = ns.get("analyze"), ns.get("build_checks")
    if not callable(analyze) or not callable(checker):
        raise RuntimeError("SME application review requires the shared legal engine")
    if getattr(analyze, "__vp_sme_application__", False):
        return

    @wraps(analyze)
    def analyze_sme(p, docs):
        previous = list(analyze(p, docs) or ())
        findings, _ = evaluate(p, docs)
        existing = {(item.get("code"), item.get("document"), item.get("match_text"))
                    for item in previous if isinstance(item, dict)}
        return previous + [item for item in findings
                           if (item["code"], item["document"], item["match_text"]) not in existing]

    @wraps(checker)
    def checks_sme(p, detail, card, docs):
        previous = list(checker(p, detail, card, docs) or ())
        _, checks = evaluate(p, docs)
        existing = {(item.get("field_path"), item.get("document"), item.get("match_text"))
                    for item in previous if isinstance(item, dict)}
        return previous + [item for item in checks
                           if (item["field_path"], item["document"], item["match_text"]) not in existing]

    analyze_sme.__vp_sme_application__ = True
    ns["analyze"], ns["build_checks"] = analyze_sme, checks_sme
    print("VP_SME_APPLICATION_READY evaluation_docs_vs_admission=True source_required=True", flush=True)
