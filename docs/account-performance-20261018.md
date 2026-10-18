# Account read performance and workflow update — 18 October 2026

## Backend changes

- Shared manual-check status reads now batch purchase metadata, download jobs, latest legal-review state and violation-type labels in groups of at most 350 identifiers. The legal finding counter still uses the existing deduplication method. Responses preserve per-entry job/target matching and input order, even when procurement numbers repeat.
- Queries remain read-only (`PRAGMA query_only=ON`). Authentication, owner-scoped PostgreSQL associations and role checks are unchanged. Download completion still cannot imply legal-review completion.
- Employee active-task and campaign counters use window aggregates over all owned records, before the existing list limits. Manager task counters use a grouped aggregate with the same creator/current-team restrictions as the list endpoint, rather than counting at most 400 displayed tasks.
- Manager document/finding metrics retain the existing 350-check workload bound and now disclose that scope when the underlying check list exceeds it.

## Reproducible fixture evidence

Compared `_read_only_status` from baseline commit `6ca21d470f7932eb33d345a79f6e8838c8e201f6` with the patched function against the same temporary SQLite database. All complete result dictionaries matched for these fixtures:

| Procurement entries | Baseline SQL statements | Patched SQL statements |
| ---: | ---: | ---: |
| 60 | 372 | 13 |
| 150 | 912 | 13 |
| 350 | 2,112 | 13 |
| 360 | 2,174 | 20 |

This counts statements through SQLite's trace callback, including read-only PRAGMAs. It is not a production latency benchmark or a promise about EIS download/audit duration. Query counts step with batch size rather than individual procurement count.

The new regression suite covers batch boundaries, job target/kind isolation, latest audit status, duplicate procurements, missing optional tables, customer column compatibility, label caps, read-only behavior, owner/manager scoping, uncapped task counters, and disclosure of capped evidence metrics.

## Validation limits

- No production database, credentials, account data, messages, or audit decisions were changed during local testing.
- PostgreSQL SQL is exercised through endpoint fixtures; no live PostgreSQL integration instance was available. SQLite batching is executed against real temporary databases.
- Baseline full unit suite: 209 tests, 17 existing failures/errors. The initial backend-only run added eight passing tests and preserved exactly the same 17 failure identities. Final integrated results are recorded separately in the handoff.

## Integrated baseline and workflow checks

The production branch advanced during work. The patch was reconciled on top of
`cd96c90` (V379 SOAP/VPS delivery and legacy RI223 preservation), without overwriting
those changes. At that point the baseline ran 252 Python tests; the integrated
account/style/protocol patch ran 299, with the exact same 17 pre-existing failure
identities and 47 additional passing tests. Card-presentation tests were added
subsequently and are reported in the final release validation.

`node tests/test_account_frontend_requests.cjs` verifies 12 executable request and
race scenarios: local filters, in-flight mutation refreshes, no repeated forbidden
history polls, lazy staff tab reads, duplicate campaign avoidance, concurrent case
reads, case-switch guards, saved draft buffers, serialized autosaves, asynchronous
form safety, and admin refresh/search request behavior. Initial staff page GETs in
the instrumented fixture decrease from 12 to 6. Manual/thematic filter edits no
longer request the entire dataset from the server.

The shared visual layer adds no external scripts, fonts or requests. Five CSS,
contrast and integration contracts pass. These checks are not screenshots:
computed browser layout could not be verified because local Chromium could not
start in the available execution sandbox and the separate cloud browser could
not reach local fixture files. No access restrictions were bypassed.

Final integrated Python run (including card): 317 tests, the same 17 baseline
failures/errors, and one optional Chromium interaction test skipped. The 65 added
tests comprise 64 passing tests plus that explicit browser skip. Source compilation
and diff whitespace validation pass. The new card has 17 passing offline
regressions; native browser click/keyboard/layout behavior remains unverified.
