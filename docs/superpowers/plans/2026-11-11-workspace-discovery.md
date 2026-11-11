# Workspace discovery implementation plan

**Goal:** Make next actions, saved materials and verification progress easy to find across the existing procurement website.

**Spec:** ../specs/2026-11-11-workspace-discovery-design.md

- [x] Inspect the actual manual/task/search/file schemas and retain existing owner scope.
- [x] Implement and integrate the attention component with existing read events.
- [x] Implement lazy native-modal search on protected builders, safe navigation/prefill and keyboard/error/race handling.
- [x] Add truthful stage models and native list/card rendering, including independent Python/JS parity checks.
- [x] Present evidence beside the conclusion/norm while preserving exact quotes and review/export conditions.
- [x] Add real-control display/filter preferences and account-scoped per-tab persistence.
- [x] Obtain independent review and fix confirmed search focus/pending-state races and first-Escape handling.
- [x] Complete combined regressions and desktop/tablet/mobile browser checks: 160 combined checks, 26 JavaScript scenarios, two self-tests, 365 browser checks and final contrast regressions passed; standalone preview rebuilt from final sources.
- [x] Record actual results, commit and update the existing authorized draft PR113.

No database, legal-decision logic, application dependency or Railway deployment change is part of this plan.
