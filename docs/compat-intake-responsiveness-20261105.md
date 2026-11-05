# Saved-original compatibility hooks: HTTP responsiveness

The native archive and receipt endpoints were corrected in `0dc4233`, but the
compatibility discovery and download hooks still traversed saved manifests and
receipts on the ASGI event loop. Slow storage in those hooks can delay unrelated
requests, including the health endpoint.

Discovery manifest reads, download receipt validation and batch reconciliation
now run through the existing bounded `run_file_io` executor. Deduplication file
existence, size and SHA-256 checks run together in that executor; historical
original lookups and cache reads also avoid synchronous file work on the loop.
The existing manifest authorization, receipt verification, offline source policy,
official-document preference and in-place document-list semantics are retained.

Five controlled regressions hold compatibility I/O while making an independent
HTTP health request. They cover discovery, manifest lookup, receipt lookup,
batch reconciliation and deduplication file metadata. All five fail against
the previous implementation and pass after this correction. The Docker build
and dashboard recovery workflow now include these regressions.

Validation: 89 tests passed across compatibility responsiveness, VPS offline
source behavior, automatic intake rechecks, intake SQLite pressure and page
response performance. `git diff --check` passed.

Production recovery is separate from this source change. At 14:07 UTC on
5 November, recovery deployment `30c651bc-ac06-47e0-b6bd-a763357ba38e` was
started from the existing `b606e86` build because applying the staged `0dc4233`
source release was rejected by the approval interface. Neither this follow-up
nor `0dc4233` should be reported as deployed until the live commit and actual
HTTP responses are verified. No database, queue or saved original is modified
by this source correction.
