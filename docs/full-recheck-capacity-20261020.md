# Bounded service for the frozen explicit recheck

## Scope and observed bottleneck

The existing general pool has four workers. Before this change, only worker 1
could run an explicit-only attempt and it alternated that work with ordinary
claims. With equal service costs and both queues continuously eligible, this
provides half of one worker, or 12.5% of the four-worker pool, to registrations
that depend on the explicit path. When ordinary work is empty, explicit work
can occupy one worker. Registrations outside the ordinary business-company
filter depend on explicit-only work. That filter is unchanged.

The frozen request remains the same request and cohort. A difference between
the captured and expected membership counts is not a reason to recapture
membership. A scheduling improvement does not establish readable
originals or verified completion. Missing/incomplete sources, timeouts and
retry backoff still constrain end-to-end results.

## Policy

Workers 1 and 2 now prefer explicit-only work. If no eligible explicit attempt
is obtained, either immediately falls back to ordinary work. Workers 3 and 4
remain ordinary-only. There are still exactly four general workers and no new
analysis pool. Two ordinary workers can make progress even under a continuously
eligible explicit backlog; all four can perform ordinary work when explicit
work is empty, disabled, conflicting or temporarily ineligible.

A nonwaiting admission counter caps explicit-only turns at two per application
runtime/event loop. The permit is acquired before waiting for selection and is
held through claim, analyzer and database settlement and
exact request lease/physical-lock cleanup. Detached read-only file work keeps
its existing shared physical permit until it exits; the explicit-turn cap does
not replace that file-resource bound. An extra caller immediately returns to ordinary work.
Ordinary-attached request receipts are not extra jobs: they continue to reuse
an ordinary reservation, as before, and do not take an explicit-only permit.

This is **not a durable cross-process cap**. Rolling processes can overlap,
just as before. Existing per-registration physical locks, ledger compare-and-
swap leases, exact claim tokens and cross-queue reservation checks remain the
cross-process protection. No active work is reset or cancelled by rollout.

## Selection and safety

One async selector owns the shared rotating cursor from recovery/catch-up
through candidate reads, wrap, visited-row advancement, physical lock and
committed claim. It releases before source reading and analysis. A cancelled
candidate or claim thread settles before releasing this selector; a committed
but cancelled claim is cleaned up under the same admission ownership.

The selector never advances to an unused page tail. Due earlier retries cannot
rewind a concurrent worker's cursor or starve fresh later members. Candidate
pages, catch-up limits, retry deadlines and lease durations are unchanged.

The existing shared physical file gate, foreground reservation, CPU/review
backpressure and SQLite single-writer transactions remain unchanged. There is
no extra writer pool, extraction pool or worker count increase. Source hashing
and legal analysis still run outside short writer transactions. Ordinary and
active thematic reservations remain blockers for the explicit path, and
explicit running members remain blockers for both other queues. Legal
applicability, evidence validation and completion requirements are unchanged.

## Reproducible proof

`tests/test_full_recheck_capacity.py` drives the actual installed general worker
loop with four equal-cost service quanta per barrier wave, not a wall-clock
speed assertion. Four waves on production base `634ee2e` produced:

- Baseline: 2 explicit-only and 14 ordinary service quanta.
- Patched: 8 explicit-only and 8 ordinary service quanta.
- Patched with an empty explicit request: all 16 ordinary service quanta.

This is a fourfold explicit service-share increase while both backlogs are
eligible. The explicit concurrency bound rises from one to two when ordinary
work is empty. It is not a fourfold production completion-rate prediction;
unequal analysis costs, shared resources and incomplete evidence still apply.

Permanent tests additionally cover two distinct real physical locks, a rejected
third explicit caller, concurrent ordinary and thematic reservations, 17-member
cursor rotation while earlier retries continually become due, cancellation
while waiting for selection, cancellation during a cursor-wrap thread, repeated
cancellation after a durable claim, real analyzer-thread cancellation, exact
stale-claim persistence fencing and same-membership restart recovery.

The capacity module contains 12 tests, including both coupled contention cases
and a separate delayed-renewal coverage case. The combined full-recheck command
also includes the deterministic ownership-race and turn-cleanup regressions. The
worker-share test fails on the clean production base with actual counts
`ordinary=14, explicit=2` instead of `ordinary=8, explicit=8`. The new module is included in
the existing throughput workflow's full-recheck test command.

The local checkout legal engine signature before/after this operational patch is identical:
`b72c97737ed696f00ab237416f181e3950cc56c6e1b378193f10d81b0dc4463b`.
The local checkout does not include the restored entry module; this is not a
claim of independently verified full local/runtime hash parity.
Correction generation remains 1. No schema, epoch, source registry, membership,
authentication or evidence-storage migration is introduced.

## Coupled SQLite contention comparison

A separate local proof compares this exact production-code patch with clean
`634ee2e`, using real temporary SQLite databases, authenticated local original
fixtures, the actual installed four-worker loop and one independent thematic
worker. No production busy timeout, lease duration or retry/backoff value is
changed; only the initial 14-second startup delay is skipped. Each test uses
barriers to observe real blocked operations before a fixed writer-hold window.

