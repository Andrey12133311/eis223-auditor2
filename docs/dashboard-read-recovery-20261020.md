# Dashboard read recovery, 20 October 2026

## Observed failure

Production was still serving bdcffad5. Main HTML returned HTTP 200 in 724/947 ms,
but /api/v257/dashboard was abandoned after 45,008/44,971 ms, in both the mobile
app and browser. A contemporaneous inventory spent 99,852.7 ms before its saved
snapshot completed; its verified-intake getter precedes that snapshot. The code
called that same restored source getter on every dashboard request. The exact
production contribution of each stage is not known until the new timing logs
are observed; deployment SUCCESS is not evidence that the table loaded.

## Changes and boundaries

- One per-app physical read of intake membership, not one scan per browser
  refresh. Cache only immutable procurement IDs, not users, legal verdicts,
  documents, counts or response bodies. Freshness is 30 seconds. A last real
  snapshot can be served for at most five minutes while a refresh is pending;
  responses and the UI label its timestamp, age and stale status.
- A cold scan gets a three-second caller wait. It continues in its original
  single-flight slot after a caller times out. Failure/missing source is HTTP
  503 with Retry-After, never a fabricated successful empty list. Only a real
  successful empty source produces empty membership. The cache does not write
  audit completion, a receipt, or a persistent counter.
- Exact current legal counts are still read from saved evidence for the full
  filter, not only the page. Current manual ownership and historical automatic
  queue membership remain independently read. The count/list read uses a
  consistent read-only SQLite snapshot; no writer reservation is introduced.
- Additive indexes on actual violation rows and purchase order are attempted
  once at startup, with bounded lock/statement budgets. An index failure is
  logged and leaves all evidence intact. Only the safe dashboard warming hook
  is retained by EIS-only startup; legacy source collectors stay disabled.
- Retire the obsolete v270 hard-coded 2-to-1 counter rewrite for one procurement.
  V352's evidence-based reconciler already handles duplicate episodes. Distinct
  genuine episodes must not be silently hidden. Preserve no-store response
  headers without buffering JSON. No saved finding is deleted.
- A same-origin dashboard-only browser observer explains warming/stale/failure
  states and offers one explicit retry, honoring Retry-After. It does not retry
  automatically, alter credentials/AbortSignal/filters/responses, swallow HTTP
  errors or intercept POST, authorization or other-origin requests.

## Legal processing and full recheck

This patch is based on dc95372 and retains PR100/101 unchanged: metadata/lease
write contention fixes, strict supported explanation XML, separate source
publication/decision/request dates, and the explicit frozen all-saved request.
No legal-engine generation changes are needed for these display-only changes.
Do not activate `2026-10-20-all-saved-explicit-2100` on the old live image. After
this image is confirmed healthy, the existing explicit epoch can capture the
actual saved list, including manual entries, without resetting prior requests
or moving those entries into the automatic section. Report the captured count
and committed completion, not the historical displayed number 4007.

## Rollout verification

Keep SQLite 3.51.3/WAL and existing verified backups. No data reset, manual direct
SQL, authentication bypass, source-rate-limit removal, or forced counter change
is part of this patch. Build success alone is insufficient: verify the exact
running commit, DASHBOARD_MEMBERSHIP_READY, index status, DASHBOARD_READ_READY
and real authenticated table responses. Compare accepted originals, errors,
queue and committed audit receipts in equal windows. If Railway refuses
deployment, report the infrastructure block; do not claim that the main page
or full recheck has been fixed in production.

## Live regression observed 21 October 2026

At 22:53 Europe/Moscow, the main mobile page showed `Загрузка...`, a blank checked-document counter, and a request timeout. Railway `web` production continued to serve deployment `d4445338` (commit `bdcffad5`) rather than the branch's reviewed dashboard recovery changes. HTTP logs contained `/api/v257/dashboard` client disconnects (499) after waits exceeding 40 s, and runtime logs showed concurrent SQLite `database is locked` retries and occasional PostgreSQL identity pool timeouts. The VPS 1.4.0 continued successful original uploads. These observations establish degraded UI/API responsiveness, not disappearance of stored purchases or a stopped VPS.

Release the already-reviewed branch through the existing `web` service, never by re-deploying the old `bdcffad5` image or creating a replacement service. Railway may reject deploys while paused. Verify a *new* successful deployment's commit SHA, `/api/v257/dashboard` and status response times, `DASHBOARD_MEMBERSHIP_READY` and `DASHBOARD_READ_READY`, and the existing acquisition/queue/DB invariants before claiming production recovery. This documentation-only change does not manipulate the production database, rate limits, audit state, or worker.
