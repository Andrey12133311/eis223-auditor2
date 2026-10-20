# SQLite contention and repeated archive failures

This change does not erase purchases, original documents, findings, queues or receipts. It does not suppress SQLite/validation diagnostics or turn rejected uploads into successful receipts.

## Runtime prerequisite

The image builds SQLite 3.51.3 from the official amalgamation. The SHA3-256 of sqlite3.c is checked against https://www.sqlite.org/releaselog/3_51_3.html before compilation. A fresh Python process must load and report that exact native version. Older SQLite through 3.51.2 contains the WAL-reset race described in https://www.sqlite.org/wal.html; merely enabling concurrent WAL on the previous image is not the chosen fix.

## Startup conversion

Before application workers are installed, the existing main database connection is inspected. Conversion requires the patched runtime, an idle connection, an existing regular file, a recognized local filesystem and sufficient free space. Network and unknown filesystem types are refused. A nonblocking process lock excludes concurrent migrations.

The SQLite backup API copies the existing database to a unique file under `eis223-sqlite-backups` beside the database. The backup must pass `quick_check` and be synced before `journal_mode=WAL` is attempted. The work budget is 45 seconds; lock acquisition for the mode change is bounded. Failure preserves the existing mode/data and emits `SQLITE_WAL_STARTUP` with its reason. A completed backup is never overwritten. Repeat startups with WAL already enabled do not duplicate backups. No original or WAL/SHM sidecar is removed; synchronous durability and automatic checkpoints are not weakened.

`EIS223_SQLITE_WAL=0` opts out of automatic conversion; it does NOT switch an already converted database back to DELETE. WAL is persistent. Do not roll back to an older image containing an unpatched SQLite after conversion. A functional rollback must retain the fixed runtime; any planned mode reversal requires stopped writers and a fresh verified backup. Never restore an old snapshot over a live database or delete WAL/SHM files to clear locks.

Success requires both `SQLITE_WAL_STARTUP enabled=true` and the subsequent `SQLITE_RUNTIME version=3.51.3 journal_mode=wal`, followed by successful real intake and audit commits. WAL does not allow simultaneous writers and cannot promise that SQLITE_BUSY will never occur.

## XML and retry behavior

The user-supplied 223-FZ TFF 16.3 Appendix 1, pp.78-79, specifies direct `explanationData/purchaseRegNum`. The quarantine identity reader accepts this field only for the exact documented `explanation` root, verifies it against the expected procurement and filename, and rejects duplicate, foreign-namespace, missing or conflicting identities. This is not a generic XML fallback. Explanation XML stays marked unsupported; no legal facts, links or completion credit are invented. Unknown unsupported XML types can still be rejected and remain visible.

Repeated `site_http_422`, `response_size_limit` and unsupported-XML reports without verified original progress use bounded 15/30/60-minute retry spacing under the existing attempt budget. Larger requested Retry-After values remain respected. Other jobs can run meanwhile. Reports are replay-safe; accepted originals are preserved; real new receipts retain the normal retry behavior. The metadata engine generation advances from 7 to 8 without resetting historical membership or receipts.

## Verification

The dedicated read-only CI compiles and uses the real fixed native library and tests backup integrity, migration refusal, concurrent reader/writer isolation, strict XML binding, retry budgets, replay, preserved original bytes, recheck generation transitions and existing worker/persistence regressions. Existing CI and Railway build checks remain enabled.

After deployment compare equal log windows for retries, intake rejections, unique accepted `(reg, uid)` pairs, queue age/backlog and successful heartbeat requests. Neither a successful build nor a heartbeat proves new verified procurement records or confirmed violations. VPS version 1.4.0 versus 1.4.1 is a separate deployment; this server change does not silently upgrade that host.
