# Frozen-request original recovery bridge

Base: `f1543ae16d2f6742b95f350369e18ecbe54e2764` (PR82-equivalent tree).
Code and fixture contract. No credential, VPS worker, browser, allowlist,
upload-size or legal-rule threshold change. Deployment is verified separately.

## Supported path and exact scope

The existing explicit recheck classified missing originals but had no direct
path to the persistent `v301_search_jobs` download queue. This bridge runs in
its existing selector, uses the unchanged server-worker reg protocol, and
never marks legal completion. There is no new worker or remote polling loop.

The frozen request and its member digest are reused. Company classification
is not applied to bridge admission. Noncompany members therefore receive the
same bounded original acquisition, while statutory applicability remains in
the existing analyzer.

Candidates are `waiting_originals`, or `retry` with a missing/partial-source
reason (`missing_originals`, `no_readable_documents`, `no_verified_documents`,
`unverified_readable_documents`, `partial_or_unverified_documents`,
`source_incomplete`, `unknown_original_coverage`). The local allowed-original
catalogue must then show a missing receipt/path or no original references.

Present but unreadable/unverified originals are deliberately excluded from
remote retries: those require local extraction/evidence repair. Catalogue
scan-limit/error cases are explicitly deferred. General `pending` members
must first pass the runtime's existing missing-original classification;
this change does not itself fix stale nonempty `docs.local_path` pointers.
This is not a future file-loss/corruption monitor: only still-deferred request
members qualify, and the existing source fingerprint is scheduling metadata,
not a cryptographic revalidation of every available file.
Missing purchases, blocked/noncanonical members and active analysis owners
are excluded. No status in this bridge means a completed legal recheck.

## Persistence and queue behavior

One additive table, with no ALTER, rebuild, migration of existing rows or
backfill of the frozen request:

- `full_recheck_originals`: `(request_id TEXT, reg TEXT)` primary key;
  `source_version TEXT`, `job_id TEXT`, `outcome TEXT`, `job_phase TEXT`, `next_check_at REAL`,
  `updated_at REAL`; a member foreign key and one due index
- Existing queue jobs remain intact. The bridge only inserts new standard
  `kind='reg'`, priority-8 FIFO jobs and stores its own binding atomically.
- Active queued/leased/deferred jobs are reused unchanged. Metadata-only jobs
  temporarily block a new download but cannot stand in for full originals.
- New job ID is the first 24 hex characters of SHA256(request + NUL + reg +
  NUL + source identity). Existing deterministic IDs prevent A→B→A duplication;
  a different target/kind/owner at the same ID fails closed as identity conflict.
- Receipt/source identity uses the existing intake source fingerprint.
  Receipt history/bookkeeping, filesystem timestamps, customer edits,
  deployments, engine revisions and clocks cannot manufacture freshness.
- The durable job phase survives catalogue errors and temporary
  present-original samples independently of diagnostic outcome; terminal
  cooldown eligibility is also preserved independently.
- A completed download's own partial source changes are adopted on its first
  observed terminal transition. They cannot immediately trigger another job.
- Known unsupported XML/attachment results and unknown terminal errors do not
  receive automatic new jobs, including after unrelated receipt changes.
- An old explicitly transient server receipt, or a prior done job whose
  originals are now missing, can receive one source-specific request job
  after a 600-second terminal cooldown. Unknown timestamp/report data fails
  closed. The new job uses existing attempts, lease fencing and backoff
  unchanged; terminal failure with the same source never resets that budget.

A rollback to old code ignores the additive table. Already admitted standard
jobs remain executable by the old downloader. Removing the opt-in variable
stops further bridge admission without resetting/cancelling existing jobs.
Legal result history and the immutable request membership are unchanged.
This scheduling-only change leaves the engine signature unchanged and does
not advance request generation. The combined release also contains an independently reviewed collector
correction: exact receipt-backed recovery of stale stored pointers is fenced
inside the durable audit transaction. Because this changes the analysis
signature, the combined runtime targets generation 3 within the same request.
Only completed old-engine members are caught up in bounded pages; active
leases, prior receipts, finding history and frozen membership are retained.
The explicit generation-2-to-3 fixture verifies one replay, unchanged snapshot,
retained old receipt and no duplicate finding.
After that activation, rolling back to generation-2 code intentionally stops
explicit-request claims on the target mismatch. It does not lower the target,
reset members or discard receipts. Restore compatible generation-3 code to
resume the request; do not manually downgrade its ledger target.

## Bounds, leases and writer scope

At most eight eligible local probes per 30 seconds per runtime, two new jobs
per turn, four bridge-created active jobs across processes, and eight existing
manual queue slots reserved. Active and capacity checks plus the insert/binding
commit under one short SQLite writer. Other automatic sources retain their
priority-8 FIFO service; no existing priority or worker count is changed.
Unvisited members sort ahead of already checked gaps. Durable next-check
backoff survives restarts; the process throttle prevents repeated tick scans.

Probes run through the existing shared file gate while holding the per-reg
execution lock. Filesystem reads and bounded persisted-report JSON parsing
happen before the write transaction. The transaction rechecks exact member
attempt/result identity, engine target, all request/general/active thematic
reservations, active download jobs, and the exact classified terminal report.
Expired running analysis leases still block until normal locked recovery.
A stale observation never resets another owner. Cancellation settles the
writer through the existing thread boundary before releasing the file lock.

Fixed aggregate logs report changed outcomes only: enqueued, reused, deferred,
terminal_blocked, capacity, originals_present. They contain no registration,
source text, filename, URL, owner or credentials. Ingestion errors/backpressure
do not prevent local evidence analysis in the existing explicit task slots.

## Local proof

`tests/test_full_recheck_originals.py` covers the request→persistent reg queue
path; same-epoch restart and completion by the unchanged legal engine after
fixture original delivery; noncompany and missing no-readable members;
present unreadable exclusion; unchanged active jobs/metadata-only jobs; actual
server claim/report bounded transient retries; permanent/unknown terminal
suppression; partial source self-trigger prevention; old transient cooldown;
capacity/manual reservation; concurrent admission and atomic rollback;
request/general/thematic/physical lock guards; superseding report fences;
A→B→A deduplication and identity collision; additive-schema compatibility;
3086-member fixture restricted to eight probes and no repeated-tick scans;
and prohibition of filesystem/JSON work in the admission transaction.

The full-chain fixture uses local fixture bytes as the transport delivery;
no real EIS/VPS/network availability is claimed. Download failure and absent
originals remain visibly incomplete, with all original legal receipt gates.
