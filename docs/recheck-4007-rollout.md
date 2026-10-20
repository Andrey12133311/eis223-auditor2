# Full-dashboard recheck requested on 2026-10-20

## Scope and activation

The user explicitly requested all 4007 procurements to be rechecked after correcting contention and improving legal analysis. The available production inventory already counted 4014 dashboard procurements at 2026-10-20T17:41:27Z; a displayed count is not a list to truncate or fabricate.

This release authorizes the fixed request ID `2026-10-20-all-dashboard-4007` with expected_count=4007. It freezes the actual complete dashboard membership once, records actual_count and its digest, and excludes manual-only purchases according to the existing dashboard ownership contract. All eligible saved dashboard entries are included even if actual_count differs. It neither limits membership to 4007 nor claims unverified purchases completed.

Activation requires explicitly setting `EIS223_FULL_RECHECK_REQUEST=2026-10-20-all-dashboard-4007` on the web service and starting this tested release. An old image does not understand this request; do not restart an old image with the new value. Runtime must confirm the new request ID, actual_count, membership digest and target_generation=9. Reusing the same ID on subsequent startups does not refreeze or duplicate membership.

The original request `2026-10-19-all-saved-214328`, its members and receipts remain intact. Switching the configured request does not delete old findings or mark old work completed. Expired leases from either explicitly authorized request are recovered only after the existing physical exclusion lock and ownership token checks; a live old lease is never stolen.

## Lock and snapshot changes

Repeated legal fact persistence now prepares outside the SQLite writer transaction and skips unchanged values. Changed values use the existing guarded transaction and a bounded optimistic rebase to avoid overwriting concurrent intake or a fresher customer/price correction. PR99 metadata preflight is included with real writer-contention and stale-lease tests. No source throttles, validation checks, durability settings or logging are disabled.

The opaque local dashboard source may take longer than a short SQLite transaction. This explicitly requested cohort has a separate bounded source-capture budget of 60 seconds, outside the writer, followed by the original short atomic database snapshot budget. Source and database capture times are recorded separately. An incomplete or budget-exceeded capture commits no partial request. Ordinary callers retain their earlier timing defaults. No additional analysis workers or EIS requests per second are introduced.

## Legal evidence chain

The user-provided official TFF 223-FZ 16.3, Appendix 1 pp.78-79, defines explanation purchaseRegNum, requestDate, decisionDate, creationDate and publishDate separately. The parser handles the documented explanation root with strict namespace/identity and attachment binding. An identity-only incomplete document remains quarantined, not silently promoted.

Clarification publication evidence is accepted only after revalidating the received XML/archive hash and the exact original-file hash and UID. Only a published explanation with consistent request, decision and publication dates can supply the missing response publication date for the same single request in that original. Creation and decision dates are not substituted for publication. Ambiguous versions, foreign UIDs, changed files, text/XML disagreement, draft status, missing dates, mixed requests and unproven applicability remain review-only. The existing legal deadline rule is not relaxed and findings are not manufactured to meet a quota.

The legal engine fingerprint includes the new verifier and the authorized target generation advances from 8 to 9. Historical completed receipts are not counted as current-engine completion until actually rechecked and committed. General and thematic persistence, duplicate collapse and evidence retention are covered by the regression suite.

## Validation and rollout

The dedicated integration run 39474231790 compiled the checksum-verified native SQLite 3.51.3 and passed 494 test executions across six isolated groups before committing the implementation. Final PR CI must also pass. Temporary compressed-patch transport and the writable branch-only integration workflow were removed from the final source; the remaining workflow is read-only.

After deployment, verify SUCCESS, health and successful heartbeat requests, `SQLITE_RUNTIME version=3.51.3 journal_mode=wal`, creation/progress of the new frozen request and preserved originals. Compare equal log windows for retries and accepted unique (reg, uid) files before reporting performance improvement. WAL still has one writer; neither a passing build nor a short zero-error window proves all future contention is eliminated.

The existing backup-first WAL runtime is retained. Never roll back to an old image with unpatched SQLite or delete WAL/SHM files to clear locks. This change does not upgrade the external VPS from 1.4.0 to 1.4.1; its version and size-limit failures must be checked separately.
