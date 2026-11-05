# HTTP intake stall, 5 November 2026

## Observed incident

Production deployment `16315857-a438-49c1-86bb-65169a7d9ef1`
(`b606e86`) remained marked SUCCESS while requests stopped receiving responses
after approximately 10:25 UTC. Proxy logs show repeated 30-second HTTP 499s
for archive upload and worker report requests, as well as timeouts on `/` and
`/documents`. An independent `/health` request received no application bytes
within 20 seconds. A deployment status alone is insufficient verification.

## Corrected blocking paths

The SOAP archive route called `_v295_enqueue_review` synchronously on the
ASGI event loop. Review admission enumerates receipts, hashes source versions
and waits for per-purchase file locks and SQLite reads. It must use the existing
`_v295_enqueue_review_async` hook, which maintains the same review leases and
offloads physical file work. Synchronous compatibility hooks run in the bounded
file executor when no async hook is installed.

The original manifest, recheck and recheck-status routes also performed receipt
reads or review admission on the event loop. They now use the same bounded file
executor and async admission. No source validation, review deduplication or
completion criteria are changed.

These paths reproduce event-loop starvation under controlled receipt/admission
contention. This establishes a concrete defect; it does not by itself identify
the exact blocked stack of the earlier production process.

## Validation

The three added HTTP regressions fail against the previous implementation.
With the correction, health requests complete while admission or receipt I/O
is held. Archives are already durable before admission waits; replay retains
one archive and one review admission. Manifest, recheck and status routes retain
their existing response status contracts.

166 relevant tests pass, covering intake pressure, automatic rechecks, SOAP
archives, worker reports, worker backpressure, original uploads, account request
responsiveness and page response performance. The existing Docker build also
runs these regression modules before an image can deploy.

The `/data` volume, database, original documents, findings and queue are retained.
Production verification must include real HTTP responses and worker traffic,
in addition to a terminal successful deployment.
