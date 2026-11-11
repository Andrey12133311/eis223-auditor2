"""Bounded source-bound lot identity for already qualified condition outcomes.

This annotation separates legal incidents; it never confirms a legal finding.
The current detector and reassessment must still authenticate the original,
clause, operative context and applicable subject rule. Security wrappers keep
their original API; generic condition readers also handle contract conditions.
"""
from __future__ import annotations

import json
import hashlib
import re
from urllib.parse import urlsplit

PREFIX = "Область условия обеспечения заявки: "
CONTRACT_PREFIX = "Область условия заключения договора: "
CODES = frozenset({"223-3.2-27-BID-SECURITY-LOW-NMCD",
    "223-3.2-27-BID-SECURITY-OVER-FIVE-PERCENT",
    "223-SME-BID-SECURITY-OVER-TWO-PERCENT"})
CONTRACT_CODES = frozenset({"223-3.2-15-CONTRACT-CONCLUSION-TERM"})
_FAMILIES = {"security": (CODES, PREFIX, "security_assessment"),
    "contract": (CONTRACT_CODES, CONTRACT_PREFIX, "contract_conclusion_assessment")}
_FIELDS = frozenset({"v", "reg", "lots", "lot_ids", "sha256", "source_url_sha256",
    "archive_member", "span", "quote"})


def _text(value):
    return " ".join(str(value or "").replace("\xa0", " ").split())


def _values(values):
    return (isinstance(values, list) and len(values) <= 1 and
        all(isinstance(value, str) and value == value.strip() and
            0 < len(value) <= 120 and not re.search(r"[\r\n\x00]", value)
            for value in values))


def _source_url(value):
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = urlsplit(value)
        return (parsed.scheme in {"https", "http"} and parsed.hostname in
            {"zakupki.gov.ru", "www.zakupki.gov.ru"} and
            parsed.port in {None, 443, 80} and not parsed.username and
            not parsed.password and not parsed.fragment)
    except ValueError:
        return False


def _url_digest(value):
    if not _source_url(value):
        return None
    try:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
    except UnicodeError:
        return None


def _valid(proof, family="security"):
    if not isinstance(proof, dict) or set(proof) != _FIELDS:
        return False
    span, quote = proof["span"], proof["quote"]
    return (type(proof["v"]) is int and proof["v"] == 1 and
        isinstance(proof["reg"], str) and bool(re.fullmatch(r"\d{11,20}", proof["reg"])) and
        _values(proof["lots"]) and _values(proof["lot_ids"]) and
        bool(proof["lots"] or proof["lot_ids"]) and
        isinstance(proof["sha256"], str) and bool(re.fullmatch(r"[0-9a-f]{64}", proof["sha256"])) and
        isinstance(proof["source_url_sha256"], str) and
        bool(re.fullmatch(r"[0-9a-f]{64}", proof["source_url_sha256"])) and
        isinstance(proof["archive_member"], str) and
        len(proof["archive_member"]) <= 1000 and
        isinstance(span, list) and len(span) == 2 and
        all(type(value) is int for value in span) and
        0 <= span[0] < span[1] <= 750000 and span[1]-span[0] <= 650 and
        isinstance(quote, str) and len(quote) == span[1]-span[0] and
        _valid_quote(quote, family))


def _valid_quote(quote, family):
    if family == "security":
        return bool(re.search(r"обеспечени\w*\s+заявк", quote, re.I))
    return family == "contract" and all(re.search(pattern, quote, re.I) for pattern in
        (r"\b(?:договор|контракт)\b", r"заключ", r"не\s+ранее", r"не\s+позднее"))


def _proof_from_source(purchase, scope, source, family):
    reg = str(purchase.get("reg") or purchase.get("reg_number") or "")
    if any(str(purchase[key]) != reg for key in ("reg", "reg_number") if purchase.get(key)):
        return None
    if source.get("reg") and str(source["reg"]) != reg:
        return None
    proof = {"v": 1, "reg": reg, "lots": scope.get("lots"), "lot_ids": scope.get("lot_ids"),
        "sha256": source.get("sha256"), "source_url_sha256": _url_digest(source.get("source_url")),
        "archive_member": source.get("archive_member") or "",
        "span": [source.get("start"), source.get("end")], "quote": source.get("excerpt")}
    return proof if _valid(proof, family) else None


def _assessment_proof(purchase, assessment):
    if not isinstance(purchase, dict) or not isinstance(assessment, dict):
        return None
    if assessment.get("status") != "confirmed" or assessment.get("reasons"):
        return None
    scope, price = assessment.get("source_scope"), assessment.get("price_basis")
    source = assessment.get("source")
    if not all(isinstance(value, dict) for value in (scope, price, source)):
        return None
    price_scope = price.get("scope")
    if not isinstance(price_scope, dict):
        return None
    if any(value.get("eligible_actual") is not True or value.get("reasons")
           for value in (scope, price_scope)):
        return None
    if any(scope.get(key) != price_scope.get(key) for key in ("lots", "lot_ids")):
        return None
    return _proof_from_source(purchase, scope, source, "security")


