# Audit recovery and observable legal processing

Base after concurrent-change reconciliation: PR72 merge
`98ac0f2ae804965af48356e00dd7283767d67c2c`. PR72's snapshot/rebase persistence,
bounded prepared-result recovery, legal recall, XML quarantine and v387/version 5
remain intact. Diagnostic plan counters are published only for the accepted
plan; discarded rebase attempts do not inflate them.
This bounded package combines independently reviewed fixes; the separate 25-file
storage/completeness migration and VPS worker changes are excluded.

## Included changes

1. Cancellation fixture: gate the actual SQLite writer transaction before commit,
   instead of pausing before BEGIN and assuming it must succeed. Preserve commit,
   lease-release and exactly-once credit assertions. Add a real >3-second commit
   hold and a cancelled SQLITE_BUSY rollback/restart test with historical rows.
   Runtime cancellation/retry/fencing code is unchanged.
2. Date comparisons: use existing Russian-month/date grammar and table separators
   for labelled V339 review evidence. This produces a sourced review request, not
   a confirmed violation. See the bounded procurement trace in this directory.
3. Archive occurrence reads: retain distinct same-path ZIP/TAR entries and prevent
   nested/literal source-identity collisions; identical same-path byte copies stay
   one evidence item. Limits and existing verified-cache recovery are preserved.
4. `AUDIT_FUNNEL` diagnostics: fixed allowlisted counts/reasons describe actual
   general and thematic boundaries through post-commit persistence. Optional
   procurement numbers are validated as 11–20 ASCII digits at entry and output.
   No text, document names/URLs, user/owner IDs, secrets or arbitrary exception
   strings are emitted. No additional database reads or document parsing occurs.

## Build failure evidence

PR71 Railway deployment `d58776b2-cd8e-483c-9bc9-061bb75ae84a` failed at
2026-10-19T20:26:07Z on the old cancellation fixture's `findings == 1` assertion.
The same assertion was reproduced on untouched PR71 by placing a competing
writer at the old pre-transaction gate: its test-configured 50 ms timeout causes
SQLITE_BUSY, cancellation settles rollback, and the reservation returns pending.
Railway did not log that swallowed exception, so this reproduction does not prove
which exception occurred in that historical build.

The original revised 20-test recovery module passed normally and pinned to one CPU with
four bounded CPU competitors. Independent mutations abandoning persistence or
releasing a lease twice fail the strengthened assertions and terminate cleanly.
No assertion is removed to make an unsafe commit path succeed. Reconciliation
also retains PR72's independent cancelled-BUSY/reclaim case. The commit gate
recognizes both single inserts and the new batched `executemany` inserts.

## Reading diagnostics accurately

- General inspection emits one `stage=complete` event; standalone persistence
  emits `stage=persist`. Nested persistence merges into the inspection event.
- Thematic emits `stage=analysis` and `stage=persist`, correlated by random trace
  and optional validated procurement number. Analysis counts are repeated; do not
  sum both stages. Aggregate inserted rows from persist events only.
- `analyzer_candidates` and `checklist_candidates` are returned engine outputs,
  after internal detector gates. Silent internal rule exclusions are not counted.
- Counts describe rows, not unique legal episodes. Existing unique-event logs
  remain separate. `analysis_observed=false` means unknown analysis coverage.
- `committed=true` records transaction completion; `persist_applied=false` can
  indicate a stale reservation or other fence. Failed transactions do not emit
  committed deltas. Emission follows SQLite and intake-guard release.
- Logging failure cannot cause a legal retry or invalidate a committed result.
- Thematic review rows remain unpersisted under existing behavior.

Synthetic detector proof: three verified documents produce three confirmed
candidate rows and one review row on both first run and replay. First persistence
adds3/1; replay adds0/0 and reports four duplicates. These are fixtures, not a
production finding-growth claim.

The observation calls alone preserve the legal-engine signature. An explicit,
small allowlist excludes only standalone thematic observation statements with
inert name/literal arguments; gates, assignments, nested execution and legal
changes remain signature-relevant. Date/archive repairs intentionally change
the signature, allowing the approved bounded automatic scheduler to re-examine
eligible sources without a manual rule-revision bump.

## Release checks and boundaries

Each new test module is included in throughput CI and Docker build gates. Local
Docker/bsdtar are unavailable; local Python gate execution is not represented as
a successful container build. Full-suite baseline failures remain reported.
Exact combined test results and source tree are recorded in the release manifest.

This package adds no database fields/tables, credentials, endpoints, access
changes, new legal thresholds, destructive history changes or resource-limit
increases. Existing automatic queue fields from the separately approved PR70
remain part of the eventual deployment. Real queued work and persisted findings
must still be verified after Railway reports a successful installation.

## Reconciled validation on PR72

The PR72 baseline has 1,336 tests. The reconciled candidate has 1,399 tests:
exactly the same eight failures, seven errors and six skips, with no new failure
or error identity. The 305-test targeted run and 24 frontend scenarios pass.
The 21-test cancellation module passes both normally and under one-CPU contention;
mutation probes still reject abandoned persistence and duplicate lease release.
Four new diagnostics regressions verify that discarded snapshot plans do not
inflate counters and that private telemetry neither consumes prepared-result
budgets nor pretends cached results were freshly analyzed. The reconciled legal
signature is `1150cdd554dfe28ffeae233f3d6d89231ce13c7b974b1cb0225d71a6f77ccbac`;
removing only diagnostic observations leaves it unchanged.
