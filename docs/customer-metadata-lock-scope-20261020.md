# Read-only buyer metadata no-ops

Base: `0b1828a`. This is a separate local candidate, not a runtime diagnosis or
deployment.

## Proven local issue

`v311_customer_backfill.reconcile_buyers` revisits rows with incomplete buyer,
INN or title fields. Missing source evidence can leave a row incomplete across
many sweeps. Previously, `v299_browser_intake._apply_customer` acquired
`BEGIN IMMEDIATE` before deciding whether any field could change. Thus a no-op
still competed for SQLite's single writer.

A synthetic WAL database reproduces 200 writer reservations for 200 identical
no-op calls, although all return `False` and none execute data-changing SQL.
With an unrelated writer held, the same no-op raises `database is locked`.
This does not identify the writer holding the production database.

## Narrow change

- Plan existing field-repair rules using a read-only connection.
- Return `False` immediately when the row is missing or no field needs repair.
- Close that reader before opening the actual write connection.
- For a candidate repair, retain `BEGIN IMMEDIATE`, re-read the current row and
  recompute the changes under the writer lock. Concurrent valid corrections
  are preserved, and deleted rows are not recreated.
- Retain rollback, unconditional close and errors for actual blocked writes.

The same local fixture now makes zero writer reservations for the 200 no-ops.
Its actual INN repair and final purchase, finding and unrelated intake rows
match the baseline exactly.

Only `v299_browser_intake.py` production behavior changes. There are no changes
to buyer provenance, timestamps, source files, audit findings, queues, legal
results, requested generations, `v332` or full-recheck modules. Timeouts and
concurrency are unchanged.

## Regression checks

`tests/test_customer_metadata_lock_scope.py` covers no-op writer reservations,
an unrelated active writer, the actual periodic backfill path, missing rows,
preflight lock scope, concurrent full/partial corrections and deletion, buyer
identity preservation, real repair idempotency, rollback and connection closure
after failure, and continued BUSY reporting for actual repairs.

The regression is included in the Docker and throughput CI gates.

## Limits

This reduces one demonstrated source of avoidable writer competition. It does
not establish that these no-ops account for the observed runtime stalls, nor
does it release a writer held by another connection.

V335 cached price recovery also acquires a writer for some no-op rows. Its
factual-check synchronization has different semantics and is left unchanged.
V314 metadata refresh can update its observation timestamp even when purchase
fields do not change; it is deliberately not treated as a history-free no-op.
Source-file reading and document extraction in the inspected backfill paths
occur outside their SQLite writer transactions.
