# Whole-site workflow implementation plan

**Goal:** Make the existing light interface clearer and more consistent across the cabinet, account lists, card, profile and workbenches.

**Spec:** ../specs/2026-11-11-ui-workflows-design.md

## Work

- [x] Move the existing cabinet form into the header and add explicit validation, pending state and a duplicate-request guard.
- [x] Refine account navigation, loading/empty/filter states and memo creation disclosure.
- [x] Clarify card file states, evidence and grouped export labels; improve profile loading/saving feedback.
- [x] Refine staff/manager navigation, calendar deadline formatting and partial-refresh feedback.
- [x] Independently review the integrated product changes and correct lifecycle-label inconsistency.
- [x] Complete integrated browser screenshots, offline demo and final verification: 118 combined tests, 26 JavaScript scenarios, two self-tests, 271 preview checks and 35 offline checks passed.
- [x] Record actual results, commit and update the already authorized draft PR113.

No production deployment is part of this plan. Preview/export adapters stay in development tools.
