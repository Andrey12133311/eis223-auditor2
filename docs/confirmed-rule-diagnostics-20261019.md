# Newly committed confirmed-rule diagnostics

Local-only diagnostic change based on `5e3639d` (PR75 tree). No deployment,
legal threshold change, counter change, endpoint, database migration or credential.

## Meaning and privacy

`AUDIT_FUNNEL` gains the additive `persisted_rules` object. It contains positive
counts of **newly inserted system-confirmed rows**, keyed by a fixed allowlist
of 16 actual checked-in analyzer rule IDs, plus the single `unknown` fallback.
It is not a legal conclusion, a count of unique legal events, a rule-coverage
claim, or a count of all historical findings. Existing counters/reasons and the
separate unique-event diagnostics remain unchanged.

For example, a new persisted amendment row produces
`"persisted_rules":{"223-4-11-AMENDMENT-PUBLICATION-DELAY":1}`. A successful
duplicate-only replay produces `{}`. Analysis-only, rolled-back, and fenced-out
items never expose a rule delta. Unknown, missing, arbitrary, or non-plain-string
codes contribute only to `unknown`; no arbitrary code is echoed, truncated,
hashed into a label, or inferred from prose. Review-only catalog codes are not
allowed as confirmed-rule labels.

The taxonomy examines only the exact stored finding/checklist code fields.
It never examines titles, evidence, document text, source URLs, PII or secrets.
Output keys are re-allowlisted using canonical static strings. Counters are
positive integers capped at `2**63 - 1`, with at most 17 keys. Input strings
longer than the longest static code are rejected before hashing or lookup.
No document/analyzer re-run, extra database query or per-document log is added.

These logs remain best-effort. A log/classifier failure cannot undo a committed
finding or trigger a legal retry. If aggregate `persisted_confirmed` is positive
but no rule breakdown is available, do not interpret that as zero new findings.
Historical logs cannot retroactively classify a previously persisted row; this
change classifies only future successful inserts.

## Explicit taxonomy and checked-in emitters

- `app.py:analyze`: `CLAR-LATE`, `CANCEL-LATE`, `PROTOCOL-LATE`
- `v324_fas_practice.py:_fas_admission_finding/_finding`:
  `SME-COLLECTIVE-ADMISSION-FAS-223FZ-9-25`,
  `SME-COLLECTIVE-APPLICATION-EXTRA`
- `v330_amendments_223.py:evaluation`:
  `223-4-11-AMENDMENT-PUBLICATION-DELAY`,
  `223-4-11-CHANGE-AFTER-BID-DEADLINE`
- `v336_legal_matrix.py:security_findings`:
  `223-3.2-27-BID-SECURITY-LOW-NMCD`,
  `223-SME-BID-SECURITY-OVER-TWO-PERCENT`,
  `223-3.2-27-BID-SECURITY-OVER-FIVE-PERCENT`
- `v341_precise_legal.py:evaluate`:
  `223-3.2-3-CLARIFICATION-RESPONSE-LATE`,
  `223-3.2-6-CANCELLATION-POSTED-LATE`,
  `223-4-12-PROTOCOL-PUBLICATION-LATE`
- `v348_cross_document_legal.py:cross_doc_clarifications/cross_doc_protocols`:
  `223-3.2-3-CLARIFICATION-CROSS-DOC-LATE`,
  `223-4-12-PROTOCOL-CROSS-DOC-LATE`
- `v372_auto_regulation_protocols.py:review_protocols`:
  `v372-protocol-rejected-bid-no-reasons` (the explicit `violation=True` path)

The `v331`, `v336` review catalog, `v337`, and `v339` signals do not constitute
additional confirmed-rule emitters. The private restored production `entry.py`
is absent from this local checkout; unlisted legacy/runtime codes safely map to
`unknown`. New rule IDs require an explicit reviewed code change to this small
taxonomy; the runtime never discovers them from catalogs or caller content.

## Commit and writer boundaries

- General: one extra call immediately after the existing successful commit and
  commit marker. It uses the exact SQL insert tuples; only checklist rows whose
  stored status is `violation` are included. Isolated retry scopes merge their
  rule counters into the outer trace only on successful commit.
- Thematic: pass the existing final insert plans to the existing post-commit
  emitter, after the database connection closes. Only applied `completed` or
  `retry` items are classified. Reservations, campaign/company/period fences,
  snapshot rebases, and rollback behavior are unchanged. Plans discarded during
  rebase are never counted. Retry summaries never inherit an earlier rule delta.
- No new work is performed while the SQLite writer lock is held. Classification
  is linear in rows already inserted, with constant memory for 17 counters.

## Validation and integration

Focused command:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:scripts:tests python -m unittest \
  test_audit_rule_counts test_audit_funnel test_audit_persistence_lock_scope \
  test_thematic_persist_lock_scope test_general_audit_counters \
  test_general_thematic_parity test_legal_audit_revision
```

The new tests cover fixed taxonomy, unknown/malicious code redaction, output
sanitization, committed/applied gating, retry isolation, general findings and
violation-checklist rows, both queue paths, duplicate-only replay, transaction
rollback and retry, lost reservations, discarded snapshot plans, actual detector
parity, unrelated SQLite writes during classification, and broken diagnostics.
Result: **95 tests passed** (7.610 seconds), including 16 new tests. The local
legal-engine signature is identical to the baseline:
`1150cdd554dfe28ffeae233f3d6d89231ce13c7b974b1cb0225d71a6f77ccbac`.

Files: new `scripts/audit_rule_counts.py`, new `tests/test_audit_rule_counts.py`,
this document, plus `scripts/audit_funnel.py` and one-line integration changes
in `v332_all_procurements_audit.py` and `v345_thematic_accelerator.py`.
Docker/CI files are deliberately untouched to avoid the concurrent full-cohort
worker's scope. Add `test_audit_rule_counts` to the integrated build/CI list.
`audit_funnel` now imports `audit_rule_counts`; the new module must be copied
before any test imports the funnel. The existing early `COPY scripts/ scripts/`
already covers that dependency. Copy the new test after its existing fixture
modules (`test_general_audit_counters`, `test_thematic_persist_lock_scope`, and
`test_general_thematic_parity`) are available.
