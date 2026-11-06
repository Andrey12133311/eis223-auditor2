# Dashboard availability during continuous EIS writes

## Observed behavior

The recovered production build `b606e86` responds to worker and status requests,
but `/api/v257/dashboard?offset=0&limit=100` repeatedly returns HTTP 503.
Finished dashboard calculations report 56.6 and 58.4 seconds. Ongoing worker
and audit commits advance SQLite's global `data_version` during each read.

The previous coordinator indexed both running reads and completed snapshots by
`(query, data_version)`. Thus a completed result was stored under a revision that
the next request could no longer reach. Polls also started redundant full reads,
increasing storage and CPU contention without making the page available.

## Correction

Running reads, retry state and saved snapshots now use a stable per-query key.
A source revision change starts at most one replacement read. A fast refresh is
still returned immediately; during a slower refresh the response can reuse only
the same query's successful snapshot, explicitly marked as stale with its age
and timestamp. Age starts before calculation, not when the long read finishes.
Display reuse is bounded to 180 seconds for this dashboard so the measured
minute-long refreshes can overlap the previous snapshot's display lifetime.
No cache value marks a legal check complete or changes source findings.

A separate read-only guard checks the indexed registry of explicitly submitted
purchase numbers. A change to that population prohibits reuse of the previous
shared-list snapshot; guard failures return an error rather than old rows.
Evidence edits that can be recalculated promptly remain immediately visible.
Filters stay isolated. Explicit default pagination now shares the startup
prewarm, while duplicate query-parameter order retains its original meaning.

The underlying cold full-filter reconciliation is still an exact calculation;
this change removes endless invalidation and duplicate scans rather than
claiming that every new cold filter is an instantaneous computation.

## Verification

Three controlled coordinator regressions fail on the previous implementation:
continuous commits create duplicate reads, a long read is falsely labelled age
zero, and a primed read is duplicated after a commit. They pass after this fix.
An HTTP integration regression verifies result availability amid real SQLite
heartbeat writes, correct stale labelling, and immediate exclusion after a
purchase enters the manual registry. Existing exact evidence-edit, filter,
cohort and private-header checks remain in the test run. A guard failure is
also covered with an already populated snapshot.

Docker explicitly copies each new test before running it. The dashboard
recovery workflow runs the coordinator and HTTP regressions. This source
change does not modify the production database, saved documents or queues.
Deployment and real authenticated-page verification remain separate steps.
