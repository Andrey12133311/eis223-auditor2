# Procurement 32616324151: bounded rule trace

Date: 2026-10-19. Original investigation base: `0d92d05`; original patch:
`83316d0`. Reconciled locally onto recovery base `9e893f2`, descended from
production-branch commit `5e97fc6`. Local investigation only.

## Limits

This is a synthetic replay of two source excerpts supplied from the saved case
view, not a production recheck or a review of all procurement originals. Receipt
metadata in the tests is simulated. No production database, job, authentication,
GitHub, or deployment was changed.

The reported saved case had 21 read documents out of 25 stored, one older finding,
and 68 review items. Those counters are not reproduced or validated by this replay.
Later changes dated September 2, 3, and 4 were reported, but their full contents
and associated deadlines were not supplied. No current deadline or real legal
breach can be concluded from the initial excerpt.

Separately, the integrator reported runtime archive reprocessing at 19:20 and
19:26 UTC on October 19 and a `V324 review_only=2` trace. Those operational
observations were not independently replayed here. Final persisted findings
and displayed counts remain unverified; that trace is not evidence that this
V339 repair is deployed or that this procurement has a confirmed violation.

## Supplied evidence

- Information-card excerpt: document `Закупочная_документация.7z / Раздел2 -
  Информационная карта.docx`, EIS attachment UID
  `5374AD4826C24C4D8D55AE95511F631A`. It contains the labelled deadline
  `Дата и время окончания срока подачи заявок | 03 сентября 2026 г.` and a
  preceding fragment about notice publication on August 26.
- Participation excerpt: `Раздел_1___Документация.pdf`, EIS attachment UID
  `FB8537E35E4A4D4C8AD760E393083536`. It starts `В конкурсе в электронной форме
  могут принять участие только субъекты малого и среднего предпринимательства...`.

The exact supplied excerpts are fixtures in `tests/test_procurement_fact_trace.py`.
September 10 and the additional event/card values used below are hypothetical
controls, not observed procurement facts.

## Replay results and gate reasons

| Stage | Findings | Checks / facts | Reason |
| --- | ---: | ---: | --- |
| `document_facts` | n/a | 1 fact | Explicit deadline parses to 2026-09-03, including Cyrillic month and table separator. |
| Method / publication | n/a | 0 facts | No accepted explicit method label or complete publication-date label in the supplied fragments. Words referring to a competition alone do not supply the method. |
| Competitive applicability | n/a | unknown | Neither method field is present. |
| Exclusive MSP resolver | n/a | unknown | Supplied sentence lacks the current-tender declaration or authoritative structured source required by the conservative classifier. Classifier unchanged. |
| V331 text review | 0 | 0 | No narrow text pattern matched. |
| V331 coverage review | 0 | 2 | Notice original and regulation original absent from this two-excerpt fixture. This is not a production coverage claim. |
| V336 deeper review | 0 | 0 | No explicit eligible clause matched. |
| V337 text review | 0 | 1 | `v337-submission_start`: the beginning/end-of-applications review category matches. |
| V336 bid security | 0 | 0 | Competitive scope unknown; no security clause or verified NMCD supplied. |
| V341 precise rules | 0 | 0 | No request/answer, cancellation or protocol event chain; no payment condition supplied. |
| V372 regulation/protocols | 0 | 0 | Neither type of original supplied. |
| V348 cross-original events | 0 | n/a | No clarification/protocol event pair supplied. |
| V330 amendments | 0 | 0 | No actual amendment decision/publication evidence supplied. History is isolated as empty for this test. |

`v337-minimum_bid_period_by_method` is a catalog entry marked
`requires_external_or_structured_evidence`. It is not a confirmed-finding detector
and is not, by itself, emitted as an automatic review item. It requires method,
scope, dates and calendar. This is an identified automation-coverage limit, not
evidence of a violation in this procurement. No statutory threshold was added.

## Reproduced and repaired parser defect

`v341_eis_facts` already understands the supplied date. The independent V339
card/document comparison accepted only numeric dates and did not accept `|`
between a label and its value. Consequently the exact same date disappeared
from mismatch review.

With a hypothetical current card deadline of 2026-09-10:

- Before: zero V339 deadline comparison items.
- After: one sourced `status=check` item comparing 2026-09-03 and 2026-09-10.
- Confirmed violations: zero both before and after.

