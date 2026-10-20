# Source-bound legal recall, 20 October 2026

The analyzer now retains independently numbered protocol events inside one
verified original, an explicit revised submission deadline in an amendment, and
additional explicit working-day payment clauses for exclusive SME purchases.
Existing legal thresholds and procurement applicability gates remain in force.

## Protocols in a compilation

Two complete numbered protocol sections can describe different legal acts even
when their signing and publication dates coincide. A local event descriptor is
created from actual heading and date-label spans in the verified original. It
requires a binary SHA-256, a complete cohort of at least two different supported
protocol numbers, disjoint sections, bounded proximity, and the existing actual
date/template guards. Every explicit supported body number must have a complete
local pair. Unrelated, incomplete, crossed, or unsupported numbers do not gain
new identity. Repeated sections bearing the same number remain one event.

The descriptor is stored in the existing evidence text; no schema migration is
needed. Physical original and binary identity are preserved. Arbitrary
`event_number` fields do not bypass copy guards. The descriptor is internal
parser evidence, not a cryptographic authentication mechanism for caller JSON.

A previously saved unresolved whole-original finding may be refined only by a
validated local descriptor with the same rule, physical original, binary hash,
and dated episode. Its raw evidence stays append-only. The UI prefers proven
local representatives so a generic row cannot bridge two distinct local acts.
For old unique count 1 refined to 2, two new evidence rows are stored, the unique
count becomes 2, and campaign/full-request growth credit is 1. Replay credits 0.
Raw `persisted_confirmed` and `persisted_rules` diagnostics still count inserted
rows, not net unique growth. Planning and unique grouping run before acquiring
SQLite's writer lock; snapshot, reservation, rollback, and rebase fences remain.
Later review-only and unrelated inserts retain the established unique count.

## Revised amendment deadline

An explicit new/after-amendment submission deadline in a verified original is
now retained with that amendment's publication. The existing
`223-4-11-CHANGE-AFTER-BID-DEADLINE` rule can use this evidence even without a
separate EIS change-history record. The current card deadline is never used as
a substitute. Multiple current/revised dates, amendment identities, publication
events, or unresolved source proximity remain review items. No universal
half-term duration is introduced: that check still needs the applicable
procurement regulation and method-specific minimum.
The existing after-deadline rule is explicitly recognized by thematic date
selection, including the ordinary inflected title `срока подачи заявок`.

## SME payment clauses

The existing seven-working-day rule continues to require confirmed exclusive
SME scope and verified source text. Explicit payment-period labels and an
acceptance clock expressed as `с момента` are recognized. The term must belong
to the same payment clause as the acceptance event; unrelated adjacent
sentences, invoice-only clocks, advance terms, and reference/example prose do
not supply that relationship. The confirmed payment rule is also explicitly
listed in the fixed diagnostic taxonomy (17 rule IDs plus `unknown`).

## Rechecks and verification

Legal-engine signatures include the local event constructor, cohort validator,
descriptor reader, and refinement helper. Explicit frozen full-request cohorts
advance from generation 5 to 6 once, preserving cumulative receipts and active
leases. Fixture inserts for the large request-admission test are batched in a
transaction without changing production durability settings.

Regression coverage includes generated DOCX originals, receipt/XML verification,
general and thematic persistence, historical refinement, replay, rollback,
writer-lock boundaries, legacy identity cases, signature invalidation, and
request-generation transitions. The four new test modules run in CI and the
Docker build gate. Synthetic test findings do not establish production growth.
