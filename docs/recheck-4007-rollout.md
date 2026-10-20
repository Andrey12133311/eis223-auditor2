# Full-dashboard recheck and write-contention correction

## Scope and preservation

This release implements the explicit 2026-10-20 instruction to recheck all 4007 dashboard procurements. 4007 is the user's observed count, not a quota: activation freezes the actual dashboard population, excluding manual-only purchases. More recent entries are not truncated. Membership digest, actual count and source/database snapshot timestamps are recorded. Repeated activation resumes the same request without rebuilding its membership or resetting receipts.

The dashboard request ID is `2026-10-20-dashboard-4007`, selected through `EIS223_FULL_RECHECK_REQUEST`. The historical `2026-10-19-all-saved-214328` request remains intact. The separately authorized upstream `2026-10-20-all-saved-explicit-2100` scope also remains supported and includes all saved purchases, including manual entries. Activate only one scope for an instruction; do not run these alternatives as duplicate jobs. No findings, originals or purchases are erased. Recovery touches exact expired claims only under the existing physical per-procurement lock, never a live executor.

Dashboard source enumeration has a bounded 60-second budget before the SQL snapshot; that transaction has its own 10-second budget. The all-saved scope retains its existing direct SQL snapshot and does not invoke the dashboard getter. Failed capture never exposes incomplete membership. Aggregate diagnostics report an unknown expected dashboard count as null, not a fabricated zero.

## Contention changes

Concurrent upstream metadata and legal-fact fixes are retained: unchanged values take a read-only path, planning happens outside the writer, and guarded writes recheck their source snapshots. The additional source guard refuses to overwrite newer intake columns with stale audit-derived facts, while allowing a safe rebase of derived-cache-only changes.

A shared event-loop admission gate sequences short recheck acknowledgement, renewal, watermark and bookkeeping stages with thematic database stages. General legal analysis, metadata planning, downloads, extraction and read stages are not serialized through it. Cancellation retains its permit until the physical executor finishes. SQLite transactions, file locks, exact leases, bounded retries and diagnostic messages remain authoritative. External lock contention can still occur.

The upstream lease fix is preserved: 30-second ownership checks remain, but a healthy 300-second lease is rewritten only when at most half its lifetime remains. Ownership and expiry are checked again under the writer; an expired or foreign claim is never revived.

## Legal processing

Retain explicit parenthesized event-date labels, price-recovery retries and strict documented explanation XML parsing from upstream revisions 1e206d9 and dc95372. Request, creation, decision and publication dates retain different meanings. Attachment references come from the bound official XML; missing, ambiguous or foreign procurement identities are not accepted. Unknown formats do not become confirmed evidence.

Malformed derived summary/evidence containers no longer crash valid date/price extraction. Official XML precedence, original provenance and final audit source-version checks stay intact. Technical reliability and extraction improvements do not authorize invented facts, new legal thresholds or treating review-only candidates as confirmed violations.

The combined interpretation generation is 11. Historical generation-9 and generation-10 transitions and receipts remain tested and preserved. Completion requires an actual committed receipt for the target engine and verified readable evidence; incomplete, missing or unreadable originals cannot be called completed or violation-free.

## Validation and rollout

Dedicated read-only CI compiles and uses SQLite 3.51.3 and tests held-writer no-op behavior, cancellation, concurrent source updates, both frozen scopes, source/SQL budgets, expired-lease recovery, explicit date labels, XML identity and existing legal/persistence regressions. All temporary writable integration workflows and patch transports are removed before merge. Synthetic test findings are not production results.

Deploy only after final-head checks pass. Activate the single requested scope only with the compatible deployed image, then verify `FULL_RECHECK_REQUEST`, actual membership, target generation 11 and committed progress. Do not set an unsupported epoch on the old image, even with deferred redeployment. A queued request or successful heartbeat does not prove completed rechecks or new violations.

Keep the fixed native SQLite runtime and persistent WAL on a functional rollback. Do not delete WAL/SHM files or restore a historical backup over a live database. This server release does not upgrade the external VPS; worker 1.4.0 file-size limitations remain a separate rollout.
