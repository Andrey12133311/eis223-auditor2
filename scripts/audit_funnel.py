"""Best-effort, bounded audit boundary diagnostics; never legal evidence.

No document text, account identifiers, titles, URLs or free-form reasons leave
this module. The existing public procurement number is optional and strictly
validated as 11–20 ASCII digits; arbitrary caller identifiers are omitted. Candidates mean outputs from the installed analyzer/checklist,
not every internal regex match. Counts are rows, not unique legal episodes.
readable_input_documents is the readable subset of the actual engine input:
general may include unverified files; thematic supplies only verified files.
Thematic analysis/persist lines repeat one trace's cumulative analysis counts;
use stage to select records, not to sum both stages. analysis_observed=False
means the analysis boundary was not observed, not that all inputs were empty.
committed=True means the transaction committed; persist_applied distinguishes
fenced items. Existing unique-event logs remain separate from inserted rows.
"""
import asyncio
from contextvars import ContextVar
from functools import wraps
import json
import uuid
import audit_rule_counts

CURRENT = ContextVar("audit_funnel", default=None)
COUNTS = frozenset(("documents", "unreadable", "readable_input_documents", "verified_documents", "analysis_documents",
    "analyzer_candidates", "analyzer_violation", "analyzer_check", "analyzer_risk", "analyzer_other", "analyzer_invalid",
    "checklist_candidates", "confirmed", "review", "ok",
    "selected_before_collapse", "selected_after_collapse", "persist_input",
    "persisted_confirmed", "persisted_review", "persisted_ok"))
REASONS = frozenset(("no_readable_documents", "no_verified_documents", "source_incomplete",
    "non_violation", "missing_law", "missing_evidence", "source_unverified", "category_unselected",
    "company_excluded", "review_only", "msp_scope_unknown", "msp_scope_conflict",
    "verified_nmcd_missing", "applicability_review", "date_pair_review", "publication_conflict",
    "fact_comparison_review", "duplicate", "invalid_record", "campaign_inactive", "reservation_changed",
    "outside_period", "company_scope_changed", "not_persistable", "analysis_failed", "analysis_cancelled", "analysis_timeout"))
STATES = frozenset(("completed", "retry", "waiting_originals", "waiting_engine", "excluded_company",
                    "no_readable_documents", "superseded", "outside_period", "failed", "cancelled", "unknown"))
MAX_COUNT = 2**63 - 1


