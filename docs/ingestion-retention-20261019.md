# Bounded EIS intake retention fixes

Base: `6263bce3a4b8e56a806f0b11fb3eb1bbfc4934b0` (PR #64).
No production data writes, remote writes, historical finding resets, or bulk rechecks were performed by this work.

## Reproduced losses and fixes

- Accepted SOAP receipt 101 and browser attachment 101 disappeared from the upload allowlist. The cumulative reader now retains all references within explicit bounds, and fails visibly rather than returning a truncated complete-looking list.
- Later browser captures silently truncated their cumulative document union to 100. The per-request limit stays 100; retained history can reach 10,000 documents within an 8 MiB manifest budget. Concurrent captures retain both updates. The transport completion reader uses the same bounds and supports all 101 stored sources.
- Replacing a same-UID filename lost previous reference names. Bounded reference history retains those observations without creating additional documents. Conflicting file extensions for the same UID are rejected without replacing prior evidence.
- Same-hash source promotion and database reconciliation discarded dates, roles, local paths and nested provenance. Duplicate originals still collapse, but complete source metadata survives. Reconciliation retains the existing snapshot/revalidation guard and does not delete source files or findings.
- Same-UID replacement advanced the only receipt pointer. Per-hash version receipts now preserve distinct bytes and bounded same-byte provenance observations under a cross-process UID lock. A valid legacy receipt is retained even if its original is missing or truncated.
- Reuploading an original whose hash-named file contained different bytes falsely reused the damaged file. A bounded repair saves the mismatching bytes and expected/observed hash association in an explicitly unverified quarantine before atomic replacement. Overflow rejects without replacing bytes. Repair forces a new review even if the expected hash did not change.
- Completed/running reviews missed same-count new bytes and new official amendment/XML evidence. Review identity now includes current receipt identities, semantic manifest facts and accepted SOAP XML identities. A changed upload/capture triggers a bounded follow-up; unchanged replays do not. Old completed states without fingerprints remain unchanged unless demonstrably new evidence arrives. Empty optional event arrays do not reset legacy reviews.
- Two identical-byte original UIDs incorrectly stayed partial forever because two source receipts produced one checked document. Duplicate-only completion now verifies the actual nonarchive binaries. Distinct unreadable originals and archives cannot use that shortcut.
- XLSM references were dropped. Ingress validates ZIP structure, workbook part and the macro-enabled package content type. The browser retains an explicit `.xlsm` filename, including when the HTTP filename is generic. Ambiguous workbook ZIPs and MIME alone are not upgraded to XLSM. No macros or formulas are executed.

## Limits and compatibility

- Limits are explicit: 1,000 SOAP receipts, 10,000 known references, 1,000 original versions/provenance observations, 20 quarantine associations per UID, and an 8 MiB retained browser manifest. Existing original upload and archive expansion limits remain in force.
- Source scheduling does not hash or decompress SOAP payloads on the event loop. Receipt identities only decide whether work may be needed; the offline legal reader still validates actual archive/XML/original hashes before using evidence.
- `saved_document` continues to select the current receipt. Historical version receipts preserve provenance; this patch does not automatically analyze every old binary version or multiply legal events. Previously lost metadata cannot be reconstructed merely from an orphaned binary.
- A corrupted already-stored SOAP ZIP is rejected explicitly on reupload; this package does not replace that damaged archive automatically.
- `_save_official_card` has no callsites in the repository. Its helper-only overwrite risk was not misrepresented as a live endpoint loss and was not expanded here.
- Read-only production telemetry reported by the parent at 13:06:24 showed worker 1.4.0 in VPS-only mode with incomplete intakes: 168 expected / 7 received / 161 unresolved (`unsupported_attachment_links`), and 100 expected / 82 received (`response_size_limit`). Those server-side worker/transport limits and unsupported links are not fixed by this package. The local 101-source regressions establish retention and completion-reader behavior, not complete production collection.
- XLSM extraction is supplied by the companion read-only format-extraction patch. These changes preserve the previously reviewed amendment validation and per-procurement manifest locking from PR #64.

## Verification

- 84 focused Python checks pass against the merged legal base, including inherited intake, authorization, concurrency and transport regression cases. Three repeated runs pass.
- Full Python suite: 1,043 tests; 8 failures, 7 errors, 6 skips. The failing test signatures exactly match the pre-existing account/schema/UI fixture failures; this is not a clean full-suite pass.
- The dedicated collector XLSM regression, JavaScript syntax check, and all four existing collector gates (`test_v314_collector.cjs`, `test_v341_collector.cjs`, `test_v376_collector_recovery.cjs`, `test_v377_collector_completion.cjs`) pass. Test-only dependency: pinned `linkedom@0.18.12` outside the repository.
- Python compilation and `git diff --check` pass.
- Independent review additionally exercised twelve concurrent processes, preservation when the final receipt-pointer write fails, same-byte provenance retention, quarantine associations, source-change/no-op behavior, and legacy no-reset behavior.

The changed production files are `v294_eis_bridge.py`, `v295_eis_originals.py`, `v299_browser_intake.py`, `v299_browser_collector.js`, and `v301_search_jobs.py`.
