# V381 throughput: evidence and operational limits

## Production baseline (before V381)

Railway runtime log sample, 2026-10-18 21:17:17–21:24:23 UTC:
107 distinct general audits completed over 425.574 seconds, with 1,232 readable
document records and 1,201 verified EIS document records. This is an observational
sample, not a controlled benchmark; document sizes and cache warmth vary.
The four-worker campaign was already running. Procurement intake, audit
completion and download completion are different metrics.

## Repeated DOCX extraction

The ordered DOCX evidence parser bypassed the existing plain extraction cache.
It now has a separate, revisioned content-addressed cache, preserving its paragraph
and table order. Hashes are recalculated from the source bytes each time; provenance
and receipt checks still run upstream. Formats are part of the key, failed/empty
extraction is not cached, and locks occupy fixed bounded memory.

Local fixture: 100 paragraphs and 100 tables, eight identical reads. One measured
run: 0.0920 s uncached versus 0.0248 s cached including the first miss (3.71×).
This is an isolated extraction fixture, NOT a measured production throughput gain.
Text outputs are compared for exact equality. Source replacement, namespace/format
isolation, concurrent singleflight, and failed extraction retry are regression tested.

## Source constraints

External EIS latency, authentication availability, source rate limiting, CAPTCHA,
and unavailable originals cannot be made instantaneous. Missing originals never
become a successful legal check. No EIS limits or security restrictions are removed.

## VPS original-download pipeline (worker 1.1.0)

The worker now overlaps one look-ahead EIS original download with upload of the
previous original to the site. EIS downloads remain serial, separated by at least
2.5 seconds, with at most 12 original attempts and two jobs per five-minute cycle.
Only the current and one prefetched original are resident; SQLite/cache writes
remain on the main thread. A queued look-ahead is cancelled on upload failure;
already-downloaded bytes are retained for retry. Concurrent EIS 403/429 and site
failures preserve the global rate-limit code and longest retry delay. TLS, official
URL validation, byte limits, archive checks and authoritative server receipts are
unchanged.

Card-metadata-only jobs now stop after verified XML delivery instead of consuming
the original-file budget. The worker reports partial delivery; only server-side
archive validation and an actual card write can finish that metadata-only lease.
Normal full-document jobs still download every required original.

### Synthetic local comparison, 2026-10-18

Baseline: exact `scripts/eis_vps_worker.py` from commit `19c77c2`, version 1.0.0.
Candidate: version 1.1.0. Five runs per version, eight synthetic originals per run,
fresh temporary durable state per run, identical fake SOAP/manifest/upload paths.
Network latency was simulated with 30 ms per original download and 30 ms per
upload, enforcing at least 25 ms between download starts (a 1:100 time-scale
model of three-second transfers and the real 2.5-second limiter). Real EIS,
user files and credentials were not used. Both variants delivered the same eight
originals and passed request-spacing assertions.

- Sequential median: 0.486680 s
- One-file pipeline median: 0.275714 s
- Reduction in active transfer time: 43.35%; speedup: 1.765×

This is a controlled local latency model, not production documents/minute. The
cycle caps are unchanged, so it does not establish a higher sustained intake
ceiling. It reduces time spent in the active transfer stage when upload latency
is material, and metadata-only jobs no longer consume original-download slots.

Validation: 26 offline worker tests (including merged 1.0.1 token hardening),
14 server queue/security/evidence tests, and
one in-process VPS → real intake endpoints → durable original receipts test pass.
The integration fixture verifies partial download, retry, duplicate handling,
forged-completion rejection and zero fabricated audit counters. Worker syntax,
installer shell syntax and `git diff --check` also pass. After merging the VPS-only
production update, the full focused `test_eis*.py` suite passed 79 tests in 11.278 s.

### Separate VPS rollout is required

The Railway image copies this worker source but does not run or update the external
systemd daemon. `/eis223/worker-version` advertises protocol version 1, not the
installed daemon's code version or a script download. The 1.1.0 changes retain
protocol version 1. The installer loads the script from `--source-dir` or a pinned
Git commit plus SHA256 and explicitly replaces `/opt/eis223-worker/eis_vps_worker.py`
on the VPS. A site-only deployment therefore does not activate these download-stage
improvements. No VPS deployment or live 1.1.0 throughput was performed or verified here.

Read-only Railway HTTP logs on 2026-10-18 confirmed the existing daemon's User-Agent
as `eis223-vps-worker/1.0.1`, including a successful heartbeat at 21:32:32 UTC and
job reports. In the 21:17–21:33 UTC window, six archive POSTs from that worker
included four HTTP 200 and two HTTP 422 responses (21:22:27 and 21:32:31). Rejection
bodies and failed registry numbers were unavailable in those logs, so the cause
is not established. Report HTTP 200 only acknowledges the report, not successful
document ingestion. Two heartbeat HTTP 502s around the production replacement
were followed by successful heartbeats; this was not a controlled performance
comparison. Original-import application logs in that window mix browser and VPS
traffic and cannot be used as a VPS-only documents-per-minute baseline.

## Memo and organization display QA

Organization formatting changes only the initial lowercase letter inside quoted
names, preserving subsequent case and abbreviations. Existing stored evidence is
not bulk rewritten. Main list, cards, manual/thematic histories and memo exports
share the formatter. Memo recipient, executor and optional phone are stored per
signed-in user and procurement; unchanged defaults come from the original template.

Real LibreOffice/PDF QA on canonical synthetic fixtures covers five short/long and
pagination-boundary cases. Representative outputs occupy 1, 2 and 3 pages. Executor
and phone occur once, on the final page, at 7pt, black (PDF color0), aligned to the
left page-content edge; no footer-only blank page. The original uploaded production
template was unavailable locally, so production export verification is still needed.

## Regression baseline

Against production b170c2a, the full Python suite has the same 10 failures and
7 errors already present in that baseline (346 baseline tests, 387 current tests
at integration, additional optional rendering skipped in ordinary discovery).
Focused release suites are green; this is not a claim that the entire legacy test
suite is green. Docker runs the new focused gates in addition to existing checks.