def safe(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Exception:
            return None  # Including broken logging; never request a legal retry.
    return guarded


@safe
def valid_registration(value):
    if not isinstance(value, str):return None
    # Reservation strings may be subclasses; bypass overridden conversions.
    value = str.__str__(value)
    if 11 <= len(value) <= 20 and all("0" <= char <= "9" for char in value):
        return value
    return None


@safe
def _registration(args, kwargs, field):
    if field not in ("reg", "item"):return None
    value = kwargs.get(field, args[1] if len(args) > 1 else None)
    if field == "item":value = value.get("reg") if isinstance(value, dict) else None
    return valid_registration(value)


@safe
def new(workflow, reg=None):
    return {"workflow": workflow if workflow in ("general", "thematic") else "unknown",
            "trace": uuid.uuid4().hex, "counts": {}, "reasons": {}, "analysis_observed": False,
            "committed": False, "persist_applied": False, "reg": valid_registration(reg)}


def _add(event, group, name, amount=1):
    allowed = COUNTS if group == "counts" else REASONS
    if event is None or name not in allowed or type(amount) is not int or amount < 0:
        return
    target = event[group]
    target[name] = min(MAX_COUNT, target.get(name, 0) + amount)


def _rows(value):
    return value if isinstance(value, (list, tuple)) else ()


def _review_reason(row):
    # Explicit machine codes only. Do not infer reasons from legal prose.
    code = row.get("field_path") or row.get("code")
    if not isinstance(code, str):return "review_only"
    if code == "v383-bid-security-msp-scope":
        scope = row.get("sme_scope")
        return "msp_scope_conflict" if isinstance(scope, dict) and scope.get("state") == "conflict" else "msp_scope_unknown"
    if code == "v336-bid-security-needs-verified-nmcd":return "verified_nmcd_missing"
    if code == "v341-clarification-applicability":return "applicability_review"
    if code in ("v341-clarification-pair", "v341-cancellation-date-chain", "v341-protocol-ambiguous"):
        return "date_pair_review"
    if code == "v341-protocol-publication-xml-conflict":return "publication_conflict"
    if code.startswith("v339-"):return "fact_comparison_review"
    return "review_only"


@safe
def observe(kind, *values):
    """Standalone observation calls accept values but never mutate them."""
    event = CURRENT.get()
    if event is None:return
    if kind == "gate":
        _add(event, "reasons", values[0])
    elif kind == "documents_general":
        docs, unreadable, verified, readable = values
        event["analysis_observed"] = True
        for key, value in (("documents", len(_rows(docs))), ("unreadable", unreadable),
                           ("verified_documents", verified), ("readable_input_documents", readable), ("analysis_documents", len(_rows(docs)))):
            _add(event, "counts", key, value)
    elif kind == "documents_thematic":
        docs, unreadable, verified = values
        event["analysis_observed"] = True
        for key, value in (("documents", len(_rows(docs))), ("unreadable", unreadable),
                           ("verified_documents", len(_rows(verified))), ("readable_input_documents", len(_rows(verified))),
                           ("analysis_documents", len(_rows(verified)))):
            _add(event, "counts", key, value)
    elif kind == "persist_input":
        _add(event, "counts", "persist_input", sum(len(_rows(rows)) for rows in values))
    elif kind == "incomplete":
        if values[0]:_add(event, "reasons", "source_incomplete")
    elif kind == "analyzer_results":
        rows = _rows(values[0])
        _add(event, "counts", "analyzer_candidates", len(rows))
        # Fixed kinds only: an output row is not necessarily a claimed
        # violation. Never serialize arbitrary kind names or inspect prose.
        for row in rows:
            if not isinstance(row, dict):
                key = "analyzer_invalid"
            else:
                category = row.get("kind")
                key = ("analyzer_" + category if isinstance(category, str)
                       and category in ("violation", "check", "risk") else "analyzer_other")
            _add(event, "counts", key)
    elif kind == "checklist_results":
        checks = _rows(values[0])
        _add(event, "counts", "checklist_candidates", len(checks))
        for row in checks:
            if isinstance(row, dict) and row.get("status") in ("check", "risk"):
                _add(event, "reasons", _review_reason(row))
    elif kind == "outcomes_general":
        findings, checks = map(_rows, values)
        _add(event, "counts", "confirmed", len(findings))
        for row in checks:
            if not isinstance(row, dict):continue
            status = row.get("status")
            key = "confirmed" if status == "violation" else "review" if status in ("check", "risk") else "ok" if status == "ok" else None
            if key:_add(event, "counts", key)
    elif kind == "selected_before_collapse":
        _add(event, "counts", "selected_before_collapse", len(_rows(values[0])))
    elif kind == "outcomes_thematic":
        _add(event, "counts", "confirmed", len(_rows(values[0])))
        _add(event, "counts", "selected_after_collapse", len(_rows(values[0])))
    elif kind == "thematic_reviews":
        for row in _rows(values[0]):
            if isinstance(row, dict) and row.get("status") in ("check", "risk"):
                _add(event, "counts", "review")
            elif isinstance(row, dict) and row.get("status") == "ok":
                _add(event, "counts", "ok")


@safe
def increment(event, group, name, amount=1):
    _add(event, group, name, amount)


@safe
def current_increment(group, name, amount=1):
    _add(CURRENT.get(), group, name, amount)


@safe
def committed():
    event = CURRENT.get()
    if event is not None:event.update(committed=True, persist_applied=True)


@safe
def committed_general_rules(findings, checks):
    """Use only the exact inserted SQL tuples, after the durable commit."""
    event = CURRENT.get()
    if event is None or not event["committed"] or not event["persist_applied"]:return
    codes = (row[1] for row in findings)
    check_codes = (row[8] for row in checks if row[3] == "violation")
    event["persisted_rules"] = audit_rule_counts.merge(
        audit_rule_counts.count(codes), audit_rule_counts.count(check_codes))


@safe
def bind(item):
    event = CURRENT.get()
    if event is not None:item["_audit_funnel"] = event


@safe
def pending(events, item, findings, state):
    previous = item.get("_audit_funnel")
    # Clone only the closed, fixed-size scalar summary; never retain findings.
    event = {**previous, "counts": dict(previous["counts"]), "reasons": dict(previous["reasons"])} if isinstance(previous, dict) else new("thematic", item.get("reg"))
    if event is None:return
    event.pop("persisted_rules", None)  # A retry never inherits an earlier delta.
    _add(event, "counts", "persist_input", len(_rows(findings)))
    event["state"] = state if state in STATES else "unknown"
    event["persist_applied"] = False
    events.append(event)
    return event


@safe
def emit(event, stage, state="unknown"):
    if event is None:return
    # Re-allowlist the output as defense in depth against caller-owned objects.
    payload = {"v": 1, "workflow": event["workflow"] if event["workflow"] in ("general", "thematic") else "unknown",
        "stage": stage if stage in ("analysis", "persist", "complete") else "unknown",
        "state": state if state in STATES else "unknown",
        "trace": event["trace"] if isinstance(event["trace"], str) and len(event["trace"]) == 32 and all(c in "0123456789abcdef" for c in event["trace"]) else "unavailable",
        "analysis_observed": event.get("analysis_observed") is True, "committed": event.get("committed") is True,
        "persist_applied": event.get("persist_applied") is True,
        "counts": {k: min(MAX_COUNT, event["counts"].get(k, 0)) for k in sorted(COUNTS) if type(event["counts"].get(k, 0)) is int and event["counts"].get(k, 0) >= 0},
        "reasons": {k: min(MAX_COUNT, v) for k, v in event["reasons"].items() if k in REASONS and type(v) is int and v >= 0},
        "persisted_rules": audit_rule_counts.clean(event.get("persisted_rules"))
            if event.get("committed") is True and event.get("persist_applied") is True else {}}
    reg = valid_registration(event.get("reg"))
    if reg is not None:payload["reg"] = reg
    print("AUDIT_FUNNEL " + json.dumps(payload, sort_keys=True, separators=(",", ":")), flush=True)


@safe
def applied(event):
    if event is not None:event["persist_applied"] = True


@safe
def _committed_thematic_rules(thematic_plans):
    # Thematic plans contain only rows actually inserted for applied items.
    # This function runs after commit/close, never inside the SQLite writer.
    for _, state, findings, _, _, _, event in thematic_plans:
        if (event is not None and event.get("persist_applied") is True
                and state in ("completed", "retry")):
            event["persisted_rules"] = audit_rule_counts.count(f.get("code") for f in findings)


@safe
def emit_committed(events, thematic_plans=()):
    _committed_thematic_rules(thematic_plans)
    for event in events:
        event["committed"] = True
        state = event.get("state", "unknown")
        if "outside_period" in event["reasons"]:state = "outside_period"
        elif any(key in event["reasons"] for key in ("campaign_inactive", "reservation_changed", "company_scope_changed")):state = "superseded"
        emit(event, "persist", state)


@safe
def _state(result):
    if isinstance(result, tuple):
        for value in result[:2]:
            if isinstance(value, str) and value in STATES:return value
    return "unknown"


@safe
def _enter(workflow, isolated=False, reg=None):
    parent = CURRENT.get()
    if parent is not None and not isolated:return None
    event = new(workflow, reg)
    if event is None:return None
    if parent is not None:event["trace"] = parent["trace"]
    return CURRENT.set(event), event, parent


@safe
def _exit(scope, stage, state):
    if scope is None:return
    token, event, parent = scope
    CURRENT.reset(token)
    if parent is not None:
        # A failed SQLite attempt contributes nothing; a retry gets fresh
        # counters. Never merge an uncommitted delta into the final audit.
        if event["committed"]:
            for group in ("counts", "reasons"):
                for key, value in event[group].items():_add(parent, group, key, value)
            parent["committed"] = True
            parent["persist_applied"] = event["persist_applied"]
            parent["persisted_rules"] = audit_rule_counts.merge(
                parent.get("persisted_rules"), event.get("persisted_rules"))
    else:
        emit(event, stage, state)


def trace(workflow, stage="complete", registration=None):
    """Only orchestration wrappers own scopes; to_thread copies their context."""
    def decorate(function):
        @wraps(function)
        async def tracked(*args, **kwargs):
            scope = _enter(workflow, reg=_registration(args, kwargs, registration))
            state = "failed"
            try:
                result = await function(*args, **kwargs)
                state = _state(result) or "unknown"
                return result
            except asyncio.CancelledError:
                state = "cancelled"
                observe("gate", "analysis_cancelled")
                raise
            finally:
                _exit(scope, stage, state)
        return tracked
    return decorate


def trace_sync(workflow, stage="persist", registration=None):
    def decorate(function):
        @wraps(function)
        def tracked(*args, **kwargs):
            scope = _enter(workflow, isolated=True, reg=_registration(args, kwargs, registration))
            state = "failed"
            try:
                result = function(*args, **kwargs)
                state = "completed"
                return result
            finally:
                _exit(scope, stage, state)
        return tracked
    return decorate
