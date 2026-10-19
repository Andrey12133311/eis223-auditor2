# Evidence-preserving legal recall corrections — 2026-10-19

Baseline: `14ea36d5dc956853b712f188a9633e9971850a97`, production branch
`thematic-audit-20261015`. This document describes local validation, not a
production acceptance or a finding against any named real procurement.

## Confirmed failure paths

- Valid amendment history was dropped by browser intake and overwritten by
  later document-only captures. The parser now validates bounded typed events,
  keeps official page and exact attachment identity separate, and merges under
  a per-procurement process lock. Contradictory evidence is rejected, never
  silently replaced. An incomplete event cannot establish a legal violation.
- Ordinary formal amendment decisions, table separators, and Russian month
  dates were missed. Unique reversed table rows are supported; different
  numbered revisions cannot be paired. A generic notice publication or draft
  decision is insufficient. Normalized legal-event dates deduplicate history
  and original copies even if excerpts spell dates differently.
- Independently numbered protocols/requests with identical dates were reduced
  to one event in analysis and display. Identity must come from the complete
  verified original, with conflicting title/body identities kept unresolved.
  Generic older evidence must not erase independently established events.
  Copies and conflicting representations of one attachment/member remain one
  event. Legacy rows without SHA cannot establish identical binary content
  across different UIDs; that absence is not invented. Persistence stays
  append-only; existing evidence and reviewer decisions
  are not rewritten.
- A verified SOAP protocol XML's publication timestamp was discarded before
  legal analysis. The narrow adapter uses only byte/hash-verified protocol XML,
  exact attachment identity, and an explicit original signature date. Notice
  publication, XML creation/modification times, ambiguous versions, and unrelated
  attachment timestamps are not substitutes for protocol publication evidence.

## Before/after fixtures

The same synthetic inputs were executed in isolated worktrees on the baseline
and patch. They are not assertions about procurement 32616233661 or 32616324151.

| Fixture | Baseline | Patch |
| --- | ---: | ---: |
| Formal “Решение от … о внесении изменений” with late explicit publication | 0 | 1 |
| Revision history retained after document-only update | 0 | 1 retained event |
| Retained revision joined to exact verified original | 0 | 1 finding |
| Two independently numbered protocols on the same dates, analyzer | 1 | 2 |
| Same two protocols, displayed count | 1 | 2 |
| One amendment repeated in history/original with ISO/dotted dates | 2 | 1 |
| Timely amendment | 0 | 0 |
| Publication without decision date | 0 | 0 |
| Same protocol copied | 1 | 1 |
| Saved ZIP/XML/receipt/DOCX, explicit signature + XML-only late publication, all installed legal wrappers | 0 | 1 |

The protocol SOAP before/after used `app.py` plus all checked-in legal wrappers:
checked documents = 1 and checklist items = 42 in both trees; confirmed findings
change from 0 to 1. The existing text-only positive control succeeds in both.
The private restored production base was not available for independent local
inspection. No production bulk recheck is automatically triggered by this patch.
Final frozen local suite: 959 tests, 8 failures, 7 errors, 6 skipped. The exact
15 failure/error signatures match the clean baseline below; no new signature
is introduced. All 97 new targeted tests pass (16 amendment extraction,
18 amendment intake, 28 event identity, 21 verified-publication integration,
14 scoped recheck). The independent reviewer found no remaining blocker in
the final runtime scope. Compilation and diff whitespace checks pass. Docker
build was not run locally because Docker is unavailable; the five new suites
are included in the deployment build gates.

## Legal boundaries

No deadline duration or MSP applicability rule is changed. Article 4(11)
requires amendment-decision and amendment-publication evidence; the half-period
extension still requires the buyer's procurement regulation for the actual
method. Article 4(12) uses the protocol signature date and its EIS publication.
Dates are calculated with the existing verified working-day calendar; unknown
calendar/applicability or incomplete facts remain review items.

