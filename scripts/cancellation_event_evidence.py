"""Bind cancellation facts to one bounded, applicable act in an EIS original.

This establishes an evidence chain, not the legality of the cancellation itself.
Unresolved identities/dates are retained for review, never interpreted as timely.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import re

from legal_applicability import competitive_rule_scope
from legal_document_context import document_context, local_context
from v331_broad_legal import verified
from v341_eis_facts import parse_day

MAX_PAIR_GAP = 1500
CURRENT_RULE_FROM = date(2019, 1, 1)
_EIS = re.compile(r"\b(?:ЕИС|единой\s+информационной\s+системе)\b", re.I)
_ID = re.compile(
    r"\b(?P<kind>решени[еяю]|извещени[еяю]|протокол(?:а)?)"
    r"(?:\s+об?\s+отмене\s+(?:конкурентной\s+)?закупки)?\s*"
    r"(?:№|N\b|номер)\s*(?P<number>[0-9A-Za-zА-Яа-яЁё][0-9A-Za-zА-Яа-яЁё._-]{0,79}"
    r"(?:[ \t]*(?:/[ \t]*[0-9A-Za-zА-Яа-яЁё._-]{1,80}|"
    r"\([ \t]*[0-9A-Za-zА-Яа-яЁё._-]{1,40}[ \t]*\))){0,8})(?![\w/-])", re.I)
_UNRESOLVED_ID_TAIL = re.compile(r"[ \t]*[(/\\—–]", re.I)
_REG = re.compile(
    r"\b(?:закупк[аи]|(?:реестровый\s+)?номер\s+(?:закупки|извещения))"
    r"(?:\s+в\s+ЕИС)?\s*(?:№|N\b|[:：])?\s*(\d{11})(?!\d)", re.I)
_LOT = re.compile(r"\bлот(?:а|у)?\s*(?:№|N\b|номер|[:：])?\s*(\d{1,8})(?!\d)", re.I)

REASONS = {
    'cancellation_pair_ambiguous': 'Не установлена однозначная близкая пара дат решения об отмене и его публикации.',
    'cancellation_identity_conflict': 'Даты относятся к разным номерам решений или документов об отмене.',
    'cancellation_purchase_conflict': 'Фрагмент относится к другой закупке либо содержит противоречивые номера закупок.',
    'cancellation_lot_conflict': 'Не подтверждено, что решение и публикация относятся к одному проверяемому лоту.',
    'cancellation_source_context': 'Образец, цитата, проект или явно заменённый документ не подтверждает совершённое действие.',
    'cancellation_publication_not_eis': 'Не доказано, что указанная дата относится к размещению решения именно в ЕИС.',
    'cancellation_scope_unverified': 'Не подтверждена применимость нормы о конкурентной закупке в соответствующий период.',
    'cancellation_historical_rule': 'Для исторических дат требуется установить действовавшую редакцию закона и переходные положения.',
    'cancellation_date_unusable': 'Не удалось достоверно прочитать обе календарные даты события.',
    'cancellation_chronology_conflict': 'Дата публикации предшествует дате решения; требуется сверка исходных сведений.',
    'cancellation_future_event': 'Будущая дата не подтверждает уже совершённое решение или размещение.',
    'cancellation_original_unverified': 'Происхождение исходного документа не подтверждено.',
}


def _number(value):
    text = str(value or '').strip()
    return str(int(text)) if text.isdecimal() else text.casefold()


def _declared(records, keys):
    return {_number(record[key]) for record in records if isinstance(record, dict)
            for key in keys if isinstance(record.get(key), (str, int))
            and not isinstance(record[key], bool) and str(record[key]).strip()}


def _identity_reasons(p, doc, text, decision, publication, events):
    first, last = min(decision.start(), publication.start()), max(decision.end(), publication.end())
    previous = max((event.end() for event in events if event.end() <= first), default=0)
    # A previous act's date bounds the context. Its number must not leak into
    # the next independently headed decision in a combined original.
    left = max(previous, first - 5000)
    # The act's identity may follow its date, including a parenthesized
    # decision/lot reference. Keep that same-line tail, bounded before the next
    # dated event, without borrowing a subsequent independently headed act.
    line_end = text.find('\n', last)
    following = min((event.start() for event in events if event.start() >= last), default=len(text))
    right = min(last + 300, line_end if line_end >= 0 else len(text), following)
    fragment = text[left:right]
    title = str(doc.get('name') or '')
    meta = doc.get('meta') if isinstance(doc.get('meta'), dict) else {}
    reasons, identifiers = [], {}
    identity_text = title + '\n' + fragment
    for match in _ID.finditer(identity_text):
        token = re.sub(r'[ \t]+', '', match['number']).rstrip('.').casefold()
        token = re.sub(r'\.(?:pdf|docx?|rtf|txt|xml)$', '', token)
        if _UNRESOLVED_ID_TAIL.match(identity_text, match.end()):
            reasons.append('cancellation_identity_conflict')
        # In a long heading the 11-digit number labels the procurement,
        # not the cancellation decision.
        if re.fullmatch(r'\d{11}', token) and re.search(r'закупки', match[0], re.I):
            continue
        kind = ('decision' if match['kind'].lower().startswith('решени') else
                'notice' if match['kind'].lower().startswith('извещени') else 'protocol')
        identifiers.setdefault(kind, set()).add(token)
    if any(len(values) > 1 for values in identifiers.values()):
        reasons.append('cancellation_identity_conflict')
    regs = set(_REG.findall(title + '\n' + fragment))
    regs.update(_declared((doc, meta), ('reg_number', 'purchase_number', 'procurement_number')))
    expected = str(p.get('reg') or '').strip()
    if len(regs) > 1 or (expected and regs and regs != {expected}):
        reasons.append('cancellation_purchase_conflict')
    lots = {_number(value) for value in _LOT.findall(title + '\n' + fragment)}
    lots.update(_declared((doc, meta), ('lot_number', 'lot')))
    target_lots = _declared((p,), ('lot_number', 'lot'))
    # A display number and an internal EIS identifier are different identity
    # fields. The same lot may legitimately have both, without contradiction.
    lot_ids = _declared((doc, meta), ('lot_id',))
    target_ids = _declared((p,), ('lot_id',))
    if (len(lots) > 1 or len(target_lots) > 1 or len(lot_ids) > 1 or len(target_ids) > 1 or
            (lots and target_lots and lots != target_lots) or
            (lot_ids and target_ids and lot_ids != target_ids)):
        reasons.append('cancellation_lot_conflict')
    return reasons


def cancellation_event_pairs(p, doc, text, decisions, publications, *, as_of=None):
    """Return explicit supported/review pairs without guessing an event identity."""
    if not decisions and not publications:
        return []
    current_day = as_of or datetime.now(timezone(timedelta(hours=3))).date()
    events = sorted([*decisions, *publications], key=lambda event: event.start())
    result, used = [], set()
    context = document_context(doc)
    for index, decision in enumerate(decisions):
        stop = decisions[index + 1].start() if index + 1 < len(decisions) else len(text)
        candidates = [publication for publication in publications
                      if decision.end() <= publication.start() < stop
                      and publication.start() - decision.end() <= MAX_PAIR_GAP]
        if not candidates and len(decisions) == len(publications) == 1:
            publication = publications[0]
            if 0 <= decision.start() - publication.end() <= MAX_PAIR_GAP:
                candidates = [publication]
        publication = candidates[0] if len(candidates) == 1 else None
        reasons = []
        if publication is None:
            reasons.append('cancellation_pair_ambiguous')
        else:
            used.add(publication.start())
            reasons.extend(_identity_reasons(p, doc, text, decision, publication, events))
            if not _EIS.search(publication[0]):
                reasons.append('cancellation_publication_not_eis')
            if not all(local_context(text, event.start(), event.end())['eligible_actual']
                       for event in (decision, publication)):
                reasons.append('cancellation_source_context')
        if not verified(doc):
            reasons.append('cancellation_original_unverified')
        if not context['eligible_actual']:
            reasons.append('cancellation_source_context')
        if competitive_rule_scope(p) is not True:
            reasons.append('cancellation_scope_unverified')
        decided = parse_day(decision['date'])
        posted = parse_day(publication['date']) if publication is not None else None
        if publication is not None:
            if not decided or not posted:
                reasons.append('cancellation_date_unusable')
            else:
                if min(decided, posted) < CURRENT_RULE_FROM:
                    reasons.append('cancellation_historical_rule')
                if posted < decided:
                    reasons.append('cancellation_chronology_conflict')
                if max(decided, posted) > current_day:
                    reasons.append('cancellation_future_event')
        reasons = list(dict.fromkeys(reasons))
        result.append({'decision': decision, 'publication': publication,
                       'decision_date': decided, 'publication_date': posted,
                       'supported': not reasons, 'reason_codes': reasons,
                       'reasons': [REASONS[reason] for reason in reasons]})
    for publication in publications:
        if publication.start() not in used:
            result.append({'decision': None, 'publication': publication,
                           'decision_date': None, 'publication_date': parse_day(publication['date']),
                           'supported': False, 'reason_codes': ['cancellation_pair_ambiguous'],
                           'reasons': [REASONS['cancellation_pair_ambiguous']]})
    return result
