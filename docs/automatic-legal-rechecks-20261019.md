# Automatic legal rechecks, 2026-10-19

## Purpose and boundary

Saved procurements should be revisited automatically when authenticated source
material or the installed legal rule set changes. A browser session and a manual
recheck button are not part of normal operation. This package changes scheduling,
source-change detection and truthful status presentation; it does not lower the
legal evidence threshold or promise detection of every legal violation.

Development began on PR68 `264921ebd7f689ec12c99730b3b2d543c17bb195`.
Before release the patch was reconciled with concurrent PR67/69 base
`c3211e27c48812885d5a6537a58807cd2063f12b`, preserving its legal coverage,
receipt-only collection, RULE_REVISION and ANALYSIS_VERSION=4 changes. The separate 25-file extraction/storage migration, date-comparison fix
and VPS delivery-fairness package are excluded.

## Eligible saved cohorts

- Existing current general campaign members, with the existing company gate and
  locally runnable originals. No new campaign or bulk status reset.
- Existing items of active thematic campaigns, respecting owner, legal form,
  category and inclusive publication-period restrictions. Stopped or deleted
  campaigns do not restart.
- Existing original-intake procurements not currently served by a runnable
  general/thematic owner, including interrupted initial intake recovery.

Legacy completed rows lack the new semantic/rule watermarks. They are unknown,
not silently declared current: normal bounded sweeps gradually catch them up.
There is no deployment-wide `UPDATE ... SET state='pending'` for completed rows.

Read-only production telemetry at 2026-10-19T17:51:25.527140538Z reported 1,907
members in the current general campaign: 954 completed, 953 pending, zero
running and 89 scheduled retries. These are **not** the number currently
eligible for automatic recheck. Ready-document, company and period guards
reduce eligibility. The separate manual and thematic counts were not available
through the permitted runtime access. Startup/sweep diagnostics provide actual
cohort/admission observations after an authorized installation.

## Meaningful inputs, not operational noise

This package preserves the reconciled baseline implementation of
`audit_source_version.source_version` for existing consumers such as MSP
reassessment. PR67 changed that legacy format; this package does not revert it. New scheduler snapshots compare current
receipt-verified original content, official XML revision/event identity,
validated browser history and authoritative current card facts. A snapshot is
an invalidation signal, not legal proof; analyzers revalidate evidence.

Touching an unchanged file, replaying a capture, regenerated extraction metadata,
cache annotations and capture timestamps do not create another legal event.
Missing/corrupt/repaired bytes are distinguished. A cache uses the full physical
file identity and expected content hash, not mtime alone. Hashing is bounded,
resumable and outside SQL writer transactions; partial snapshots are never
acknowledged as successfully analyzed inputs.

The installed engine signature hashes an explicit AST dependency registry of
legal analyzers, evidence readers, qualification and documentary deduplication.
Comments, docstrings, UI changes and queue timing do not change it. Existing
RULE_REVISION and ANALYSIS_VERSION constants are included but are not bumped by
this package. A newly installed substantive rule change invalidates all covered
queues through the same signature.

## Scheduling and preservation

- Reuse existing poll/evidence notifications and worker capacities.
- General scanning uses bounded keyset pages and durable cursor state, at most
  eight new admissions per pass and a runnable high-water mark of sixteen.
- Missing-original backlog and future retries do not consume runnable capacity.
- Pending/running reservations are not reset by another source/rule notification.
- Thematic processing retains the existing four-worker, eighteen-per-campaign,
  thirty-six-per-cycle limits, with fair campaign/item progress.
- Saved intake scans 32 records per source page, admits at most two records per
  tick, and shares four task slots. Same-input failures stop automatic attempts
  after four failures; genuinely changed input gets a new attempt budget.
- Initial orphan recovery repairs only receipt/hash-verified pending-text
  pointers: at most eight candidates, 64 MiB and five seconds between candidates
  per record, and two repair batches per tick. No document is marked checked
  merely because its pointer was restored. The short SQL adoption transaction
  performs only bounded file-identity stat checks, never byte reads or hashing.
- Capture source and engine watermarks before analysis. An arrival during an
  active run remains eligible for a subsequent pass.