`FullRecheckContention` in the existing
`tests/test_full_recheck_capacity.py` covers two ungated pressure cases:

1. A writer is already held when workers start. Hold it for three seconds,
   exceeding the unchanged 2.5-second request/thematic busy timeout. Both base
   and patch make seven blocked admission attempts with two BUSY returns; peak
   waiting/entering writers is five including the independent thematic worker.
2. Admit the initial wave without contention, explicitly asserting two
   explicit-only plus two ordinary claims on the patch versus one plus three
   on the baseline. Then hold the writer during their real renewal and result
   stages, so cap-two occupancy is actually exercised under contention.

Both cases invoke one actual renewal tick through the unchanged `_renew` and
`db_stage` functions for all four live claims. During the three-second renewal
hold both builds make four attempts and receive four BUSY returns, then renew
all exact tokens after the existing retry delay. During the three-second
persistence hold both make six attempts with one BUSY return; peak contenders
is five including thematic persistence. During a 250-ms cleanup hold, all four
physical locks remain held until their cleanup transactions can finish, with
four contenders and no BUSY return. The single SQLite writer never exceeds one.

The corrected contention cases require all eight frozen members to complete
with exactly eight attempts, eight durable receipts and eight newly credited
findings. All four ordinary
members and the separate thematic member finish. Membership count, digest and
capture timestamp stay unchanged; active lease identities and attempt counts
stay intact across blocked renewals. Old tokens cannot persist after completion,
and another request turn adds neither a claim nor a duplicate credit. After
settled worker shutdown, every registration’s physical lock is reacquirable.

The tests explicitly use five- and six-thread executors. Test-only analyzer
barriers park asynchronously before real analyzer submission; synchronous
barriers previously exhausted a small executor and made four renewal entries
impossible. Both pressure cases leave real SQLite BUSY delivery and production
backoff ungated. A third case deliberately delays the fourth renewal until the
first three have returned BUSY. It proves exact claim coverage without relying
on a transient simultaneous-pending count; it is not retry-pressure evidence.
Shutdown begins only after all four workers and database operations are idle,
without increasing the existing shutdown timeout.

The contended-admission case emits four general retry lines, two thematic retry
lines and one request-deferred line on both builds. The initially saturated case
emits four general retry lines and one thematic retry line on both. Neither
emits a traceback. Total post-release no-op scan transactions are timing-sensitive
and are not presented as a fixed throughput comparison.

This demonstrates no increase in peak contenders or retry storm in these
controlled coupled workloads. It does not fix unrelated heartbeat/report
endpoint handling, establish production performance under sustained external
writers, or turn prolonged storage contention into successful completion. Those
remain separate rollout checks; scheduling publication remains gated on the
integrator's approval and verification of the live contention issue.

## Ordinary/request ownership handoff

A stale explicit candidate page could briefly take the physical lock of an
already claimed ordinary row before its database claim was rejected. Ordinary
attachment previously treated that transient lock conflict as permission to
continue without request identity. A controlled baseline case retained one
actual finding, but analyzed twice and attributed zero new findings to the
later request receipt. This was duplicate work and incomplete request
attribution, not proof that the finding itself was deleted.

Selection now skips currently running ordinary rows before taking their lock.
Because state can still change after that read, unsuccessful attachment to an
unfinished request raises a dedicated ownership conflict. The general worker
defers that reservation through its existing 60-second busy schedule, preserving
old results and exact tokens; no analyzer runs and no receipt is invented.
Completed, blocked and nonmember cases retain ordinary behavior. Fixed reason
labels omit exception text. Real-flock tests cover both race orderings, a
foreign process, stale claims and continued progress by another ordinary item.

Successful and already-started explicit turns now propagate cancellation only
after their existing shielded cleanup settles, including when cleanup itself
raises. Slot and permit cleanup remains unconditional. This is not a guarantee
covering every shutdown phase: the separate pre-existing claim-time combination
of cancellation plus physical-unlock failure remains outside this correction.
If an actual unlock fails before release, the retained physical handle keeps
that registration fenced until it closes or the process exits.

## Authenticated worker backpressure and failure diagnostics

The existing claim, bootstrap, report, INN-report, heartbeat and discovery
routes translate only recognized SQLite BUSY/LOCKED errors into HTTP 503 with
Retry-After 300. The client already persists that site-level pause. Transactions
roll back and close before the response; no uncommitted report is acknowledged,
no lease is extended by a busy response, and a later expired/foreign token is
still rejected. Fixed operation labels are logged at most once per operation
per 300 seconds. No database timeout, credential, endpoint or lease is added.

The shared authenticated request-stream reader handles only ClientDisconnect
as an incomplete HTTP 400 request, before any database function runs. It adds
no retry header or log. Malformed/oversized payload checks remain unchanged;
authentication still occurs before reading the body. Cancellation, unrelated
exceptions and disconnect exceptions outside streaming are not hidden. The
client retains pending reports on either non-success response; only 503
activates its existing global pause.

Thematic analysis emits fixed exception/source categories and an exact stale
reservation marker through the existing bounded funnel. No source content,
exception text, arbitrary labels, extra SQL or source reads are added. These
changes distinguish recoverable failures from valid zero findings. They do not
identify a long-lived writer or establish that production contention is solved.
