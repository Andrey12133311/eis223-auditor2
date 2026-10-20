# Full-dashboard recheck requested on 2026-10-20

## Scope and activation

The user explicitly requested all 4007 procurements to be rechecked after correcting contention and improving legal analysis. The available inventory already counted 4014 dashboard procurements at 2026-10-20T17:41:27Z; a displayed count is not a list to truncate or fabricate.

This release authorizes the fixed request ID `2026-10-20-all-dashboard-4007`, expected_count=4007. It freezes actual complete dashboard membership once, records actual_count and its digest, and excludes manual-only purchases under the existing ownership contract. All eligible saved dashboard entries are included even when actual_count differs. No unverified purchase is declared complete.

Activate with `EIS223_FULL_RECHECK_REQUEST=2026-10-20-all-dashboard-4007` on the web service using this tested release. An old image does not understand this epoch. Verify the new ID, actual_count, membership digest and target_generation=10 in runtime logs. Reusing the same ID on startup does not refreeze or duplicate membership.

The original request `2026-10-19-all-saved-214328`, members, attempts and receipts remain intact. Switching the configured request does not delete findings or mark old work completed. Expired leases from either authorized request are recovered only after physical-lock and token ownership checks. Live old claims are not stolen.

## Concurrency and snapshots

Metadata and legal facts prepare outside the SQLite writer and skip unchanged values. Changes use the existing guarded transaction and bounded optimistic rebase, preserving concurrent intake and official source precedence. The concurrent PR100 fixes for price writes and explicit date labels are retained, together with their tests. No logging, validation or source throttling is disabled.

The opaque local dashboard source has a separate bounded 60-second capture budget outside the writer, followed by the existing short atomic SQLite snapshot budget. Source and database capture times remain distinct. Incomplete captures commit no partial membership. Ordinary callers retain prior defaults. Worker counts and EIS requests per second are not increased.

## Legal evidence

The user-provided official 223-FZ TFF 16.3 Appendix 1 pp.78-79 defines explanation purchaseRegNum, requestDate, decisionDate, creationDate and publishDate separately. This parser uses strict namespace, identity and attachment binding. Incomplete identity-only documents remain quarantined.

Publication evidence is accepted only after revalidating XML/archive hashes and the exact original binary hash and UID. Only a published explanation with consistent dates can fill an absent answer publication date for the same single request in that original. Creation and decision dates are never substituted for publication. Ambiguous versions, foreign UIDs, changed files, draft status, text/XML disagreement, mixed requests and unproven applicability remain review-only. Existing statutory thresholds are unchanged and findings are not generated to meet a quota.

The legal fingerprint includes the verifier. Concurrent PR100 already authorized generation 9, so this distinct interpretation advances to generation 10 rather than reusing 9 with a different signature. Historical completion counts do not prove current-engine completion. Existing general/thematic deduplication and evidence retention stay enabled.

## Tests and rollout

Initial integration run 39474231790 passed 494 test executions. After concurrent production changes were discovered, run 39475788604 verified the exact reviewed integration patch, compiled checksum-verified native SQLite 3.51.3 and passed both branches' regressions before committing. Final PR CI must also pass. Temporary patch transport and writable/snapshot workflows were removed; the final workflow is read-only.

After deployment verify SUCCESS, health/heartbeat, `SQLITE_RUNTIME version=3.51.3 journal_mode=wal`, the actual frozen cohort and new-engine committed receipts. Compare equal windows before claiming fewer retries or faster intake. Neither passing tests nor a short zero-error window means SQLite will never be busy. Original intake, document analysis and newly committed findings are different metrics.

Retain the backup-first fixed WAL runtime. Do not roll back to an unpatched SQLite image, restore over live data, or delete WAL/SHM sidecars. External VPS version 1.4.0 versus 1.4.1 is a separate update; this release does not silently upgrade it.
