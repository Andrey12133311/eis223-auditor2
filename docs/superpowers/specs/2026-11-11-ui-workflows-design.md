# Whole-site workflow refinement

The user asked to continue improving the entire procurement website after merging the two primary sidebar groups. Keep the existing restrained light theme and the settled name «Мой кабинет».

## Intended behavior

- Put the procurement number field and its real submit action together at the top of the cabinet. Validate locally, show pending/success/error feedback, retain the number after failure, and prevent duplicate requests while pending. Acceptance of a request never means a legal audit is complete.
- Give protected account lists consistent return links and active section navigation. Distinguish missing counts from zero, a genuinely empty list from filtered results, and initial failure from a successful empty response. Offer a clear next action or retry.
- Make the memo archive primary and place the existing creation form in a native disclosure. Preserve the routes, required requisites, review decisions and export behavior.
- Explain document retrieval and text extraction independently of legal conclusions. Group card exports with format labels and retain evidence warnings and export conditions.
- Give the profile clear loading, retry and saving feedback. Disable edits until data loads, preserve edits after rejected saves and retain keyboard focus where appropriate.
- Align staff/manager navigation and empty states. Preserve saved calendar deadlines without browser timezone shifts, and report partial refresh failures truthfully.

## Boundaries

No authorization, ownership, storage, audit logic or Railway changes. Keep existing actionable IDs and endpoint contracts. Use fictional local fixtures for previews; the private restored registry runtime is represented by structural fixture markup, not a production authenticated session.

## Validation

Use focused Python regressions, executable JavaScript request/race checks, real Chromium at desktop/tablet/mobile sizes, pending and failed requests, keyboard interactions, inspector security/races, and an offline demo with no production requests. Obtain independent review and update the existing authorized draft PR113 after passing checks.
