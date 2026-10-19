"""Fixed taxonomy for newly committed system-confirmed rows, not legal proof.

Only actual confirmed-rule emitters in the checked-in analyzer are listed.
Review-only catalog entries and arbitrary restored/runtime codes are unknown.
No rule discovery, string conversion, document inspection or database access.
"""

# Keep this explicit: a prefix, regex, catalog or caller-supplied key is unsafe.
RULE_IDS = (
    # app.analyze (base timing rules).
    "CLAR-LATE",
    "CANCEL-LATE",
    "PROTOCOL-LATE",
    # v324_fas_practice: _fas_admission_finding, _finding.
    "SME-COLLECTIVE-ADMISSION-FAS-223FZ-9-25",
    "SME-COLLECTIVE-APPLICATION-EXTRA",
    # v330_amendments_223: evaluation.
    "223-4-11-AMENDMENT-PUBLICATION-DELAY",
    "223-4-11-CHANGE-AFTER-BID-DEADLINE",
    # v336_legal_matrix: security_findings.
    "223-3.2-27-BID-SECURITY-LOW-NMCD",
    "223-SME-BID-SECURITY-OVER-TWO-PERCENT",
    "223-3.2-27-BID-SECURITY-OVER-FIVE-PERCENT",
    # v341_precise_legal: evaluate.
    "223-3.2-3-CLARIFICATION-RESPONSE-LATE",
    "223-3.2-6-CANCELLATION-POSTED-LATE",
    "223-4-12-PROTOCOL-PUBLICATION-LATE",
    # v348_cross_document_legal: cross_doc_clarifications, cross_doc_protocols.
    "223-3.2-3-CLARIFICATION-CROSS-DOC-LATE",
    "223-4-12-PROTOCOL-CROSS-DOC-LATE",
    # v372_auto_regulation_protocols: review_protocols (violation=True only).
    "v372-protocol-rejected-bid-no-reasons",
)
UNKNOWN = "unknown"
KEYS = (*RULE_IDS, UNKNOWN)
_CANONICAL = {code: code for code in RULE_IDS}
_MAX_CODE_LENGTH = max(map(len, RULE_IDS))
MAX_COUNT = 2**63 - 1


def count(codes):
    """One bounded counter per code; never retain or stringify unknown input."""
    result = {}
    for code in codes:
        key = (_CANONICAL.get(code, UNKNOWN)
               if type(code) is str and len(code) <= _MAX_CODE_LENGTH else UNKNOWN)
        result[key] = min(MAX_COUNT, result.get(key, 0) + 1)
    return result


def clean(counts):
    """Re-allowlist canonical keys and positive integers at the output boundary."""
    if type(counts) is not dict:
        return {}
    result = {}
    for key in KEYS:
        value = counts.get(key)
        if type(value) is int and value > 0:
            result[key] = min(MAX_COUNT, value)
    return result


def merge(left, right):
    result = clean(left)
    for key, value in clean(right).items():
        result[key] = min(MAX_COUNT, result.get(key, 0) + value)
    return result
