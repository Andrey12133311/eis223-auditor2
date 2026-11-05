"""Read explicit dates from saved EIS fields without inference or I/O.

The resolved summary precedes raw snapshots. An unresolved official-source
conflict must never be filled from an older raw page or an unrelated date.
"""
from __future__ import annotations

from datetime import datetime
import json
import re

ALIASES = {
    "submission_start": ("submission_start", "submissionStart", "submissionStartDate",
        "submissionStartDateTime", "applicationStartDate", "applicationStartDateTime",
        "submissionOpenDate", "applicationOpenDate", "startSubmissionDate",
        "applicationSubmissionStartDate"),
    "submission_end": ("submission_end", "submissionEnd", "submissionCloseDate",
        "submissionEndDate", "submissionEndDateTime", "applicationEndDate",
        "applicationEndDateTime", "submissionDeadline", "applicationDeadline",
        "endSubmissionDate", "applicationSubmissionEndDate", "deadline"),
    "published": ("published", "published_at", "publicationDate"),
}
LABELS = {
    "submission_start": re.compile(r"(?:дата\s+(?:и\s+время\s+)?начала\s+(?:срока\s+)?(?:подачи|при[её]ма)\s+заявок|начало\s+(?:срока\s+)?(?:подачи|при[её]ма)\s+заявок)", re.I),
    "submission_end": re.compile(r"(?:дата\s+(?:и\s+время\s+)?окончания\s+(?:срока\s+)?(?:подачи|при[её]ма)\s+заявок|окончание\s+(?:срока\s+)?(?:подачи|при[её]ма)\s+заявок)", re.I),
    "published": re.compile(r"(?:дата\s+(?:и\s+время\s+)?(?:первоначального\s+)?(?:размещения|публикации)(?:\s+(?:извещения|закупки))?(?:\s+в\s+еис)?|размещено)", re.I),
}
LOCAL_DATE = re.compile(r"(?P<date>\d{2}\.\d{2}\.\d{4})(?:\s+(?P<time>\d{2}:\d{2}(?::\d{2})?))?(?:\s*\(МСК(?:[+-]\d{1,2})?\))?", re.I)
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:?\d{2})?)?")


def explicit_date(value):
    if not isinstance(value, str):
        return ""
    value = " ".join(value.split())
    match = LOCAL_DATE.fullmatch(value)
    try:
        if match:
            datetime.strptime(match["date"] + (" " + match["time"] if match["time"] else ""),
                "%d.%m.%Y" + (" %H:%M:%S" if match["time"] and len(match["time"]) == 8 else " %H:%M" if match["time"] else ""))
        elif ISO_DATE.fullmatch(value):
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            return ""
    except ValueError:
        return ""
    return value


def _mapping(raw):
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _one(values):
    dates = list(dict.fromkeys(value for item in values if (value := explicit_date(item))))
    # Different unresolved values are not a license to choose an arbitrary one.
    return dates[0] if len(dates) == 1 else ""


def _pairs(container, field):
    pairs = container.get("pairs")
    if not isinstance(pairs, (list, tuple)):
        return
    for pair in pairs:
        if not isinstance(pair, (list, tuple)) or len(pair) < 2:
            continue
        label = " ".join(str(pair[0]).split()).strip(" :")
        if ":" in label:
            prefix, label = label.split(":", 1)
            if prefix not in ("common", "lots"):
                continue
            label = label.strip()
        label = re.sub(r"\s*\(по местному времени заказчика\)\s*$", "", label, flags=re.I)
        if LABELS[field].fullmatch(label):
            yield pair[1]


def recovered_date(p, field, card=None):
    """Return a known field; publication/deadline never substitute for opening."""
    if field not in ALIASES:
        raise ValueError("Unknown procurement date field")
    p = p or {}
    card = _mapping(card if card is not None else p.get("card_fields_json"))
    for source in (p, card):
        conflicts = _mapping(source.get("_official_context_conflicts"))
        fact = _mapping(_mapping(source.get("fact_evidence")).get(field))
        if field in conflicts or fact.get("source") == "official_eis_context_conflict":
            return ""
    canonical = explicit_date(p.get(field))
    if canonical:
        return canonical
    direct = [value for key in ALIASES[field] if key not in (field, "deadline")
              if (value := explicit_date(p.get(key)))]
    if direct:
        return _one(direct)
    if field == "submission_end" and explicit_date(p.get("deadline")):
        return explicit_date(p["deadline"])
    summary = _mapping(card.get("summary"))
    # Summary fields have already passed the current-source reconciliation.
    for container in (summary, card):
        candidates = [container.get(key) for key in ALIASES[field] if container.get(key)]
        if candidates:
            return _one(candidates)
    facts = []
    for source in (p, card):
        fact = _mapping(_mapping(source.get("fact_evidence")).get(field))
        if fact.get("source") in ("official_eis_card", "official_eis_xml", "official_eis_getDocsIP", "official_eis_document"):
            facts.append(fact.get("value"))
    if facts:
        return _one(facts)
    values = list(_pairs(card, field))
    for name in ("common", "lots"):
        page = _mapping(_mapping(card.get("pages")).get(name))
        if page.get("status") == "ok":
            values.extend(_pairs(page, field))
    return _one(values)


def with_saved_dates(p, card=None):
    """Project consistent saved dates for audits/exports without modifying input."""
    result = dict(p or {})
    for field in ALIASES:
        result[field] = recovered_date(result, field, card)
    if result["submission_end"] or "deadline" in result:
        result["deadline"] = result["submission_end"]
    return result