- Save unique documentary episodes through the existing append/dedup logic.
  Preserve findings, supporting evidence, manager decisions and approval history.
- A later direct original upload also uses append-only inspection whenever saved
  legal results or successful coverage already exist, including after a partial
  or failed run or a missing review-state file. The legacy initializer is retained
  only for genuine first intake. A history-ledger copy is not treated as a
  substitute for preserving active finding IDs and their approval references.
- A changed input arriving after completion is written but before the execution
  lock is released is durably marked pending. The old reservation remains
  exclusive until physical work settles; the arrival cannot receive a false
  current-completion acknowledgement.
- Reconcile interrupted work after restart; reject writes from superseded
  reservations, including analyzer-derived metadata writes.
- Errors retain their diagnostic and bounded retry state rather than pretending
  the old result is a new successful review.

Card-price changes no longer wait for the entire general campaign to finish or
invoke a second independent analyzer. Their marker is handed to normal queue
monitoring and labelled `automatic`, not `completed`.

## UI and authorization

The general staff recheck button and its browser POST click handler are removed. Initial
registry/INN/bulk intake, read-only refresh, employee decisions, private exports,
manager controls and protected backend routes remain. The specialized MSP
reassessment journal control is distinct and remains available.

Queued/running status is shown separately from download completion. Previously
saved findings remain accessible and are labelled as previous results while a
new analysis is outstanding. A zero-document completion is not presented as a
successful legal check. Opening or refreshing a card does not trigger a write.

## Deployment, rollback and evidence limits

Publication and installation require approval for this package's final file
scope and its bounded catch-up behavior. No production rechecks or schema
changes were performed while preparing it. Additive scheduler columns and a
small cursor table do not rebuild the evidence tables; no credentials, access
rules, schema drops or destructive result migrations are included.

Schema delta relative to reconciled base c3211e2 is additive:

- `v332_audit_queue`: `last_semantic_version`, `last_engine_signature`,
  `reservation_token` (TEXT with empty defaults), and `receipt_ready` (INTEGER
  default 0, a scheduling hint only, never evidence proof).
- `thematic_items`: the same three TEXT fields plus `next_retry_at` (REAL
  default 0).
- `v382_audit_scan_cursors`: `scope` TEXT primary key and `cursor` TEXT.

No procurement, document, finding, check, approval or history table is rebuilt.
Startup catches up legacy unknown watermarks through bounded normal admissions;
ready/running capacity limits still apply to receipt-only sources.

A rollback to the reconciled base (or PR68) can leave additional scheduler columns/table in place; the
old code ignores them. Prefer graceful shutdown so thematic reservations return
to queued. If rollback follows abrupt termination, token-owned thematic running
reservations require a queue-only recovery to queued through the normal
authorized runtime workflow; the older scheduler does not understand that new
state. Never reset findings, counters or signoffs for rollback.
Do not remove data/columns or restore an older database
snapshot over newer decisions. A runtime rollback or writer maintenance window
must use the existing authorized administrative workflow.

The browser recovery action previously returned `next_action=stop`; this package
is prepared without retrying that browser action. Real outcomes for procurements
32616233661 and 32616324151 remain unverified after PR68. The earlier unsupported
SOAP XML422 case is a source-availability blocker, not proof of a legal violation.
Automation cannot manufacture missing decision/publication dates.

## Verification record

The final release report must distinguish:

1. Positive synthetic examples run through real analyzers and persistence.
2. Negative controls: noncompetitive scope, missing dates, unverified sources,
   duplicate/repacked/copy evidence, unchanged bytes and operational metadata.
3. Queue concurrency, retry, restart, late-page progress and stale-writer tests.
4. UI/auth/read-only behavior and unchanged decision/history records.
5. New targeted tests versus pre-existing full-suite failures.
6. Local fixture counts versus subsequently observed production results.

New targeted checks are included in GitHub throughput CI and the image build.
Final validation of code/test commit `cdb5f377ef3482b0d319e2da2b1fe0acbe684c29`
completed at 2026-10-19T18:50Z, after independent review. The release contains
41 files: 16 production Python files, four Python selftests, eighteen files in
`tests`, two CI/image-build files and this document. Remote base was fetched again
at 18:50Z and remained `c3211e27c48812885d5a6537a58807cd2063f12b`.