Official text inspected: [223-FZ, official MChS-hosted consolidated PDF](https://mchs.gov.ru/uploads/document/2025-06-07/81c0ff552c6a12324d38e676f727a820.pdf),
article 4(11)–(13), page 51. The hosted edition includes amendments through
2024-08-08; this is not a claim that a current official consolidation through
2026 was independently retrieved. The EIS-outage exception and other disclosure
exceptions still require case-specific review. This patch does not promise to
identify every legal violation.

## Baseline suite limitations

A clean baseline with the same project dependency environment ran 862 tests:
8 failures, 7 errors, 6 skipped. These must not be reported as a green full suite.

Failures:
- `test_account_owner_isolation.OwnerIsolationTests.test_unowned_memo_download_returns_not_found`
- `test_account_private_api_v1.PrivateResourcesTests.test_unknown_memo_returns_404_not_storage_path`
- `test_account_production_v1.ProductionLoginTests.test_existing_session_skips_entry_forms`
- `test_account_workspace_contract.WorkspaceContracts.test_dashboard_api_fields`
- `test_account_workspace_contract.WorkspaceContracts.test_personal_summary_is_owner_scoped`
- `test_account_workspace_contract.WorkspaceContracts.test_personal_thematic_queue_controls`
- `test_account_workspace_contract.WorkspaceContracts.test_thematic_unlink_is_owner_scoped`
- `test_v262.V262Tests.test_existing_main_page_total_uses_exact_filtered_list`

Errors:
- `test_account_migrations_v1.MigrationTests.test_schema_runs_one_statement_at_a_time`
- `test_account_session_policy_v1.SessionPolicyTests.test_cookie_is_secure`
- `test_account_site_entry_v1.SiteEntryTests.test_logout_revokes_cookie_and_old_session`
- `test_account_site_entry_v1.SiteEntryTests.test_one_cookie_opens_site_and_cabinet_without_another_login`
- `test_v257.FeatureTests.test_dashboard_manual_isolation_and_findings`
- `test_v257.FeatureTests.test_new_manual_request_appears_without_restart`
- `test_v319_manual_inputs.ManualInputTests.test_old_source_and_download_labels_do_not_make_import_manual`

Production acceptance remains separate: install the reviewed, authorized scope,
verify the running commit and startup logs, then compare authorized concrete
procurements using original evidence and visible persisted findings. Public EIS
retrieval failed during this investigation, and no authenticated application
session was available. Existing signed-off findings must not be mutated, and no
mass re-audit is authorized by this document.

## Explicitly scoped operational rechecks

`RULE_REVISION` stays unchanged. Installation alone does not reset completed
queue entries. `scripts/scoped_legal_recheck.py` is a standalone, dry-run-first
maintenance tool; it is not an HTTP endpoint or startup action. It accepts an
existing SQLite path and an explicit list of at most 20 registry numbers, with
no “all” or wildcard mode. Application requires `--apply` after separately
reviewing and authorizing the exact list and preview.

Start with 32616233661 and 32616324151. Only selected existing terminal queue
entries with available local originals can return to `pending`; running/pending
jobs are not interrupted. The ordinary worker reuses receipt-verified evidence
and append-only/idempotent finding persistence. Original documents, stored
findings/checks, reviewer decisions, history, counters, result tokens, and rule
revision are retained by the queueing tool. The subsequent audit may append new
findings and update ordinary result counters. Displayed totals may change as genuinely distinct findings become
visible and duplicate representations are collapsed.

A broader managed recheck requires a separately reviewed explicit inventory,
small batches, and observing completion/results of each batch before the next.
No statement in this runbook authorizes production execution or access around
an authentication boundary. Current runtime access is limited to read-only
provider metadata/logs; administrative execution or an authenticated approved
application workflow is still required for a real scoped recheck.

Preflight example (paths must first be verified against the running deployment):

```sh
python scripts/scoped_legal_recheck.py \
  --database /absolute/path/to/existing.sqlite \
  --originals-root /absolute/path/to/data \
  --reg 32616233661 --reg 32616324151
```

Only after approval and preview, append `--apply`. Missing, pending, running,
concurrently changed, or unreadable-source entries are skipped. Applying the
same still-pending request again is a no-op; a subsequent completed audit is
also finding-idempotent. No credential or authentication setup is included.
