# Preserve price meaning before bid-security qualification

## Supported conclusion

This is a source-provenance correction demonstrated with synthetic fixtures.
It does not establish the correctness of a finding about a real procurement.
No production documents, runtime observations, or private case details form
part of these fixtures or this report.

The low-NMCD rule remains enabled. A verified explicit contract NMCD and an
applicable, affirmative bid-security clause still qualify. An unknown price,
unit-price sum, commodity price, or maximum contract value remains a review
item. The latter values are not silently equated with contract NMCD.

## Reproduced chain

The previous implementation could lose price meaning in several places:

1. `verified_xml_nmcd` searched matching descendant names, without limiting
   the amount to a single documented lot or retaining its price context.
2. `offline_audit` selected a numeric amount independently of its saved type.
   It could copy an original's inferred type solely because the amounts matched.
3. General and thematic context adapters could retain a saved type while
   replacing its amount, or omit the saved type entirely.
4. Current-notice reconciliation treated every SOAP price as NMCD.
5. A generic “initial (maximum) price” heading was sufficient for original and
   browser-card NMCD classification. Previously saved browser classifications
   were not revalidated against the explicit label.

The paired baseline test selection contains eleven cases: nine negative
expectations failed before the correction; two explicit-NMCD positives passed.
All eleven pass after the correction. Tests use temporary SQLite databases,
synthetic DOCX originals, and ZIP/XML receipts validated by the existing
hash/identity collectors.

## Changes

- Read a single documented `lots/lot/lotData` price and retain its kind.
  Duplicate/multiple lots, unsupported nested scalar shapes, foreign namespace
  collisions, invalid cancellation state, foreign currency, and ambiguous
  current fields stay unknown. An explicit unit-price qualification stays typed.
- Persist price, numeric representation, and meaning together in source context.
  Keep current SOAP context through both general and thematic adapters.
- Require explicit contract-NMCD labels in original and browser-card evidence.
  Generic headings remain generic. Revalidate saved browser labels without
  rewriting their historical snapshots.
- Preserve conflicting parsed amounts before deciding which text evidence can
  qualify as NMCD. Unknown or generic alternatives are not erased by filtering.
- Preserve current-revision precedence in both directions: an older unit-price
  card cannot override a newer explicit NMCD; current unit/unknown context
  cannot be relabeled by older NMCD text or equal numbers.
- Keep legacy v338 conflicts active even when an older v341 price snapshot
  exists. The timestamp of the first value cannot date its later unresolved
  alternative or prove that it predates current SOAP.
- Keep review output and existing privacy-bounded funnel diagnostics. The
  allowlisted reason codes are `price_basis_non_nmcd`, `price_basis_conflict`,
  and `verified_nmcd_missing`; no new source text, amount, or URL is logged.

This change does not add a SOAP-only affirmative legal basis, modify legal
thresholds, reset queues, change concurrency/authentication, or remove findings,
originals, source snapshots, history, or manager decisions.

## Format reference and limits

The fixture field names are grounded in the supplied Federal Treasury EIS
223-FZ TFF version 16.3, Appendix 2, “Извещения о закупках”, printed pages 21–22:
`initialSum`, `commodityPrice`, `maxContractPrice`, and `initialSumInfo`.
Printed page 15 describes currency identifiers. The supplied main document's
cover identifies version 16.3 and Moscow, 2026.

This is a versioned supplied reference, not a claim that version 16.3 is the
currently effective deployment format. An effective date and primary download
URL have not been established here. The small XML fixtures are
transport/hash-verified subsets, not full-schema-valid examples. Unknown shapes
are deliberately review-only.

## Regression coverage

`test_price_provenance_chain.py` covers:

- Generic original and browser headings; saved generic evidence; source identity
- Equal-valued multiple lots and unsupported XML descendants/namespaces
- Unit-sum wording, including “сумма цен всех единиц”
- Both directions of current-revision precedence and both engine paths
- Unknown/generic/max-value conflicts, including missing numeric caches
- Saved kind and current price round-trips; bounded review diagnostics
- Tampered-original rejection and preservation of historical rows and bytes
- Explicit contract-NMCD positives and existing bid-security guards

The earlier typed-price suite's positive fixture now explicitly names contract
NMCD. The generic-heading behavior has its own negative regression rather than
being retained as an affirmative expectation. Existing independent manager
submission/approval tests also remain part of verification.

The public legal engine signature changes because source interpretation changes.
The dependency registry includes the saved-type adapter, and mutation tests
verify that these guards affect the signature. The integrated rollout advances the existing same-epoch request to generation 4
using bounded completed-only catch-up. Active claims, immutable membership, prior
receipts and decisions remain preserved. Tests cover both generation 2→3 and
3→4 transitions with identical membership, retained receipts, exactly one new
attempt and no additional finding credit. Reinitialization does not rerun the
already-current receipt; older or conflicting targets remain fenced.