def _contract_assessment_proof(purchase, assessment):
    if (not isinstance(purchase, dict) or not isinstance(assessment, dict) or
            assessment.get("state") != "confirmed" or assessment.get("reason_codes") or assessment.get("reasons")):
        return None
    binding = assessment.get("source_binding")
    if not isinstance(binding, dict):
        return None
    quote, position = binding.get("source_excerpt"), binding.get("source_position")
    if not isinstance(quote, str) or type(position) is not int:
        return None
    source = {**binding, "start": position, "end": position+len(quote), "excerpt": quote}
    return _proof_from_source(purchase, assessment, source, "contract")


def security_scope_evidence(purchase, assessment):
    """Serialize only the explicit scope of a freshly confirmed assessment."""
    return _serialize(_assessment_proof(purchase, assessment), PREFIX)


def contract_scope_evidence(purchase, assessment):
    """Serialize a contract condition's fresh, independently reproduced scope."""
    return _serialize(_contract_assessment_proof(purchase, assessment), CONTRACT_PREFIX)


def _serialize(proof, prefix):
    if not proof:
        return ""
    raw = json.dumps(proof, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    try:
        raw.encode("utf-8")
    except UnicodeError:
        return ""
    return "\n"+prefix+raw+"." if len(raw) <= 4096 else ""


def finding_security_scope(item):
    """Read identity only; arbitrary lot fields never become source evidence."""
    return _finding_scope(item, "security")


def finding_contract_scope(item):
    return _finding_scope(item, "contract")


def _family(item):
    if not isinstance(item, dict):
        return None
    code = str(item.get("code") or item.get("field_path") or "").strip().upper()
    return next((family for family, values in _FAMILIES.items() if code in values[0]), None)


def finding_condition_scope(item):
    family = _family(item)
    return _finding_scope(item, family) if family else None


def _finding_scope(item, family):
    if _family(item) != family:
        return None
    _, prefix, assessment_key = _FAMILIES[family]
    lines = str(item.get("evidence") or "").strip().splitlines()
    annotations = [line for line in lines if line.startswith(prefix)]
    if len(annotations) != 1 or annotations[0] != lines[-1] or not annotations[0].endswith("."):
        return None
    raw = annotations[0][len(prefix):-1]
    if len(raw) > 4096:
        return None
    try:
        proof = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not _valid(proof, family) or _url_digest(item.get("source_url")) != proof["source_url_sha256"]:
        return None
    if _text(proof["quote"]) not in _text("\n".join(lines[:-1])):
        return None
    for key in ("reg", "reg_number"):
        if item.get(key) and str(item[key]) != proof["reg"]:
            return None
    for key, expected in (("sha256", proof["sha256"]), ("source_sha256", proof["sha256"]),
                          ("archive_member", proof["archive_member"])):
        if item.get(key) and item[key] != expected:
            return None
    assessor = _assessment_proof if family == "security" else _contract_assessment_proof
    if assessment_key in item and assessor({"reg": proof["reg"]}, item[assessment_key]) != proof:
        return None
    return proof


def distinct_security_lots(left, right):
    """Explicitly different authenticated scopes forbid semantic coalescing."""
    return security_scopes_conflict(security_scope_key(left), security_scope_key(right))


def security_scope_key(item):
    return _scope_key(finding_security_scope(item))


def condition_scope_key(item):
    return _scope_key(finding_condition_scope(item))


def _scope_key(proof):
    return (proof["reg"], tuple(proof["lots"]), tuple(proof["lot_ids"])) if proof else None


def security_scopes_conflict(left, right):
    return condition_scopes_conflict(left, right)


def distinct_condition_lots(left, right):
    return condition_scopes_conflict(condition_scope_key(left), condition_scope_key(right))


def condition_scopes_conflict(left, right):
    """A missing scope cannot bridge two incompatible established scopes."""
    return bool(left and right and (left[0] != right[0] or any(
        left[index] and right[index] and left[index] != right[index] for index in (1, 2))))


def same_security_original_scope(left, right):
    """Copies of the same bytes in one established lot are one representation."""
    return _same_original_scope(finding_security_scope(left), finding_security_scope(right))


def same_condition_original_scope(left, right):
    if not _family(left) or _family(left) != _family(right):
        return False
    return _same_original_scope(finding_condition_scope(left), finding_condition_scope(right))


def _same_original_scope(a, b):
    return bool(a and b and all(a[key] == b[key] for key in
        ("reg", "lots", "lot_ids", "sha256", "archive_member")))


def security_evidence_text(item, text=None):
    """Identity metadata, including invalid tags, is not a new substantive fact."""
    return _evidence_text(item, text, "security" if _family(item) == "security" else None)


def condition_evidence_text(item, text=None):
    return _evidence_text(item, text, _family(item))


def _evidence_text(item, text, family):
    value = str(item.get("evidence") or "") if text is None else str(text or "")
    if not family:
        return value
    prefix = _FAMILIES[family][1]
    return "\n".join(line for line in value.splitlines() if not line.startswith(prefix))
