# Repeated legal processing and SQLite writes

The production logs on 20 October showed exhausted `legal_persist_facts`,
metadata and request-acknowledgement BUSY retries after the verified WAL
conversion. WAL allows readers alongside a writer; it still has one writer.

Unchanged document metadata and derived legal facts now finish with a read-only
preflight. Plans are computed after closing that snapshot. Actual changes still
acquire the existing guarded transaction, validate the exact reservation, reread
the purchase and rebase on a changed snapshot. This does not acknowledge a lost
lease or erase source evidence, findings, queues or receipts. Missing optional
buyer tables are recognized during metadata preflight so the original guarded
writer can reject a stale attempt before it can make any change.

Price sweeps avoid acquiring a writer for already satisfied amounts/checks.
Original-based recovery retrieves evidence once and retries only its database
stage. Real repairs use the existing guarded transaction. An invalid amount
cannot convert a missing-NMCD fact check to `ok`.

The date grammar additionally recognizes the explicit synonymous labels
`поступления (получения)`, `регистрации (поступления)` and
`размещения (публикации)` in receipt and placement fields. Arbitrary parenthetic
text, planned events and a non-EIS organizer website remain insufficient.
The existing same-document and cross-original analyzers, scope checks, calendar
rules and event deduplication remain in use. These are parser corrections,
not changes to statutory thresholds or production violation counts.

Explicit request generation advances from 8 to 9 once. The original request ID,
captured membership, historical attempts, receipts and active claim identities
are retained. Prior completion under a different engine is not presented as
completion under this engine. New procurements retain the ordinary automatic
admission path; manual inputs retain their separate owner-scoped workflow.

Regression coverage uses real temporary WAL databases, held writers, a concurrent
intake, an expired lease, source-stage retry, malformed derived JSON, invalid
price evidence, explicit event labels and identical source copies. Docker and
the throughput CI run the new tests. Synthetic findings prove parser behavior,
not a production increase. Confirm deployment SUCCESS and new-generation
committed receipts before reporting the production recheck as running; it is
complete only when the request coverage says so.
