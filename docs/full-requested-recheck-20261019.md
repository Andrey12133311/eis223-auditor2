# One explicitly requested full dashboard recheck

Base: PR75, `7516619842c0036ab1c14b633a4e191f708a666d`.
Its tree is identical to local test baseline `5e3639de58a2e368e5d50546c903a23af585cb01`.
This package is local preparation; no production database, Railway variable,
public endpoint or deployment was changed while preparing it.

## What is and is not established about 3,058

The operator requested all 3,058 purchases. That number is an expectation, not
a verified current inventory. The general runtime counter previously reported
2,003 positions and thematic startup reported 6,406 active positions. These are
different populations. Neither subtraction nor their sum establishes excluded
or unique procurement counts. Current live SQLite access and the authenticated
dashboard were unavailable during this investigation. No missing IDs are
invented, and no completion of the live cohort is claimed.

### Existing source/gate map

- `v257_features.dashboard`: unfiltered dashboard membership is the volatile
  `_verified_set235()` set union the persistent current general campaign,
  intersected with saved `purchases`, minus `v319_manual_inputs`. There is no
  `business_company` filter in this default dashboard. Explicit INN and UI
  filters change that population and are not part of this full-request scope.
- `_verified_set235` is supplied by the restored private `entry.py`. Its
  implementation is not in this checkout. Existing comments and dashboard code
  explicitly preserve queue history when its returned intake set shrinks.
  Its current runtime definition/return value was not independently inspected.
- `v332_all_procurements_audit.dashboard_regs`: initial queue seed is limited
  to `DASHBOARD_MAX=1253`, additionally requiring the company parser. The
  historical campaign name and startup `MAIN_1253_SCOPE` message are not a
  permanent cap. `add_new_verified` expands this same campaign in bounded pages.
- General new admission excludes explicit manual numbers, requires membership
  in the current verified getter and a company name recognized by
  `v342_thematic_queue.customer_form`. Existing queue claims and persistence
  recheck company eligibility. The recognized forms are ООО, АО, ПАО, ОАО, ЗАО
  and their recognized full leading names. Institutions, individual
  entrepreneurs, blank names and unrecognized leading text do not pass. This
  is a name parser, not proof of a legally excluded purchaser or statutory rule.
- General `progress.total_registered` counts every row for the campaign,
  including possible orphan rows or rows with now-ineligible customer names.
  `baseline_purchases` is a historical all-database count at campaign creation.
  Neither is a current exact executable or dashboard population.
- `progress.semantic_recheck_cohort` counts terminal queue rows with selected
  saved document metadata, not physically verified/readable originals.
  `ready_work` and ordinary claims additionally respect current state,
  retries and document/receipt readiness. The original 1,253 seed does not
  bypass those runtime guards.
- Thematic `_index_batch` considers saved canonical-looking registration
  numbers, each active campaign's legal forms and inclusive publication
  period. `_campaign_guard` repeats scope checks before claiming and writing.
  Missing publication dates fail a dated campaign; they can enter an all-time
  one. Campaign categories limit which legal results are saved.
- `THEMATIC_RECHECK_COHORT active_items` is `COUNT(*)` of active `(batch,reg)`
  positions, not `COUNT(DISTINCT reg)`. One procurement can be present in
  multiple independent campaigns. History views count each campaign separately.
- `v360_thematic_ownership` keeps authenticated user-to-campaign mapping in
  PostgreSQL. Existing owner checks, chosen forms/categories/periods and
  stopped/deleted campaign behavior are unchanged here. No request imports or
  claims private campaign ownership or writes into another campaign's history.
- `v295_eis_originals._review_owned_elsewhere` is automatic execution routing,
  not an all-purchases inventory: it recognizes currently eligible general
  members or matching active thematic members.

### Available existing read-only runtime summaries

- Authenticated `GET /api/v257/dashboard` returns the filtered purchase count
  and a maximum 200-row page. Its count is the relevant default dashboard basis.
- `GET /api/v332/legal-audit/progress` and its private `V332 HEARTBEAT` show the
  general campaign counters and runtime state described above.
- Owner-filtered thematic history/status returns individual campaign counts.
  It cannot safely be treated as a cross-owner unique procurement inventory.
- `GET /api/v298/eis-only/status.official_imported_procurements` counts archive
  inbox directories, not database/dashboard membership or completed reviews.
- EIS worker/status summaries count discovery or download jobs, not distinct
  dashboard procurements. Historical startup baseline counts are not live counts.

No existing inspected operational log reconciled all these sets exactly.

## Read-only inventory

`full_recheck_inventory.collect` samples the getter once before opening a
read-only SQLite snapshot. It reports only fixed aggregate counts: saved
purchases, explicit manual saved records, current verified/saved overlap,
default dashboard count/company split, persistent general count/current
eligibility/orphans, pending new eligibility, thematic active positions,
distinct registrations and overlap counts. No customer names, registry lists,
private campaign IDs, file paths, source text or account identifiers are logged.

The 20,000-purchase/verified-source and 100,000-thematic-position limits plus a
two-second SQL/classification deadline fail to `unavailable` with a fixed
reason. Unknown/failed reads are never reported as zero. No originals are read,
no legal fingerprints calculated and no queue/history rows changed. The
restored getter must itself return promptly; its elapsed source-capture time
is separate from the SQL snapshot, not falsely claimed as atomic. Diagnostics
run at startup and at most every five minutes from the private heartbeat.
The existing ownerless progress GET response is unchanged.

## Opt-in activation and exact frozen request

The default is disabled. After code review and a healthy deployment, the
authorized operator may set the single nonsecret Railway variable:

