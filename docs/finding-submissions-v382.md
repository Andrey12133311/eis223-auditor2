# Direct violation approval (V382)

Authenticated employees can use **Направить на согласование руководителю** directly on the normal procurement check card or electronic case. The identity API verifies the authenticated session and current shared EIS evidence key; no automatic legal finding or source document is changed.

The assigned manager sees **Направленные сотрудниками нарушения** under **Согласование нарушений**, opens the exact submitted evidence and chooses **Согласовать** or **Отказать**. Refusal requires a meaningful reason (at least five characters). The employee sees the final decision and manager comment in the original card. There is no correction, return or resubmission action in this flow. Memo approvals and task hand-back remain separate existing workflows.

## Integrity and access

- Additive identity-database table `account_finding_submissions`, initialized by the existing schema initializer.
- Unique owner/registry/evidence key; common submitting-account row lock serializes employee submission and manager decision.
- Duplicate submission/decision returns existing state without duplicate audit records.
- Decision version and current-state checks prevent stale or conflicting outcomes.
- Owner checks and current-team manager scope remain enforced by the existing authenticated-account and membership guards.
- Live evidence must still match before a decision. Canonical evidence reads match active card ordering and grouping inputs.
- No real customer cases, approvals, emails or ingestion jobs are submitted during regression tests.

## Verification

Run with the repository's account dependencies installed:

    PYTHONPATH=.:scripts python -m pytest -q tests/test_v382_finding_submissions.py
    PYTHONPATH=.:scripts python scripts/v368_manager_selftest.py
    PYTHONPATH=.:scripts python scripts/v369_case_report_selftest.py
    PYTHONPATH=.:scripts python scripts/v370_workbench_selftest.py
    node tests/test_account_frontend_requests.cjs

The endpoint lifecycle tests use isolated temporary SQLite databases and a PostgreSQL syntax adapter. They exercise real session-cookie resolution, the production account gate and fast-card entry, HTTP routes, SQL state, membership checks, evidence keys and audit counts, but do not establish PostgreSQL concurrent lock behavior. No authenticated production session is available for live role-based UI acceptance.

## Shared main-list cards correction

The normal `/check-new/{reg}` entry is an authenticated shared EIS read route. It must not require an employee's `account_manual_checks` row. Submissions are attributed exclusively to the current session, with no automatic manual-check creation or disclosure of another user's review state. The manager queue uses the explicit submission as provenance. For shared-only procurements, the assigned manager can list and decide only the exact submitted finding keys; personal case notes, memos and manual checks retain their existing ownership rules.

The duplicate “Правовое основание” display line is removed from violation cards. The “Применённая норма” row and source/legal links remain, without changing legal data or memo output.
