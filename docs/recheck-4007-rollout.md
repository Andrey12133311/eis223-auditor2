# Full-dashboard recheck and verified clarification evidence

## User authorization and frozen scope

On 2026-10-20 the user requested correction of contention/errors, better legal processing and rechecking all 4007 dashboard procurements. The latest successful inventory subsequently observed 4082 dashboard entries at 2026-10-20T18:41:44Z. Never truncate the actual population to a historical UI count or fabricate completion.

Use the single canonical request ID `2026-10-20-dashboard-4007` from PR102 with expected_count=4007. The actual full dashboard membership and digest are frozen once, excluding manual-only entries under the existing dashboard contract. There is no second `all-dashboard-4007` epoch. Repeating the user's instruction or restarting does not reset/refreeze the ledger.

Retain the separately authorized old `2026-10-19-all-saved-214328` and explicit all-saved `2026-10-20-all-saved-explicit-2100` scopes and historical receipts. Do not replace the dashboard scope with all-saved or silently exclude records lacking documents. Missing/unreadable originals and unproven applicability are not successful completion.

After this compatible release is SUCCESS, activate `EIS223_FULL_RECHECK_REQUEST=2026-10-20-dashboard-4007`, then verify the runtime request ID, actual_count/digest and target_generation=12. An older image does not understand this request; do not switch its epoch while deployment is blocked. Current-engine completion requires committed receipts, not historical flags.

## Integrated contention corrections

Preserve PR100/101/102 and their regression suites: no-op metadata/price/fact writes, empty renewal preflight, claim-only schema readiness, shared short-write admission for general/thematic/renewal operations, lease-token fencing, cancellation-safe executor settlement and rejection of stale source updates. Admission does not replace physical file exclusion or SQLite transactions, and analysis/extraction/network I/O stays outside it.

The fact planner freezes caller-owned input, prepares outside the writer and performs bounded optimistic rebases for derived-cache changes only. A concurrent source-column update defers the stale plan. Unchanged facts do not reserve the writer. No diagnostics, validation, durability or source throttles are disabled.

The dashboard snapshot uses bounded source capture outside the short atomic DB snapshot. First activation prioritizes this immutable capture rather than enumerating the opaque source twice. Incomplete/budget-exceeded captures commit no partial cohort. Worker counts and source request rates are unchanged.

## Legal evidence chain

Preserve the official 223-FZ TFF16.3 explanation format's distinct requestDate, decisionDate, creationDate and publishDate, strict namespace/GUID/identity validation and the explanation record family. Drafts and incomplete documents remain quarantined.

The publication verifier rechecks received XML/archive hashes and the exact original binary hash/UID. A published explanation may fill a missing response publication date only for the same verified single request in that original with consistent dates. Creation/decision dates are not publication. Ambiguous versions, foreign attachments, tampering, mixed requests and text/XML disagreement remain review-only. Existing statutory thresholds and applicability tests are not relaxed; findings are not generated to meet a numeric quota.

Generation 12 uniquely identifies this verifier after concurrent production generations 9, 10 and 11. The verifier is included in the legal engine fingerprint. Preserve general/thematic duplicate collapse and historical evidence.

## Verification and release blocker

Integration run 39480851906 compiled checksum-verified native SQLite3.51.3, checked the exact SHA256 of every integrated implementation/test file and passed 560 test executions in six groups before committing. Final PR checks must pass. All temporary source snapshots, writable integration jobs and transport scripts are removed from the final source; CI is read-only.

At the last attempted release Railway explicitly returned `Deploys have been paused temporarily`. An accepted environment-patch command did not create a new deployment; do not report it as a successful release or activated recheck. The previous request remains configured until compatible deployment is verified.

After publication is permitted, require SUCCESS, health/heartbeat, SQLite3.51.3/WAL and new-ledger progress. Compare equal windows before claiming fewer retries or faster intake. Accepted files, analyzed documents and newly committed findings are different metrics; no passing test proves zero future SQLite_BUSY.

Retain the fixed backup-first WAL runtime. Never delete WAL/SHM files, restore over live data or roll back to unpatched SQLite. The external VPS1.4.0-to1.4.1 update is separate and has not been silently performed by this release.
