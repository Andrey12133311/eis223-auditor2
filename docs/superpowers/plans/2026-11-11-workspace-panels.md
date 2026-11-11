# Workspace panels Implementation Plan

> **For agentic workers:** Execute the approved behavior with independent read-only review. Steps use checkbox syntax.

**Goal:** Show a working compact two-panel procurement workspace and its mobile behavior.
**Architecture:** Add progressive navigation/inspector markup to the existing homepage builder, read saved same-origin card HTML, sanitize cloned sections, retain all server authorization and audit decisions. Existing card/manager builders receive focused presentation and navigation enhancements.
**Tech Stack:** Existing Python/HTML/CSS/JavaScript; Playwright/Chromium for local fixtures. No application dependencies.
**Spec:** ../specs/2026-11-11-workspace-panels-design.md

## Global Constraints

- Existing routes, IDs, authentication, authorization, legal outcomes and exports remain intact.
- No new backend read or mutation API; only saved-card GET for inspector.
- No invented counts, overdue status or filter options.
- Narrow overlays must handle focus, inert, Escape and44px touch controls.
- All preview data is fictional and every demo request is local.

## Review Focus

Stale requests, expired sessions, hostile returned HTML, loss of evidence warnings, CSS override conflicts, resizing with saved menu preference, real task-filter semantics and loaded-row count limits.

### Task1: Shared navigation and read-only inspector

- [x] Write focused markup/role contracts, observe missing module failure.
- [x] Implement workspace_panels_v1.py and homepage integration, safe copy controls and adaptive existing-filter shortcuts.
- [x] Run role/DOM/legacyUI tests; browser-test tabs, race, failure, sanitizer, focus, mobile/inert and state retention.

### Task2: Card and daily-work refinements

- [x] Add compact card summary, explicit stage states, semantic section hooks, dates and accessible mobile document records; preserve general/thematic separation and warnings.
- [x] Make manager KPIs native buttons using existing server-supported filters, correct empty states/count labels and monoSVG icons.
- [x] Reduce nested staff frames; run focused/adjacent unit and browser checks.

### Task3: Preview and review

- [x] Generate fictional fixture pages and an offline interactive HTML containing real UI builders.
- [x] Capture desktop panels/collapsed/evidence, mobile menu/inspector/card and manager views.
- [x] Independent review; preserve incomplete-evidence/export warning notices in both inspector summary and findings.
- [x] Final evidence: 96 unit checks (1 skip), two existing UI/auth self-tests, 209 browser checks, fresh desktop/mobile screenshots and offline HTML smoke. Commit and hand over without publishing production.
