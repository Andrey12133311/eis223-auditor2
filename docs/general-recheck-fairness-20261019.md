# General automatic recheck fairness, 2026-10-19

## Reproduced blocker

Base: `dc4d1b91fcb18335d9678863d5c631ba7368d698` (PR73).
This is a separate scheduling patch, not part of PR74's semantic snapshot/index
performance changes. No production data was changed while preparing it.

`refresh_changed()` returned before inspecting any terminal row whenever
`ready_work()` was at least `READY_HIGH_WATER=16`. That count includes all
runnable legacy first-pass pending work, not merely recently admitted rechecks.
Consequently, an existing terminal result with an unknown or stale semantic/rule
watermark could wait indefinitely while first-pass work kept that count high.
This reproduces starvation/delay; it does not establish permanent result loss.

A finite local fixture completed 40 first-pass jobs and replenished each with
one new first-pass item, maintaining exactly 16 runnable pending jobs. Before
the patch there were zero terminal admissions, zero semantic inspections, and
the known terminal retained its old watermark after all 40 cycles. The new
regression also fails against the unchanged base implementation.

## Bounded worker handoff

The normal new-pending admission limit remains eight per sweep and its
high-water remains sixteen. `ready_work()` is unchanged.

When that gate is full, a bounded terminal scan may save one scheduling hint in
the existing `v382_audit_scan_cursors` table. Saving the hint does not change
any queue row, stamp a watermark, admit pending work, or reserve a worker.
The scan retains its 128-row page, five-second inter-row budget and persistent
keyset cursor. A terminal's future retry deadline is respected.

An existing worker attempting a claim can consume the hint:

1. Compare the original terminal row's state, update timestamp, source/engine
   watermarks, reservation token, retry state and last result token.
2. Recheck source readiness and semantic/rule freshness outside SQL writer
   transactions and under the existing physical file-I/O gate.
3. In a short writer transaction, compare the hint and exact terminal row again,
   recheck company/campaign scope and retry eligibility, and change only that
   terminal row to token-owned `running`.
4. Retain the running token in the durable hint slot until normal completion,
   retry or startup recovery. This permits at most one direct terminal handoff
   to remain running at a time.
5. Give ordinary pending work a turn before another direct handoff whenever
   ordinary readiness is present. A bounded unsuccessful ordinary receipt scan
   releases that same turn, so stale readiness hints and temporary receipt
   errors cannot veto terminal progress forever. A newer hint's turn is
   protected by the original-payload comparison.

Normal workers still capture their current pre-analysis watermark. A source
that changed after staging is inspected at its latest pre-analysis version; a
source that reverted to the last successfully analysed version is not rerun.
A change during analysis remains eligible for the next sweep. No new result is
acknowledged by scheduling alone.

## Tradeoff and preserved boundaries

This is a deliberate scheduling tradeoff. The sixteen-item value remains a
new-pending admission gate, not an absolute limit on an already larger legacy
backlog. An existing free worker may add one running terminal row to that
backlog; for an otherwise unchanged fixture with sixteen ready pending rows,
the counted ready total briefly reaches seventeen. No pending/running row is
displaced, delayed, reset or removed to create the slot. Normal retries and
restart recovery retain their existing pending/deadline behavior.

The four general workers and shared six physical file operations, including
the foreground reservation, are unchanged. The bounded scan and one freshness
recheck add some work during saturation; one existing worker can spend time
on terminal catch-up rather than first-pass work. This prevents starvation
without promising a fixed completion time. Polling, scan position, source
availability and inspection duration still determine latency.

No schema, authorization, legal threshold, legal revision, original, finding,
check, cumulative result count, approval or decision history changes are made.
The existing exact reservation fencing and append/dedup persistence remain in
use. Engine signature before and after is identical:
`1150cdd554dfe28ffeae233f3d6d89231ce13c7b974b1cb0225d71a6f77ccbac`.

## Verification

`tests/test_automatic_recheck_fairness.py` covers eighteen scenarios:

- Replenished 16-item first-pass backlog: three stale terminals each run once,
  interleaved with ordinary claims; both classes progress over forty cycles.
- Hint-only staging, unchanged tables/schema and unchanged pending/terminal rows.
- At most one running direct terminal, concurrent scanners and claimers,
  persistent turn/cursor behavior, restart recovery and stale-owner rejection.
- Source reversion, latest source before claim, and changed source during work.
- Snapshot errors, racing reservations, customer/campaign scope, future retries,
  unknown historical watermarks, receipt-only sources and stale receipt hints.
- Zero admission budget and a late-page candidate after 300 terminal records.
- Real verified-original extraction, legal analysis and persistence: the
  competitive late-reply fixture produces one evidence-backed episode; the
  noncompetitive control produces none; manager decisions are preserved.

