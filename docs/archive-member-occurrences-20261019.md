# Preserve distinct archive member occurrences

Prepared 2026-10-19 on PR71 merge `838a378f`. This is a standalone extraction
repair; it does not import the separate evidence-storage migration or its
completeness schema. Production originals were not inspected for this defect.

## Reproduced loss

ZIP and TAR allow repeated member paths. Listing preserved both records, but
name-based reads selected the last occurrence twice. Deduplication then retained
only that last payload, silently losing the earlier document. TAR.GZ reproduced
the same defect. An oversized last occurrence also hid an earlier readable one.

The repair retains the exact physical entry index across listing and reading.
Different bytes at one path acquire distinct displayed source names. Repeated
identical bytes at that same path remain one evidence item. Existing source UID,
archive hash, member hash and deterministic member URL mapping remain intact.
Nested-archive ancestry is kept separately from literal member paths. Final
displayed names are allocated collision-free against all emitted names before
URL fragments are derived; genuine root paths cannot impersonate nested members.
A resolved occurrence does not emit an incomplete-coverage warning: doing so
would cause repeat processing despite all bytes having been read. The occurrence
prefix itself discloses the identity. Ambiguous name-only libarchive duplicates
remain unread and explicitly warned; no concatenated payload is treated as one
original.

## Before/after synthetic end-to-end result

A receipt-verified ZIP contains two `Разъяснение.docx` entries: the first has an
existing supported late clarification-response finding; the second is timely.
No legal rule or threshold changes.

| Boundary | PR71 base | Candidate |
| --- | ---: | ---: |
| Readable member documents | 1 | 2 |
| General confirmed findings saved | 0 | 1 |
| Thematic confirmed findings saved | 0 | 1 |

Both queues finish. Replaying unchanged evidence adds zero findings. Identical
copies still yield one member and one legal event. Two timely copies yield zero
violations. These are local fixtures, not growth in real procurement findings.

## Preserved protections and validation

- Member/expanded-byte/count/depth/time limits remain unchanged.
- Traversal, nonregular TAR entries, ZIP symlinks and encrypted/oversized entries
  remain rejected. Selected ZIP entry type is revalidated at read time.
- Malformed archives emit no evidence. Changed indexed names are rejected.
- Original bytes remain unchanged. Existing hash-cache validation restores each
  occurrence from original bytes after tampering or a cached-file symlink; the
  unrelated symlink target is not modified.
- Nested archives retain separate occurrence ancestry. Stable ordinary member
  names and distinct UID namespaces are preserved.
- Fifteen new tests pass; 107 tests covering receipts, both audit paths, source
  versions and legal-event boundaries pass. The new module is included in CI and
  Docker build gates. Real image/bsdtar validation remains in deployment.
- Full suite: 1,267 tests, exactly the known baseline eight failures/seven errors/
  six skips; all failure identifiers match, no new failure. An intermediate
  composed-test cleanup leak was found and fixed; affected module order passes
  82 tests and restores stdout/storage-health mocks. Independent review found
  and verified the nested/literal identity repair and Docker COPY dependencies.
- No database schema, authentication, credentials, manager decisions, history,
  concurrency, legal thresholds or safety limits are changed.

The separate 25-file package's comprehensive partial-extraction contract, Office
parser changes and evidence-storage migration are not included. This patch does
not certify every document format or complete archive coverage beyond existing
resource bounds.

## Concurrent PR72 integration

Reconciled onto PR72 merge `98ac0f2a`. Generated occurrence/collision labels
are prefixes, leaving original explicit event numbers at the end of their
filenames untouched. Suffixes were found to look like an ambiguous continuation
to PR72's strict clarification/protocol identity parser and suppress valid pairs.
Three actual-ZIP regressions cover duplicate-member pairs, nested/literal
collisions and continued rejection of unsupported source identifiers such as
`1(А)`. No legal parser or ambiguity gate is relaxed. Historical 15-test/107-test
counts above describe the standalone review; reconciled counts are recorded in
the combined release manifest.
