# Verified-source context and recheck recovery

This correction reuses the existing legal validators. It does not relax legal thresholds or treat review-only signals as confirmed violations.

## Verified saved context

Both legal paths must load receipt/hash-validated current SOAP notice context even when the original documents already have stored text rows. Reusing those rows must not discard current method, publication, bid-period or price facts. The metadata-only context path avoids re-extracting the same original bytes.

The same-byte positive regression previously produced no applicable late-clarification finding on the all-persisted path, while the original-collection path produced one. The corrected general and thematic paths agree. Noncompetitive, pre-rule, unknown-method, generic-word-only, foreign-registry and tampered-receipt cases remain negative. A repeated accepted episode does not insert another finding.

Current source fields replace stale purchase-row values only through the validated source context. A nonempty old database value is not evidence that it is the current notice version. Zero is preserved as a supplied numeric value.

An old card is superseded only when explicit timezone-aware instants prove it predates the verified notice. Conflicting facts with unknown or ambiguous ordering remain unresolved. Independently captured card prices retain their own timestamp. The semantic source token records the material ordering relation rather than a raw capture timestamp, and analysis-derived SOAP values do not rewrite intake-owned scalar source fields. This prevents an unchanged source from invalidating its own completion receipt.

## Archive-parent read accounting

An intake-restored `downloaded_no_text` archive pointer can remain in stored rows after its authenticated members have been successfully read. Its obsolete unreadable contribution is reconciled only against fresh, authenticated, warning-free expansion and matching readable members. This does not delete or rewrite stored evidence.

Failed children, invalid or changed receipt hashes, unresolved member mapping, path/size limits, forged aliases and failed collection continue to require review/retry. Different UID copies do not multiply a legal episode.

## Explicit request diagnostics

Fixed primary reasons distinguish unreadable or unverified documents, missing/unknown original coverage, unavailable or changed source, unavailable or changed engine, and incomplete source collection. The completion predicate and number of source reads are unchanged by the diagnostic split. Overlapping conditions use a deterministic priority; a reason does not imply that no violations exist in the readable subset.

Legal results may be durably saved from verified readable evidence while the request remains incomplete. Newly inserted confirmed records, review/check rows, unique members attempted and total attempts are separate metrics. Ordinary, explicit-request and thematic counters must not be added as globally unique legal episodes.

## Engine-specific catch-up

The explicit request retains its original registration membership and epoch. A corrected legal engine must not inherit current-coverage credit from a different engine merely because a member was historically completed. Bounded catch-up preserves pending/running claims, prior receipts and cumulative inserted-result credit.

Two additive operational tables hold the monotonic correction target and immutable per-attempt receipt history. There is no existing-table rebuild or trigger. Completed members with an older engine are reopened in bounded pages, with the old receipt archived in the same transaction. Reopening alone neither increments attempt counts nor credits another finding. A late old-process completion is historical; current coverage requires a durable receipt for the installed target. Catch-up runs even when a legacy process has marked the legacy request completed.

`current_completed`, `current_pending`, `stale_completed`, `target_generation`, `target_engine_signature` and `coverage_basis` make this distinction explicit. Existing raw state counts and `legacy_status` remain available. Cumulative newly inserted findings are not a count of independently reviewed legal episodes.

The rotating candidate cursor advances only past actually visited registrations. Previously it jumped to the end of an eight-row page even when the worker returned after the first row. A controlled-clock regression demonstrates how repeatedly due leading retries could starve fresh entries later in each page. The correction retains backoff, physical locks, lease fencing, batch limits and worker counts; a busy first row does not prevent later rows from being visited.

### Rollout and rollback

1. Deploy the reviewed combined context/archive engine with correction generation 1. Retain the already configured explicit request epoch; do not create a new one or recapture membership.
2. Verify the persisted count, digest and capture timestamps remain identical. Check the target signature, bounded old-completion catch-up, current coverage and both queue commit logs.
3. A restart with the same generation and signature resumes. An older generation or a different signature at the same generation fails closed. A later semantic correction requires an explicitly reviewed generation advance, not a deployment-SHA-derived version.
4. If rollback is needed, stop admitting this explicit campaign through its existing nonsecret setting before returning to pre-target code. Keep all operational ledger and result rows. The previous code ignores additive tables but cannot enforce their current-engine coverage, so its legacy completed count must not be presented as target coverage. Reinstalling the reviewed target resumes the same frozen membership without resetting active work.

## Historical verdict limitation

Existing general finding storage is insert-only. A later source correction can produce no current finding while an older stored finding remains visible and counted. This release does not delete that history or rewrite manager decisions. Generic historical reassessment is a separate change; historical dashboard counts must not be presented as new or current-engine confirmed findings.

No credentials, authentication boundary, worker count, resource/security limit, extraction format whitelist or separate evidence-storage migration is changed by this correction.
