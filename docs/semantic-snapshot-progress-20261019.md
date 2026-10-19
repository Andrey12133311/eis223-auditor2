# Bounded semantic snapshot progress

Base: `dc4d1b91fcb18335d9678863d5c631ba7368d698` (PR73).
All reproductions use temporary local fixtures. No production files, queue rows,
settings, authentication, endpoints, or database schemas were changed.

## What the observed error means

`SemanticSnapshotError: byte_budget` means a pre-analysis source snapshot did
not finish during that pass. It does not establish lost findings or a permanent
block. Before this patch, a worker's historical `last_error` lacked its original
registration/time and survived later successful work, so the current heartbeat
`reg` could wrongly appear associated with an older error.

- General running work: analysis does not start after the snapshot exception.
  The row returns to pending with its existing bounded retry/backoff; prior
  findings and successful semantic/engine watermarks survive.
- General terminal sweep: the row is left untouched, the fair scan cursor
  advances, and a later sweep retries the snapshot.
- Thematic preparation: incomplete snapshots do not reserve work or replace a
  previous successful watermark. A later scan can resume cached progress.
- Thematic running work: a snapshot error becomes retry, without executing the
  legal engine or acknowledging the incomplete input.

A 327,155,748-byte valid-original fixture at the unchanged 256 MiB budget failed
once, retained 11 complete hashes, and finished on pass two. Real-queue
regressions also prove two incomplete passes preserve the exact old row, the
third admits changed input, and completed work subsequently reaches a fixed
point.

## Separately reproduced and corrected defects

1. Diagnostics classified source snapshots as a database lock. Both queues now
   retain a bounded `source_snapshot_*` code without including paths or source
   metadata. General worker failures record their originating registration,
   time and phase, and clear after a successful save. SQL lock classification,
   retry parameters, reservation checks and `defer_retry`'s signature remain
   unchanged.
2. Stable corrupt SOAP archives were rehashed on every pass without preserving
   progress. Nineteen corrupt 14 MiB archives repeatedly exhausted the default
   budget. Their observed digest and size now use the existing bounded hash
   cache, keyed to the same validated physical identity and expected digest.
   The fixture finishes on pass two with exactly 19 explicit corrupt markers
   and zero XML facts. Raw ZIP bytes are never retained in the shared cache.
3. A valid 3 MiB XML member referenced 100 times by one receipt was decompressed,
   parsed and charged 100 times, exceeding the default budget. Within one
   authenticated ZIP, repeated references now reuse an already parsed physical
   member. Every reference must still match the observed XML hash. Separate
   archives and physical members remain independent, including equal filenames
   with different bytes.
4. An operational receipt rewrite could leave obsolete physical versions pinned
   by the continuation lease, causing false `resume_capacity`. Cache writes now
   verify the current no-follow file identity under the cache lock before
   retiring only older physical versions of that exact path. A late stale
   writer cannot evict a current observation. Different paths and different
   receipt views of the same current archive remain separate.

## Boundaries and security

No byte, time, file, row, expansion, metadata, cache, retry or lease limits were
increased. No partial hash is trusted, semantic format changed, legal source
invented, source file deleted, or legal engine rule modified. Positive
hash-only cache entries still cannot replace archive/member authentication.
Repair at the same path, even with restored mtime, invalidates observations
through the full physical identity (including ctime). Corrupt content, malformed
XML, conflicting repeated-reference hashes and untrusted receipt sources remain
fail-closed. Cache clearing/restart yields the same semantic result.

Current inputs that truly exceed resource caps still report explicit incomplete
errors; they are not declared completed. Continuation is not guaranteed if a
single unit cannot finish within its time/byte guard, current metadata cannot
fit the fixed caches, retries fall beyond the six-hour inactivity lease, or the
process repeatedly restarts. The queues' existing retry behavior is unchanged.

The legal engine signature remains PR73's:
`1150cdd554dfe28ffeae233f3d6d89231ce13c7b974b1cb0225d71a6f77ccbac`.

## Validation

- 50 final semantic snapshot regressions, independently rerun and reviewed.
- 212 integrated tests passed: semantic snapshots, both schedulers, DB-stage
  retry, intake rechecks, legal revision, legacy source versions, general and
  thematic recovery, result counters, automatic legal outcomes and audit funnel.
- Independent per-change review approved all four production corrections.
- Default-budget positive/corrupt/duplicate-member reproductions and reduced-cap
  churn, stale-writer, conflicting-hash and oversized-file negative regressions.
- The existing seven-module Docker gate passed all 153 tests using the prepared
  dependency environment. The system-Python attempt was blocked by its missing
  email-validator dependency; no dependencies were installed or changed.
- Existing Docker and CI test commands already include the changed test files.

## Recurring thematic indexing: bounded writer scope

The recurring index previously applied the publication filter inside a write
transaction and only then limited matching output. A sparse period could scan
all purchases while retaining SQLite's writer. It now reads at most 128 raw keys,
closes the reader, classifies dates/customer forms outside transactions, and
revalidates campaign identity, cursor and the exact bounded source page before
committing membership. Changed ownership/options/source rows abort stale work;
a later sweep retries. Excluded rows advance the cursor; wrap discovers late
inserts and metadata corrections. Existing active work, leases and history stay
unchanged. The legacy membership rule remains eleven characters beginning with
3; this package does not introduce strict digit validation.

A temporary 100,000-row outside-period fixture, with no injected sleep, changed
writer holding time from 181.091 to 0.434 ms (WAL), and 174.513 to 1.685 ms
(DELETE). A concurrent writer changed from SQLITE_BUSY after about 81 ms to a
successful commit in 1.388 / 3.364 ms. Date classification calls changed from
100,000 to 128. A separate controlled 250 ms classification pause also proved
that the second writer can commit during classification. These are local
reproductions; they do not identify the production lock holder or database size.

The 90-second per-campaign indexing cadence is unchanged. Sparse campaigns can
take more sweeps to reach distant matches; eventual traversal and wrap are
covered, not instantaneous full indexing. Production journal mode is unknown,
so regression tests cover both WAL and DELETE. No PRAGMA or worker-count change
is included. The new lock-scope tests are mandatory in Docker and GitHub CI.

## Cache cost and transaction boundary

At the existing 12,000-key cap, 100 cache writes measured a median 82.480 ms
versus 0.144 ms before (about 0.823 ms additional per write); existing-path
rewrites took 94 ms per 100. Cache hits are unchanged. This is one no-follow
stat and a capped in-memory scan, with no file-content or network read. Five
instrumented cache writes occurred outside SQLite transactions; active BEGIN
was rejected before any stat or cache mutation. Legal signature is unchanged.

## Final combined validation

- 241 targeted tests and 255 independent combined-review tests passed.
- All 107 executable local Python build commands passed; container-only setup
  and the bsdtar self-test remain mandatory in the real Railway build.
- 24 JavaScript frontend scenarios passed.
- Full suite: 1,428 tests, 8 failures, 7 errors, 6 skips. The fifteen failure/error
  test identities exactly match the 1,399-test PR73 baseline; no new failing
  identity was introduced. This is not a claim of a green full suite.
- Independent review approved exactly nine changed files, including isolated
  execution of all nine index tests with Docker's preceding COPY dependencies.
