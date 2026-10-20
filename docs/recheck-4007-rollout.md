# Full-dashboard recheck requested on 2026-10-20

## Scope and activation

The user requested all 4007 procurements rechecked after correcting contention and improving legal analysis. Inventory already counted 4014 dashboard procurements at 2026-10-20T17:41:27Z. Do not truncate a changing population to a displayed historical count.

The fixed request ID `2026-10-20-all-dashboard-4007` has expected_count=4007. It freezes actual complete dashboard membership once, records actual_count and its digest, and excludes manual-only entries under the existing dashboard contract. All eligible saved dashboard entries are included even if actual_count differs. Missing originals are not completion.

Activate `EIS223_FULL_RECHECK_REQUEST=2026-10-20-all-dashboard-4007` only with this release. Verify its ID, actual_count, digest and target_generation=11. Reusing the ID does not refreeze or duplicate membership.

Concurrent PR101's separate all-saved epoch `2026-10-20-all-saved-explicit-2100` remains supported, including its explicit manual/noncanonical scope and blocked reason; it is not silently substituted for the dashboard scope. The original `2026-10-19-all-saved-214328` ledger and all historical attempts/receipts are retained. Physical exclusion and exact lease-token checks protect live claims across authorized epochs; only expired leases are recovered.

## Contention changes

Unchanged metadata, legal facts and prices avoid reserving SQLite's writer. Changed facts are prepared outside the transaction and revalidated with bounded optimistic rebases under the existing ownership guard. PR100 and PR101 fixes and tests are preserved, including empty lease-renewal preflight, claim-only schema readiness and guarded stage-only retries. No validation, durability, logging or source throttling is disabled.

The local source capture has a separate 60-second budget outside the writer, followed by the original short atomic DB snapshot. Incomplete captures commit no partial membership. Actual timestamps and counts are recorded. Worker counts and source requests per second are unchanged.

## Legal evidence

The official user-provided 223-FZ TFF16.3 explanation format has distinct requestDate, decisionDate, creationDate and publishDate. Preserve PR101's strict published format, GUID/date/namespace validation and the `explanation` record family. Incomplete or draft documents remain quarantined.

The new publication verifier rechecks received XML/archive hashes and the exact original-file hash/UID. Only a published explanation with consistent dates may fill an absent answer publication date for the same single request in that original. Creation and decision dates are not publication. Foreign UIDs, drafts, ambiguity, mixed requests, changed files and text/XML disagreement remain review-only. Existing statutory thresholds and applicability checks are unchanged; no quota-driven findings are created.

Generation 11 uniquely identifies this verifier after concurrent production generations 9 and 10. Historical receipts do not count as current-engine completion without a committed recheck.

## Verification and safety

The final combined integration run 39478349673 compiled checksum-verified native SQLite3.51.3 and passed 540 test executions in six groups before committing the implementation. Earlier isolated runs also passed. Final PR checks must pass before merge. Temporary snapshot/writable integration workflows and patch transport were removed; final CI is read-only.

Verify deployment SUCCESS, health/heartbeat, SQLite3.51.3/WAL and the actual new request ledger before reporting activation. Compare equal log windows before claiming throughput improvement. Neither test success nor a short zero-error window proves SQLite can never be busy. Accepted files, analyzed documents and newly committed findings are separate metrics.

Preserve the fixed backup-first WAL runtime. Never delete WAL/SHM files, restore over a live DB or roll back to an unpatched SQLite image. External VPS1.4.0 versus1.4.1 is separate; this release does not silently update that host.
