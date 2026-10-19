"""Shared evidence-preserving classification; unknown is never competitive.

Classification is of the explicit method field, not a legal conclusion based
on words anywhere in tender documents. Generic quotations cannot supply scope.
"""
import re

_NONCOMPETITIVE = re.compile(
    r"\bне\s+(?:является|относится\s+к)\s+конкурентн\w*|"
    r"\bне\s+(?:конкурс\w*|аукцион\w*)\b|\bзакупк\w*\s+у\s+ЕП\b|"
    r"\bне\s*[-–—]?\s*конкурентн\w*|\bнеконкурентн\w*|"
    r"\bединственн\w*\s+(?:поставщик\w*|подрядчик\w*|исполнител\w*)|"
    r"\bпрям\w*\s+закупк\w*|\bбез\s+(?:проведения\s+)?(?:конкурс\w*|торгов)", re.I)
_COMPETITIVE = re.compile(
    r"\bконкурс\w*\b|\bаукцион\w*\b|\bзапрос\s+(?:котировок|предложени\w*)\b|"
    r"\bконкурентн\w*\s+(?:закупк\w*|процедур\w*)\b", re.I)

def competitive_scope(purchase):
    """Return True / False / None. Contradictory explicit method fields are unknown."""
    values = [str(purchase.get(k) or '').strip() for k in ('method', 'purchase_method')]
    states = []
    for value in values:
        if not value:
            continue
        # Explicit noncompetitive terminology takes priority over a quoted
        # standard method, e.g. 'неконкурентный запрос предложений'.
        state = False if _NONCOMPETITIVE.search(value) else True if _COMPETITIVE.search(value) else None
        states.append(state)
    known = {state for state in states if state is not None}
    return known.pop() if len(known) == 1 else None

def competitive_rule_scope(purchase):
    """Current competitive rule family is unsupported for known legacy notices.

    505-FZ transition depends on publication of the aligned regulation. We do
    not infer that transition from event dates or silently apply modern rules
    to a known pre-2019 notice. Missing dates still need the detector's own
    event-date evidence; this is not a complete historical-edition resolver.
    """
    from datetime import date
    for key in ('published', 'published_at'):
        raw=str(purchase.get(key) or '').strip()
        if not raw:continue
        try:
            notice=date.fromisoformat(raw[:10])
        except ValueError:
            match=re.search(r'\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b',raw)
            if not match:continue
            try:notice=date(int(match[3]),int(match[2]),int(match[1]))
            except ValueError:continue
        if notice<date(2019,1,1):return None
    return competitive_scope(purchase)
