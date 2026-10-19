# Owner-scoped legal recheck

## Scope

Base: `446c73b` on `thematic-audit-20261015`. Local package only; no production requests, publication, migrations, credentials or new endpoints.

Both existing authenticated owner paths now use the same internal start helper:

- `POST /me/manual-checks/{reg_number}` keeps its canonical 223-FZ validation, origin check and authenticated-owner association.
- `POST /me/workbench/{reg}/recheck` keeps its origin, session and existing-owner checks.

The trusted application's SQLite connection supplies the `main` database filename through `PRAGMA database_list`. The original root is that database's parent, matching `app.py`'s existing DATA layout. Query/body fields cannot select a database, original root, campaign or additional registry numbers. Only the explicitly validated registry number enters the scoped helper.

## Ordering and concurrency

1. Obtain a read-only one-reg plan using the existing `scoped_legal_recheck` utility.
2. Enqueue or reuse the existing unfinished EIS download.
3. Save its association with the authenticated owner in PostgreSQL.
4. Apply the existing scoped helper's authorizer-protected compare-and-set.

Taking the plan before download/link I/O prevents a delayed owner link from resetting a newer result completed during that request. The apply step rechecks state, `updated_at`, retry timing, original availability and original-row identity under the utility's existing transaction rules. Pending/running work is preserved. Concurrent starts reuse one download and produce at most one reset from the same terminal snapshot.

Only `v332_audit_queue.state` and `next_retry_at` can change in the legal reset. Existing findings, checks, source bytes, audit revision, evidence fingerprint, run/result tokens, prior metrics, manager decisions and signoffs remain unchanged. There is no global revision bump, bulk requeue, worker invocation or result deletion. Workers continue their normal polling and claiming.

The two databases do not share a transaction. PostgreSQL failure leaves the real download queued but does not apply the explicit legal reset. A subsequent legal-reset error returns HTTP 503 stating that the saved download exists but the legal recheck is unconfirmed. Raw exceptions, internal paths and database details are not returned. A retry reuses unfinished work; this is pending/running idempotency, not permanent request-key deduplication after a completed rerun.

## Response semantics

The existing response fields remain. The additional `legal_recheck` object contains only `action`, `reason` and observed `state`:

- `queued / explicit_scoped_recheck / pending`: this request changed the eligible terminal row.
- `skipped / already_pending` or `already_running`: existing work was left untouched.
- `skipped / missing_purchase`, `missing_queue_row` or `no_local_original`: intake/download continues; no legal run is claimed.
- A concurrent newer result or original change is reported with the scoped helper's specific skip reason.

This queues work; it does not certify a new completed legal analysis. The owner GET reports `pending` and `legal_review_completed=false` after a successful reset while preserving historical document counters. Existing frontend wording/layout is unchanged in this narrowly scoped package.

## Compatibility

`scoped_legal_recheck.py` is not edited or included. The frozen evidence-retention package at `07a2fad` adds `partial_review` and `.xlsm` eligibility to that utility. This adapter imports the utility dynamically and does not copy terminal-state/extension lists or hardcode its SQL placeholder count. Compatibility is tested in a separate disposable checkout with that exact utility version; neither frozen worktree is changed.

## Local verification

Commands use `PYTHONPATH=.:scripts` and `/tmp/msp-reassessment-venv/bin/python`.

- `-m unittest discover -s tests -p test_account_owner_legal_recheck.py -v`: route integration tests with local SQLite files, real session/ownership SQL (only PostgreSQL syntax is translated), real download queue and real legal-worker claim. Covers both routes, exact-reg isolation, auth/blocked/expired/revoked sessions, CSRF, cross-owner denial, canonical validation, repeated/concurrent starts, worker claims/completion races, original removal, missing intake state, partial failures, trigger denial, path injection, and truthful owner GET status.
- `-m unittest discover -s tests -p test_scoped_legal_recheck.py -v`: 14 existing utility tests pass.
- `scripts/v366_manual_cabinet_selftest.py`: passes; its static source assertion now points to the shared helper.
- `scripts/v370_workbench_selftest.py`: passes.
- The broad account test run reproduces exactly the baseline's seven failures and four errors, independently verified in a clean detached `446c73b` worktree. Those existing migration, cookie/login, memo and workspace-contract failures are not changed by this package. It is not claimed as an aggregate green suite.

No live EIS retrieval, PostgreSQL connection, legal rerun, deployment or production data mutation was performed.
