# Full-dashboard recheck and write-contention correction

## Scope and preservation

This release implements the explicit 2026-10-20 request to recheck all 4007 dashboard procurements. 4007 is the user's observed count, not a quota: the current dashboard population is frozen at activation, excluding manual-only purchases. More recent entries are not truncated to make the count match. The immutable membership digest, actual count and source/database snapshot timestamps are recorded. Repeated activation of the same request resumes it without rebuilding membership or resetting receipts.

The new authorized request ID is `2026-10-20-dashboard-4007`, selected through `EIS223_FULL_RECHECK_REQUEST`. The old `2026-10-19-all-saved-214328` request and all its membership, attempts and receipts remain stored. No findings, originals or purchase records are erased. Old expired reservations can be recovered only under the existing physical per-procurement lock; live executors are never reset.

The verified-source getter receives a bounded 60-second budget before the database snapshot transaction. That transaction has its own 10-second budget. A slow source enumeration cannot consume the complete SQL budget before it begins. Failed capture never exposes a partial new membership to workers.

## Contention changes

Unchanged customer metadata and derived facts return without acquiring a SQLite write transaction. Planning and serialization happen outside the writer. Snapshot comparisons inside the short write transaction reject stale updates rather than overwriting a concurrent buyer/source change.

A shared event-loop admission gate sequences short recheck acknowledgement, renewal, watermark and bookkeeping writes with thematic database stages. Reads, downloads, document extraction, legal analysis and metadata planning do not wait behind that gate. Cancellation retains a permit until the underlying executor finishes. Existing SQLite transactions, cross-process locks, conditional leases, bounded retries and diagnostics remain authoritative. This is not a promise that all external lock contention disappears.

## Legal processing

Malformed derived summary/fact-evidence containers no longer crash otherwise valid date/price evidence processing. Original source provenance and official XML precedence remain unchanged. Official source rows are not rewritten into their own derived evidence. A concurrent source update invalidates the stale cache update; final audit source-version checks still apply. This is a technical reliability/provenance change, not a new legal interpretation or permission to count uncertain findings as confirmed.

The reviewed semantic generation advances from 10 to 11. Historical generation receipts remain stored. Completion requires a committed receipt for the target engine and the actual verified readable evidence; missing, partial and unreadable originals cannot be called completed or violation-free.

## Validation and deployment

The dedicated read-only CI uses native SQLite 3.51.3 and runs 320 tests covering real held-writer no-op behavior, cancellation, changed-source preservation, request population/snapshot budgets, old-lease recovery, full-recheck contention and legal evidence regressions. Existing PR and Railway checks remain enabled. Synthetic CI findings are not production results.

Deploy code only after all checks pass, then activate the new request via the single service variable. Verify `FULL_RECHECK_REQUEST` and `FULL_RECHECK_PROGRESS` for the new ID, actual membership and target generation 11. Monitor successful heartbeat requests, unique accepted `(reg, uid)` files, actual committed findings and retries in equal time windows. A successful deployment or queued request does not prove all procurements have been checked.

Keep the patched SQLite runtime and existing persistent WAL during any functional rollback. Do not delete WAL/SHM files or restore a historical backup over a live database. No VPS upgrade is performed by this server change; worker 1.4.0 file-size limitations remain a separate rollout.

Concurrent production changes through `1e206d99b2577d7929ca6eb432ac648ebc6823d2` are retained, including parenthesized event-date labels, price-recovery retries and ingestion metadata safety. Generation 9 remains a historical tested transition; this combined change uses generation 10.

The subsequent upstream revision dc95372 is retained: strict explanation parsing, low-write lease renewal and the all-saved epoch stay supported. Activate only one scope for a user instruction, never both as duplicate jobs. Historical generation 10 remains tested; this combination uses generation 11.