`EIS223_FULL_RECHECK_REQUEST=2026-10-19-all-saved-214328`

The release accepts this one authorized epoch only. Legal revisions,
deployment SHAs and startup times never generate another request. A repeated
epoch returns the same persisted request without invoking the getter again.

The snapshot is exactly:

`(captured verified set ∪ saved current general campaign members) ∩ purchases − explicit manual inputs`

The request stores expected count 3,058 separately from its exact observed
count, stable sorted-member SHA-256, capture/snapshot timestamps, explicit
manual exclusions and verified source IDs with no saved purchase. Source-only
missing IDs are diagnostics, not invented dashboard members. Other/unknown
company names are included in this explicit user-requested cohort. Invalid
saved registration formats are counted and classified as blocked, not dropped.

Two new small request/member control tables and indexes are additive and
idempotent. No existing table is rebuilt, migrated, emptied or reset. A bounded
atomic snapshot transaction commits its request and all members together;
interruption or a budget failure leaves no partially active request. The
runtime activation marker is set only after that commit. A mismatch with 3,058
remains visible while the actual frozen authorized population is processed;
new arrivals do not silently enlarge that frozen request.

## Scheduling, concurrency and ownership

- The same four general workers and shared physical file-I/O gate remain.
  Worker 1 alternates at most one requested attempt with an ordinary turn.
  No second analysis pool, mass pending reset or normal retry override exists.
  When ordinary work has drained, an empty ordinary turn does not impose a
  30-second delay between successful requested attempts.
- Every ordinary worker can attach a request member to a new ordinary claim
  whose exact claim timestamp is at/after the immutable DB snapshot. Already
  running pre-request work is left alone and does not satisfy this new request.
  Pending company work is shared this way, with ordinary backoff unchanged.
  Missing originals are reported as waiting while preserving that pending row.
- Other members receive their own exact request/attempt/lease identity. Their
  persistence guard replaces only the automatic company scheduling guard.
  The same legal analysis, evidence authenticity and statutory applicability
  rules remain in use. General company admission is not expanded by this path.
- A claim checks active general and thematic reservations under a short SQLite
  writer transaction. General/thematic claims reciprocally exclude a running
  request lease. Stopped/deleted campaign history cannot block a new request;
  its existing start/persist fences remain authoritative and its rows are not
  changed. An incomplete thematic schema still fails closed.
  The existing per-reg intake execution file lock excludes
  concurrent intake work. Expiry alone never permits another executor.
- Leases renew every 30 seconds with a 300-second horizon. Cancellation settles
  late claim/file-lock acquisition and actual write threads before releasing
  the matching identity. Recovery requires the exact expired lease plus the
  exclusive per-reg execution lock. It never resets an unrelated normal task.
- Recovery remains enabled for expired interrupted request leases even if the
  activation variable is absent after restart; it starts no new requested work.
  This prevents a disabled request's stale rows from indefinitely blocking the
  existing general/thematic queues.

## Honest completion and crash boundaries

The attempt captures source and engine identities before analysis. Full
completion requires verified readable input, no unverified readable remainder,
no unreadable/incomplete-source condition, known expected/received original
coverage with no missing expected originals, and an unchanged post-analysis
source/engine snapshot. A returned incomplete source-catalogue snapshot uses
explicit unavailable markers only for non-completable partial receipts, so valid
partial evidence can still be saved. Exceptions while capturing or rechecking
source/engine identities defer the attempt before persistence; none count as
successful completion.

The request receipt commits in the **same SQLite transaction** as the legal
result. The receipt records the exact attempt, source/engine, result token and
coverage. Zero new findings can be a valid completed recheck; it cannot be
inferred merely from missing originals. A separate acknowledgement accepts
only this exact durable full receipt. Process death after result commit but
before acknowledgement recovers from the receipt without re-analysis.

A standalone requested result can acknowledge an unchanged terminal general
row by exact compare-and-set, preventing a redundant ordinary freshness pass.
This cannot overwrite a pending/running/newer ordinary identity. Independent
thematic campaign history remains separate, while existing episode deduplication
keeps repeated general/thematic evidence from inflating unique dashboard counts.
Prior findings, checks, manager decisions, signoffs and source originals remain.

Request status separately reports pending, running, completed, waiting originals,
retry and blocked. Only fully acknowledged members are completed. Partial result
growth is cumulative per committed attempt receipt; repeated receipts and
deduplicated retries cannot inflate it or erase previously committed growth.
Historical result counters are not evidence of newly found violations.

## Validation and remaining live checks

The local suites cover frozen membership (including a synthetic exact 3,058),
old cap expansion, dashboard/queue differences, deadline/interruption rollback,
renewal/fencing, committed-before-ack recovery, mixed/unverified/missing sources,
fresh ordinary sharing, pre-request reservations, explicit noncompany review,
cross-queue exclusion, independent campaign history, duplicate episode counts,
source changes during review, disabled-epoch recovery, repeated cancellation
while renewal settles, and optional-request failure isolation.

All new tests are mandatory in the Docker image and throughput workflow.
Exact test/build totals are recorded in the final integration report. The legal
engine signature remains `1150cdd554dfe28ffeae233f3d6d89231ce13c7b974b1cb0225d71a6f77ccbac`.

After deployment, first verify `FULL_RECHECK_INVENTORY`, then the committed
`FULL_RECHECK_REQUEST` observed count/digest/date/manual exclusions, and finally
`FULL_RECHECK_PROGRESS` state and cumulative committed-result transitions.
Missing originals remain a separate intake/access problem; this request uses
saved evidence and does not create a new bulk remote-download workload. A
population mismatch or blocked member must be reported, not concealed as
“all 3,058 checked.”
