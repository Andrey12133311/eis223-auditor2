# macOS-inspired interface Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Give the existing Russian procurement audit website a coherent, compact light appearance while retaining its navigation and behavior.

**Architecture:** Keep Python-generated HTML and existing JavaScript. Centralize presentation rules in the existing account_visual_polish_v1.py CSS builders, apply these after page-specific layout styles, and use dedicated builders for sign-in, profile and audit-card pages.

**Tech Stack:** Python, inline CSS, existing FastAPI/JavaScript, Chromium/Playwright for validation; no application dependencies added.

**Spec:** ../specs/2026-11-11-macos-interface-design.md

## Global Constraints

- Preserve routes, DOM IDs, role gates, API calls, text and legal/audit behavior.
- Neutral light surfaces, system type, #0066cc primary actions, #1d1d1f body text.
- Keep desktop density and minimum 44px coarse-pointer controls.
- No production database access for browser fixtures.
- Prepare a UI-only release on actual live commit 82f78d29, preserving pending backend work separately.

## Review Focus

- White identity text against newly light toolbars must become readable.
- Later compact styles and inline styles must not override selected navigation colors.
- Long Russian names and 390px screens must not overflow the whole viewport.
- Authentication, role-specific links and keyboard reachability must remain intact.
- Startup imports must work both with /app/scripts and package-based imports.

### Task 1: Shared appearance

**Files:** scripts/account_visual_polish_v1.py, scripts/account_site_ui_v1.py, scripts/v370_staff_ui.py, scripts/v368_manager_ui.py.
**Interfaces:** Keep workspace_style(), home_style(), account_page_style(); add signin_style(), card_style(), profile_style(), each returning one local CSS style element.

- [x] Preserve the existing 20 UI regression checks as the baseline.
- [x] Replace shared presentation rules with neutral tokens and scoped component styling; place final visual layer after compact layouts.
- [x] Update ordering checks and existing contrast-pair checks for the deliberate theme change.
- [x] Run UI contracts and inspect browser-computed toolbar, selected navigation, focus and contrast.

### Task 2: Independent pages and browser verification

**Files:** scripts/v343_fast_cards.py, scripts/v371_profile_ui.py, scripts/account_site_ui_v1.py; tools/render_interface_preview.py (local fixture runner).
**Interfaces:** Existing page builders retain signatures; previews render existing builders with explicitly fictional fixture data, intercept all API requests locally, and never submit to production.

- [x] Apply dedicated shared styles to sign-in, profile and audit cards.
- [x] Render representative populated registry, cabinets, login/register and card states.
- [x] Check 1440, 768 and 390px widths, coarse pointers, navigation tabs, disclosures and inputs; capture before/after evidence.
- [x] Run UI and adjacent authentication/card/profile regression suites. Record any unrelated baseline failures separately.

### Task 3: Review and release

**Files:** Only UI builders, their tests, preview runner and design documentation.

- [x] Obtain independent code review and fix material issues.
- [x] Commit implementation on the feature branch; create release from 82f78d29 with only UI changes.
- [x] Verify the release diff and source/pending Railway state before publication.
- [x] Prepare a concrete tested UI-only release and preview for final publication approval: Railway source changed concurrently from c687b88 to b75f9a9. No source configuration was overwritten and no deployment was initiated. See ../../macos-interface-20261111.md.