An existing receipt-parity test is updated for the intentional healthy direct
claim. Its assertions still check stale readiness cleanup, pending guards and
bounded remaining normal admissions.

Independent review found one receipt-cache alternation edge, now fixed and
covered. Its additional stress run used two simultaneous scanners and four
claimers, with 24 stale terminals above a constant sixteen-item pending
backlog. All 24 terminals completed, without duplicate or simultaneous direct
terminal handoffs.

Final local validation:

- Focused scheduler/recovery, receipt, DB retry, worker-resource and engine
  signature modules: 125 tests passed in 22.031 seconds.
- Full unittest discovery: 1,417 tests in 80.513 seconds; the same eight failures,
  seven errors and six skips as the clean 1,399-test base. The fifteen anchored
  failure/error identities match exactly; this is not a clean full-suite pass.
- `scripts/v372_automatic_legal_selftest.py`, Python compilation and
  `git diff --check` passed.
- The full/focused validation used the existing accounts-capable Python virtual
  environment; default Python lacked dependencies required by account tests.

Validation uses `PYTHONPATH=.:scripts:tests`. The new module must be added to the
explicit unittest module lists in the image build and throughput workflow when
this separate patch is integrated. Those two files were left untouched here to
avoid mixing with the concurrent PR74 release.

Deployment and live-runtime verification remain separate. Useful observations
are `GENERAL_RECHECK_SWEEP ... handoff_staged=1`, followed by
`GENERAL_RECHECK_CLAIM ... direct_handoff=True`, normal terminal completions,
and the unknown semantic/rule cohort shrinking while first-pass work continues.
No mass reset or direct production queue maintenance is needed.

## Integration on PR74 and diagnostic corrections

The release is reconciled onto PR74 `b79416ab8ed6d7c0b3a22a718987ca4d134ebbc1`.
Semantic snapshot/cache progress and bounded thematic indexing remain intact.
The new fairness module is mandatory in the existing Docker/CI command lists.

Existing `AUDIT_FUNNEL` events gain five fixed counters for analyzer kinds:
violation, check, risk, other and invalid. These classify returned rows, not
statutory breaches, and read no evidence/text/source names. Non-dict rows are invalid; dictionaries with missing or arbitrary kinds are
grouped as other. No per-row log stream
or arbitrary labels are added. Counts remain cumulative per trace, so thematic
analysis/persist records must not be summed together.

The existing FAS diagnostic previously called the mere presence of price_num
`nmcd_verified`; it is now `price_present`. `agreement_scope_confirmed` refers
only to the observed agreement cases, not an overall yes/no MSP determination.
Case counts now explicitly use `agreement_cases`, `definitive_agreement_cases`
and `collective_admission_cases`. Two additional bounded case counts distinguish
absence of confirmed scope from unproven application duty. They reuse existing
case classifications without rerunning the resolver or price verifier. Invalid
registration strings are omitted. External consumers of the old diagnostic
field names would need updating; no repository consumers were found. Finding,
checklist and legal AST outputs remain unchanged.

The PR74 post-merge diagnostic test exposed a fixture synchronization race:
`defer_retry` can commit in a worker thread before the async continuation prints
its log. The test now deliberately holds that post-commit boundary, verifies
all original DB/watermark/backoff/attribution assertions, releases the thread,
and separately waits for the actual log before checking classification. Its
five-second limits and all original assertions are preserved. No production
retry behavior is changed. A held-boundary replay fails on the original test;
the corrected 153-test gate and 20 single-CPU stress repetitions pass. Mutations
for wrong database-busy classification or missing error clearing still fail.

Coverage caution: the 229 catalogue entries are review signals/descriptors,
not 229 automatic legal validators. Five non-violation analyzer rows in a live
trace do not establish five missed violations. Without the original source
content, a zero result's legal correctness remains unverified. The additional
telemetry is diagnostic evidence only and does not promote review rows.

## Final combined release validation

- 200 targeted tests passed after combining PR74, fairness and diagnostics.
- Independent review: 170 focused tests, the final 171-test eight-module gate,
  and 19 isolated COPY-context cases passed, plus an extra real cancelled-claim
  fencing check. No unresolved blocking findings.
- All 107 executable local Python build commands and 24 JavaScript scenarios
  passed. Container setup and bsdtar remain required in the real image build.
- Full suite: 1,449 tests in 82.196 seconds, with exactly the PR74 baseline's
  eight failures, seven errors and six skips. All fifteen failing/error test
  identities match; this does not claim a green full suite.
- Final legal signature remains
  `1150cdd554dfe28ffeae233f3d6d89231ce13c7b974b1cb0225d71a6f77ccbac`.
- Exact release scope: ten files; no storage migration or VPS changes.
