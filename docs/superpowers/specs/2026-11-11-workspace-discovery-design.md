# Discoverable procurement work

The user accepted the six proposed improvements: attention priorities, unified search, truthful processing stages, adjacent finding/evidence presentation, workspace preferences, and consistent compact interaction styling. Continue the approved light interface and existing draft PR113.

## Implementation direction

Use progressive Python/HTML/CSS/JavaScript builders over the existing authorized endpoints. This preserves the restored runtime, current routes and owner scopes without adding an application framework or changing audit/storage logic. A full application rewrite would add unrelated migration work; a static mockup would not deliver the requested interactions.

## Components

- «Требует внимания» consumes actual personal-check/task reads. Merge reasons per procurement, prioritize real failures/returned/authoritative overdue tasks, distinguish partial reads and unknown counts from an empty successful result. Link to existing card/workbench pages. No additional automatic reads or mutations.
- Search is available on protected surfaces through a native modal and Ctrl/Cmd+K. Search personal saved checks, memos, tasks and thematic runs, plus the loaded registry rows and existing owner-scoped `/me/center/search` results. Clearly state loaded-data scope. Lazy read only; safe constructed local routes, stale-request rejection, explicit access/error/retry feedback and preserved keyboard focus. A new number may prefill the existing form without starting a check.
- Four independent stages explain accepted request, file receipt, extracted text and legal review. Native saved-file evidence and full known coverage are required for complete middle stages. A download count, a missing value or a previous run must not establish current completion. No fictional percentage.
- Findings show the conclusion/norm next to the exact preserved original quote and saved document/location. Narrow screens stack these sections. No fabricated PDF page anchors, altered evidence or changed export/review conditions.
- «Вид» adapts to actual table headers and filter controls. Keep identity and actions visible, offer density/column preferences and up to six named filtersets. Display preferences are local; sensitive filtersets stay in the current tab's account-scoped session storage, with a clearly labelled in-memory fallback. Restore only currently supported fields/options through existing handlers.
- All new controls use the existing palette, visible focus, coarse-pointer targets and reduced-motion support. Keep public sign-in free of protected search/navigation.

## Delivery and limits

Use real builders and coherent fictional fixtures for desktop/tablet/mobile and offline demonstrations. Independently review request/keyboard/storage/evidence invariants. Update the already authorized draft PR after verification. Railway publication remains separate because its source changed concurrently in earlier work; no production deployment or configuration change is included.