- Seven new unittest modules: 132 tests, all passed (19.825 seconds).
- Full unittest discovery: 1,244 tests (77.358 seconds), exactly the same eight
  failures, seven errors and six skips as the clean 1,108-test base below.
- All 99 locally executable Python image-build validation commands passed.
- Frontend request/race tests: all 24 scenarios passed.
- `git diff --check`: passed.

The seven targeted modules are `test_semantic_audit_sources`,
`test_legal_audit_revision`, `test_automatic_review_scheduler`,
`test_intake_automatic_rechecks`, `test_automatic_card_handoff`,
`test_automatic_legal_status_ui` and `test_automatic_legal_outcomes`. Commands use
`PYTHONPATH=.:scripts:tests python -m unittest` followed by those modules; full
discovery uses `python -m unittest discover -s tests`; frontend checks use
`node --test tests/test_account_frontend_requests.cjs`. The Python image-build
commands were executed directly from Dockerfile RUN steps; this is not a claim
that a local container image was built.

A final regression initially used an incomplete three-column mock queue. It now
uses production initialization and proves exactly one append-only catch-up after
genuine initial intake, a current successful watermark, and three unchanged-input
replays without another analysis. The correction changes only that test fixture;
assertions were strengthened and the runtime review remains valid.

## Acceptance examples

The new end-to-end fixtures use actual DOCX extraction, authenticated source
receipts, current official card validation, the installed analyzer, real SQLite
persistence and the dashboard count reconciler. These are synthetic examples,
not claims about production results:

- A late clarification with an authoritative noncompetitive method initially
  yields zero confirmed findings. Correcting that official current method to a
  competitive procedure, with unchanged original bytes, automatically admits
  the procurement and persists one distinct proven episode.
- Replaying/touching those same bytes does not admit another run. An installed
  engine-signature change does admit a run, but the saved/displayed episode count
  stays one and the manager's existing approval remains unchanged.
- A genuinely noncompetitive case remains at zero after a rules rerun.
- Missing the required response-publication date remains a review, with zero
  confirmed violations after a rules rerun.

Pending/running source arrivals, retries and restart recovery are covered
separately. A source snapshot that cannot finish within the resource envelope
never becomes a successful analyzed watermark. Readable verified subsets remain
available under the concurrent baseline's explicit partial-source policy.

## Known baseline test debt

A clean detached c3211e2 checkout ran 1,108 unittest tests on 2026-10-19: eight
failures, seven errors and six optional skips. These predate this package; the
full suite is not green. The exact retained baseline is:

Failures:

- test_account_owner_isolation.OwnerIsolationTests.test_unowned_memo_download_returns_not_found
- test_account_private_api_v1.PrivateResourcesTests.test_unknown_memo_returns_404_not_storage_path
- test_account_production_v1.ProductionLoginTests.test_existing_session_skips_entry_forms
- test_account_workspace_contract.WorkspaceContracts.test_dashboard_api_fields
- test_account_workspace_contract.WorkspaceContracts.test_personal_summary_is_owner_scoped
- test_account_workspace_contract.WorkspaceContracts.test_personal_thematic_queue_controls
- test_account_workspace_contract.WorkspaceContracts.test_thematic_unlink_is_owner_scoped
- test_v262.V262Tests.test_existing_main_page_total_uses_exact_filtered_list

Errors:

- test_account_migrations_v1.MigrationTests.test_schema_runs_one_statement_at_a_time
- test_account_session_policy_v1.SessionPolicyTests.test_cookie_is_secure
- test_account_site_entry_v1.SiteEntryTests.test_logout_revokes_cookie_and_old_session
- test_account_site_entry_v1.SiteEntryTests.test_one_cookie_opens_site_and_cabinet_without_another_login
- test_v257.FeatureTests.test_dashboard_manual_isolation_and_findings
- test_v257.FeatureTests.test_new_manual_request_appears_without_restart
- test_v319_manual_inputs.ManualInputTests.test_old_source_and_download_labels_do_not_make_import_manual

No assertions or access checks were removed to make these pass. Docker itself
and bsdtar are unavailable in the native validation environment; the real image
build still retains the archive gate. The container-only /data directory setup
is not executed as a local test.
