"""Repeated archive audits reuse only byte-checked expansion, never verdicts."""
import concurrent.futures
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import v310_archive_scan as archives


class ArchiveExpansionCache(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="eis-archive-cache-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.archive = self.root / "original.zip"

    def write(self, members, path=None):
        path = path or self.archive
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as stream:
            for name, data in members:
                stream.writestr(name, data)
        return path

    def extract(self, path=None, extractor=None, reg="32619000001", uid="A" * 32):
        return archives.extract_members(path or self.archive, reg, uid, self.root,
                                        extractor or (lambda p: Path(p).read_text()))

    def test_unchanged_original_is_not_expanded_again_and_text_stays_identical(self):
        self.write([("notice.txt", "Дата окончания подачи заявок: 10.11.2026"),
                    ("contract.txt", "Оплата в течение 7 рабочих дней")])
        with patch.object(archives, "_read_member", wraps=archives._read_member) as read:
            first = self.extract()
            second = self.extract()
        self.assertEqual(first, second)
        self.assertEqual(read.call_count, 2, "An unchanged archive was expanded twice")
        self.assertEqual([d["name"] for d in second[0]], ["notice.txt", "contract.txt"])
        self.assertEqual(second[1], [])

    def test_current_extractor_and_unreadable_member_retry_are_preserved(self):
        self.write([("contract.pdf", "actual original bytes"),
                    ("missing.pdf", "another actual original")])
        def initial(path):
            return "Оплата в течение 30 дней" if Path(path).read_text().startswith("actual") else ""
        with patch.object(archives, "_read_member", wraps=archives._read_member) as read:
            first = self.extract(extractor=initial)
            second = self.extract(extractor=lambda path: "Оплата в течение 7 рабочих дней")
        self.assertEqual(len(first[0]), 1)
        self.assertEqual(first[1], ["Нет читаемого текста: missing.pdf"])
        self.assertEqual(len(second[0]), 2)
        self.assertEqual(second[1], [])
        self.assertTrue(all(d["text"] == "Оплата в течение 7 рабочих дней" for d in second[0]))
        self.assertEqual(read.call_count, 2)

    def test_structural_warning_remains_on_every_partial_audit(self):
        self.write([("contract.txt", "Сохранённый договор"), ("../bad.txt", "unsafe")])
        with patch.object(archives, "_read_member", wraps=archives._read_member) as read:
            first = self.extract()
            second = self.extract()
        self.assertEqual(first, second)
        self.assertEqual(second[1], ["Пропущен небезопасный путь внутри архива"])
        self.assertEqual(read.call_count, 1)

    def test_changed_original_invalidates_expansion(self):
        self.write([("contract.txt", "Срок оплаты 30 дней")])
        first, _ = self.extract()
        self.write([("contract.txt", "Срок оплаты 7 рабочих дней")])
        second, _ = self.extract()
        self.assertNotEqual(first[0]["sha256"], second[0]["sha256"])
        self.assertEqual(second[0]["text"], "Срок оплаты 7 рабочих дней")

    def test_missing_modified_and_symlink_member_are_restored_from_original(self):
        self.write([("contract.txt", "Подлинный текст договора")])
        original, _ = self.extract()
        member = original[0]["path"]
        outside = self.root / "unrelated.txt"
        outside.write_text("Сторонний текст")
        for mode in ("changed", "missing", "symlink"):
            with self.subTest(mode=mode):
                if mode == "changed":
                    member.write_text("Подменённый текст")
                else:
                    member.unlink()
                    if mode == "symlink":
                        member.symlink_to(outside)
                restored, warnings = self.extract()
                self.assertEqual(restored, original)
                self.assertEqual(warnings, [])
                self.assertFalse(member.is_symlink())
                self.assertEqual(outside.read_text(), "Сторонний текст")

    def test_limits_are_part_of_expansion_identity(self):
        self.write([("a.txt", "Первый документ"), ("b.txt", "Второй документ")])
        self.assertEqual(len(self.extract()[0]), 2)
        with patch.object(archives, "MAX_MEMBERS", 1):
            records, warnings = self.extract()
        self.assertEqual(records, [])
        self.assertEqual(warnings, ["Слишком много файлов в ZIP"])

    def test_reg_uid_and_returned_records_are_isolated(self):
        self.write([("contract.txt", "Подлинный текст")])
        first, _ = self.extract()
        first[0]["name"] = "Caller changed display name"
        first[0]["text"] = "Caller changed text"
        second, _ = self.extract(reg="32619000002", uid="B" * 32)
        again, _ = self.extract()
        self.assertEqual(second[0]["name"], "contract.txt")
        self.assertIn("32619000002", str(second[0]["path"]))
        self.assertIn("B" * 32, str(second[0]["path"]))
        self.assertEqual(again[0]["name"], "contract.txt")
        self.assertEqual(again[0]["text"], "Подлинный текст")

    def test_duplicate_concurrent_audits_share_expansion_but_not_result_objects(self):
        self.write([("contract.txt", "Подлинный текст")])
        with patch.object(archives, "_read_member", wraps=archives._read_member) as read:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                result = list(pool.map(lambda unused: self.extract(), range(2)))
        self.assertEqual(result[0], result[1])
        self.assertIsNot(result[0][0][0], result[1][0][0])
        self.assertEqual(read.call_count, 1)

    def test_archive_format_and_member_name_are_not_inferred_from_hash_only(self):
        # A standalone compressed file derives its source name from the actual
        # filename. Equal gzip bytes must not reuse another source's name/type.
        import gzip
        payload = gzip.compress("Подлинный текст".encode())
        text = self.root / "contract.txt.gz"
        pdf = self.root / "contract.pdf.gz"
        text.write_bytes(payload)
        pdf.write_bytes(payload)
        first, _ = self.extract(text)
        second, _ = self.extract(pdf, extractor=lambda unused: "Text from current PDF parser")
        self.assertEqual(first[0]["name"], "contract.txt")
        self.assertEqual(second[0]["name"], "contract.pdf")
        self.assertEqual(second[0]["text"], "Text from current PDF parser")

    def test_transient_archive_open_failure_is_retried(self):
        self.write([("contract.txt", "Подлинный текст")])
        with patch.object(archives, "_list", side_effect=OSError("temporary unavailable")):
            first, warnings = self.extract()
        self.assertEqual(first, [])
        self.assertTrue(warnings)
        second, warnings = self.extract()
        self.assertEqual(len(second), 1)
        self.assertEqual(warnings, [])

    def test_transient_member_unpack_failure_is_retried(self):
        self.write([("contract.txt", "Подлинный текст")])
        with patch.object(archives, "_read_member", side_effect=OSError("temporary unavailable")):
            first, warnings = self.extract()
        self.assertEqual(first, [])
        self.assertTrue(warnings)
        second, warnings = self.extract()
        self.assertEqual(len(second), 1)
        self.assertEqual(warnings, [])

    def test_nested_warning_keeps_original_source_name_and_order(self):
        self.write([("folder/missing.pdf", "Actual file bytes"),
                    ("../unsafe.txt", "unsafe")])
        expected = ["Нет читаемого текста: folder/missing.pdf",
                    "Пропущен небезопасный путь внутри архива"]
        for _ in range(2):
            records, warnings = self.extract(extractor=lambda unused: "")
            self.assertEqual(records, [])
            self.assertEqual(warnings, expected)

    def test_cache_has_a_fixed_entry_bound_and_eviction_preserves_evidence(self):
        self.write([("contract.txt", "Подлинный текст")])
        with patch.object(archives, "_EXPANSION_CACHE_LIMIT", 2):
            first = self.extract(uid="A" * 32)
            self.extract(uid="B" * 32)
            self.extract(uid="C" * 32)
            self.assertLessEqual(len(archives._expansion_cache), 2)
            again = self.extract(uid="A" * 32)
            self.assertEqual(first, again)
            self.assertLessEqual(len(archives._expansion_cache), 2)

    def test_text_replacement_after_expansion_is_not_bound_to_original_hash(self):
        for extension in ("txt", "csv"):
            with self.subTest(extension=extension):
                self.write([("contract." + extension, "Original payment 07 days")])
                expected, _ = self.extract()
                expanded = archives._expanded_members

                def replace_after_validation(*args):
                    events = expanded(*args)
                    for event in events:
                        if event[0] == "document":
                            Path(event[2]).write_text("Modified payment 30 days")
                    return events

                with patch.object(archives, "_expanded_members", side_effect=replace_after_validation):
                    records, warnings = self.extract()
                self.assertEqual(records, [], "Replaced text was attached to the original SHA256")
                self.assertTrue(warnings)
                recovered, warnings = self.extract()
                self.assertEqual(recovered, expected)
                self.assertEqual(warnings, [])

    def test_binary_replacement_before_extractor_is_not_bound_to_original_hash(self):
        self.write([("contract.pdf", "Original payment 07 days")])
        expected, _ = self.extract()
        expanded = archives._expanded_members

        def replace_after_validation(*args):
            events = expanded(*args)
            Path(next(event[2] for event in events if event[0] == "document")).write_text(
                "Modified payment 30 days")
            return events

        with patch.object(archives, "_expanded_members", side_effect=replace_after_validation):
            records, warnings = self.extract()
        self.assertEqual(records, [], "Replaced PDF bytes were attached to the original SHA256")
        self.assertTrue(warnings)
        recovered, warnings = self.extract()
        self.assertEqual(recovered, expected)
        self.assertEqual(warnings, [])

    def test_binary_replacement_during_extraction_is_not_bound_to_original_hash(self):
        self.write([("contract.pdf", "Original payment 07 days")])

        def replace_during_extraction(path):
            Path(path).write_text("Modified payment 30 days")
            return Path(path).read_text()

        records, warnings = self.extract(extractor=replace_during_extraction)
        self.assertEqual(records, [], "Extraction racing a replacement was emitted as original evidence")
        self.assertTrue(warnings)
        recovered, warnings = self.extract()
        self.assertEqual(recovered[0]["text"], "Original payment 07 days")
        self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()