Minimal repair: reuse the existing `DATE_TOKEN` / `parse_day` grammar and accept
DOCX table/full-width-colon separators in the two existing date comparisons.
No method/MSP classifiers, legal thresholds, confirmation rules, result ranking,
or saved results are changed. The item explicitly calls for comparing document
versions and is never promoted to a confirmed violation by this module.

The runtime patch applies without changes to the recovery base. The only added
release integration is a Docker `COPY`/test command and the matching throughput
workflow path/test entries. The existing canonical legal-rule signature changes
with this parser repair, so the rule-change detector can observe it without a
manual revision bump. That offline signature check is not proof of a completed
production recheck.

## Amendment safeguards verified

- Distinct initial and amended deadlines are not merged into one current
  document-derived fact.
- An amendment published after the initial deadline does not borrow that date
  from an old document or the card: a missing deadline belonging to that exact
  amendment produces review only.
- A hypothetical amendment with its own later deadline does not trigger the
  change-after-deadline finding despite the older card value.
- Matching calendar dates with different times produce no mismatch.
- Invalid dates, unlabelled document dates and a generic duration produce no
  date comparison item.
- Unchecked documents and dates separated from the submission label by another
  event label are ignored.
- Checked unverified text retains V339's existing review-only behavior and is
  explicitly marked unverified. It supplies no authoritative document fact or
  confirmed precise-legal finding.
- An archive-member review retains the exact document/member name, original
  source URL, card URL, excerpt and version caution. Repeated dates are
  deduplicated without removing distinct source documents or mutating inputs.

## Validation

- Bounded trace/date/version/source tests: 18 passing, including 16 date-format /
  separator subcases.
- Existing applicability tests: 26 passing.
- Existing amendment-evidence recall tests: 16 passing.
- Existing conservative MSP classifier tests: 19 passing.
- Existing legal-event recall tests: 28 passing.
- Existing amendment-history intake tests: 18 passing.
- Existing V339 factual-comparison, V341 facts/legal, V372 automatic-legal and
  V336 legal-matrix selftests: all four passing.
- Targeted total: 125 unit tests plus four selftests; no-write Python compilation
  and `git diff --check` passing.
- Current-base source/receipt, automatic-recheck, event/security applicability,
  scoped-recheck and writer-lock regression modules: 226 additional tests pass.
  The first attempt with the default interpreter had an import error because
  `email_validator` was absent. The complete suite then passed using the
  preexisting complete test environment; no application fix was needed.
- Before/after comparison against `9e893f2`: the supplied excerpt plus the
  hypothetical September 10 card produces zero then one `status=check` item;
  confirmed violations remain zero.
- 216,000 numerical date cases preserve the base parser's result, including
  all years 2000–2099, months 0–14, days 0–35, padded/unpadded formats and
  time-suffixed values. An independent review also checked 28,560 date cases.
- Independent review verified that importing V339 performs no writes, SQLite
  opens, network access or subprocess actions, and introduces no import cycle.
- The newly added Docker/workflow test command passes all 18 tests locally.
  A Docker image build was not run because Docker is unavailable here.

Reproduction commands, from the repository with its declared test dependencies:

```sh
export PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:scripts:tests
python -m unittest test_procurement_fact_trace test_legal_applicability test_amendment_evidence_recall test_v383_msp_scope test_legal_event_recall test_amendment_history_intake
python -m unittest test_semantic_audit_sources test_legal_audit_revision test_automatic_review_scheduler test_intake_automatic_rechecks test_automatic_card_handoff test_automatic_legal_status_ui test_automatic_legal_outcomes test_receipt_original_parity test_official_publication_evidence test_legal_event_coverage test_legal_security_clause_coverage test_scoped_legal_recheck test_thematic_persist_lock_scope
for test in v339_main_facts_selftest v341_facts_selftest v372_automatic_legal_selftest v336_legal_selftest; do python "scripts/$test.py"; done
python -m unittest discover -s tests -p 'test_procurement_fact_trace.py'
git diff --check
```

These are offline targeted checks. They do not assert full-suite success, a
completed production reassessment, or that all 25 stored documents were read.

## Next evidence needed

To investigate a real missed submission-period or amendment finding, inspect the
complete current notice/XML, labelled method, authoritative MSP scope, original
and subsequent deadlines tied to their exact revisions, the relevant procurement
regulation, and the four documents not included in the saved read counter. Do not
substitute the initial September 3 date for a final deadline.
