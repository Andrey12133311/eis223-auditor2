"""Persistent identity of confirmed 223-FZ legal incidents.

An identical violation may be extracted by two different analyzers and from
multiple originals. Conversely, different dated events or contractual terms
are NOT the same infringement merely because their rule code is identical.
No destructive SQL cleanup is performed.
"""
import re
from v333_dedup_ui import issue_key,_different_fact,_proof
from v352_dashboard_truth import _common_event
from v341_eis_facts import DATE_TOKEN,parse_day
from v348_cross_document_legal import finding_protocol_number

DATED_RULES=("223-3.2-3-CLARIFICATION-","223-4-12-PROTOCOL-",
             "223-3.2-6-CANCELLATION-","223-4-11-")

def event_dates(evidence):
    dates=[]
    for match in re.finditer(DATE_TOKEN,str(evidence or ""),re.I):
        date=parse_day(match.group(0))
        if date:dates.append(date.isoformat())
    return tuple(sorted(set(dates)))

def same_episode(old,new):
    """Only suppress a stored row when both represent the same legal act."""
    if not isinstance(old,dict) or not isinstance(new,dict):return False
    left=issue_key(old.get("title") or old.get("item") or old.get("code"))
    right=issue_key(new.get("title") or new.get("item") or new.get("code"))
    # A matching normalized rule code can be used when a legacy title differs,
    # but never without matching independently established event dates.
    a=str(old.get("code") or old.get("field_path") or "")
    b=str(new.get("code") or new.get("field_path") or "")
    matching_rule=bool(a and b and a==b)
    # Identical dates can belong to different protocols. Preserve their
    # independently evidenced numbers across repeated audits and persistence;
    # dates alone are not an identity when both originals say otherwise.
    if "PROTOCOL-" in a and "PROTOCOL-" in b:
        old_number,new_number=finding_protocol_number(old),finding_protocol_number(new)
        if old_number and new_number and old_number!=new_number:
            return False
    if not matching_rule and (not left or left!=right):
        return False
    la=str(old.get("law") or "").strip().casefold()
    lb=str(new.get("law") or "").strip().casefold()
    if la and lb and la!=lb:return False
    ea,eb=_proof(old.get("evidence")),_proof(new.get("evidence"))
    if _different_fact(ea,eb):
        # Date spelling (2026-10-01 vs 01.10.2026) is not a new event.
        old_dates,new_dates=event_dates(ea),event_dates(eb)
        if not(old_dates and new_dates and old_dates==new_dates):
            return False
    if matching_rule and any(a.startswith(prefix) for prefix in DATED_RULES):
        od,nd=event_dates(ea),event_dates(eb)
        if od and nd:
            return od==nd
    # Match text, not rule code alone. Different contract clauses may have
    # the same legal title, source and requirement but constitute two acts.
    return _common_event(
        {**old,"title":old.get("title") or old.get("item") or a},
        {**new,"title":new.get("title") or new.get("item") or b})

def distinct_new(candidate,existing):
    return not any(same_episode(previous,candidate) for previous in existing)
