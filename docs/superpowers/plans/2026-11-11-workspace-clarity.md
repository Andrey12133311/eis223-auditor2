# Workspace clarity Implementation Plan

> **For agentic workers:** Execute the scoped interface changes with independent review and browser verification.

**Goal:** Make the procurement workspace easier to understand by separating views and reducing duplicate navigation.
**Architecture:** Keep existing authorized page builders, add client-side mutually exclusive view state and hash navigation, refine only cabinet markup/CSS and labels. No backend or Railway change.
**Tech Stack:** Existing Python/HTML/CSS/JavaScript, local Playwright/Chromium.
**Spec:** ../specs/2026-11-11-workspace-clarity-design.md

## Global Constraints

Preserve routes, role gates, owner-scoped reads, audit results, exports and all actionable DOM IDs. Keep «Мой кабинет». Do not turn missing data into zero or imply a complete audit from document download. Keep no-JavaScript fallback navigation. Preview requests and data are local and fictional.

## Review Focus

Hidden-view focus, Back/Forward and initial hash, cabinet/registry scroll positions, mobile overlays, duplicated navigation visibility, missing counts and inspector races/errors/security.

### Task 1: View controller

- [x] Add meaningful accessible route/label contracts and observe failures.
- [x] Implement mutually exclusive views, active selection, hash/history and focus/scroll preservation.
- [x] Verify browser behavior and inspector context changes.

### Task 2: Cabinet clarity

- [x] Clear heading, single primary action, calmer summary/list hierarchy, fewer duplicate controls.
- [x] Align tasks/workbench labels while retaining routes/tab IDs.
- [x] Verify role/accessibility/DOM and genuine missing-data states.

### Task 3: Evidence and handover

- [x] Refresh coherent fictional fixtures, desktop/mobile cabinet screenshots and standalone demo.
- [x] Run focused and existing regression suites, obtain independent review, fix material findings.
- [x] Commit and update the already authorized draft PR; deliver preview without deploying Railway.
