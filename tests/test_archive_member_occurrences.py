"""Archive occurrence regression; source bytes and protective limits stay intact."""
import hashlib
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
import warnings
import zipfile
from unittest.mock import patch

import v310_archive_scan as archives


class ArchiveOccurrenceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def archive(self, entries, kind="zip"):
        path = self.root / ("original." + kind)
        if kind == "zip":
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                with zipfile.ZipFile(path, "w") as stream:
                    for name, data in entries:
                        item = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
                        stream.writestr(item, data)
        else:
            with tarfile.open(path, "w:gz" if kind == "tar.gz" else "w") as stream:
                for name, data in entries:
                    item = tarfile.TarInfo(name)
                    item.size = len(data)
                    stream.addfile(item, io.BytesIO(data))
        return path

    def extract(self, path, uid="A" * 32):
        return archives.extract_members(path, "32619000001", uid, self.root,
                                        lambda p: p.read_text())

    def test_distinct_same_path_entries_survive_zip_tar_and_compressed_tar(self):
        for kind in ("zip", "tar", "tar.gz"):
            with self.subTest(kind=kind):
                path = self.archive([("terms.txt", b"First original evidence"),
                                     ("terms.txt", b"Second original evidence")], kind)
                before = path.read_bytes()
                records, notes = self.extract(path)
                self.assertEqual([r["text"] for r in records],
                                 ["First original evidence", "Second original evidence"])
                self.assertEqual(len({r["name"] for r in records}), 2)
                self.assertEqual(notes, [])
                self.assertEqual(path.read_bytes(), before)
                again, _ = self.extract(path)
                self.assertEqual([(r["name"], r["sha256"]) for r in again],
                                 [(r["name"], r["sha256"]) for r in records])

    def test_identical_repeated_path_is_not_counted_twice(self):
        for kind in ("zip", "tar"):
            with self.subTest(kind=kind):
                path = self.archive([("terms.txt", b"Same original evidence")] * 2, kind)
                records, notes = self.extract(path)
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0]["text"], "Same original evidence")
                self.assertEqual(notes, [])

    def test_distinct_member_names_and_uid_namespaces_stay_distinct(self):
        path = self.archive([("one.txt", b"Same original evidence"),
                             ("two.txt", b"Same original evidence")])
        left, notes = self.extract(path)
        right, _ = self.extract(path, "B" * 32)
        self.assertEqual([r["name"] for r in left], ["one.txt", "two.txt"])
        self.assertEqual(notes, [])
        self.assertEqual([r["sha256"] for r in left], [r["sha256"] for r in right])
        self.assertNotEqual(left[0]["path"], right[0]["path"])

    def test_directory_and_symlink_entries_do_not_shift_occurrence_selection(self):
        path = self.root / "links.zip"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(path, "w") as stream:
                stream.writestr("directory/", "")
                link = zipfile.ZipInfo("terms.txt")
                link.create_system = 3
                link.external_attr = 0o120777 << 16
                stream.writestr(link, b"/tmp/not-evidence")
                stream.writestr("terms.txt", b"First safe original")
                stream.writestr("terms.txt", b"Second safe original")
        records, _ = self.extract(path)
        self.assertEqual([r["text"] for r in records], ["First safe original", "Second safe original"])

    def test_traversal_oversize_and_member_count_guards_remain(self):
        path = self.archive([("../escape.txt", b"Outside"),
                             ("terms.txt", b"Short evidence"),
                             ("terms.txt", b"X" * 33)])
        with patch.object(archives, "MAX_MEMBER", 32):
            records, notes = self.extract(path)
        self.assertEqual([r["text"] for r in records], ["Short evidence"])
        self.assertGreaterEqual(len(notes), 2)
        self.assertFalse((self.root.parent / "escape.txt").exists())
        with patch.object(archives, "MAX_MEMBERS", 2):
            records, notes = self.extract(path)
        self.assertEqual(records, [])
        self.assertTrue(notes)

    def test_name_based_duplicate_libarchive_members_are_explicitly_unread(self):
        path = self.root / "original.7z"
        path.write_bytes(b"7z fake fixture")
        with patch.object(archives, "_list", return_value=([
                ("terms.txt", 10, None), ("terms.txt", 10, None)], [])), \
                patch.object(archives, "_read_member") as read:
            records, notes = self.extract(path)
        self.assertEqual(records, [])
        self.assertTrue(any("Повторяющееся" in note for note in notes))
        read.assert_not_called()

    def test_changed_entry_identity_is_rejected(self):
        for kind in ("zip", "tar"):
            with self.subTest(kind=kind):
                path = self.archive([("one.txt", b"First evidence"),
                                     ("two.txt", b"Second evidence")], kind)
                with self.assertRaises(ValueError):
                    archives._read_member(path, "one.txt", 1)

    def test_symlink_replacing_selected_zip_entry_is_rejected(self):
        path = self.root / "link.zip"
        with zipfile.ZipFile(path, "w") as stream:
            link = zipfile.ZipInfo("terms.txt")
            link.create_system = 3
            link.external_attr = 0o120777 << 16
            stream.writestr(link, b"not evidence")
        with self.assertRaises(ValueError):
            archives._read_member(path, "terms.txt", 0)

    def test_malformed_archive_never_emits_evidence(self):
        path = self.root / "broken.zip"
        path.write_bytes(b"PK\x03\x04not an archive")
        records, notes = self.extract(path)
        self.assertEqual(records, [])
        self.assertTrue(notes)

    def test_nested_duplicate_archives_keep_separate_members(self):
        entries = []
        for text in (b"First nested evidence", b"Second nested evidence"):
            data = io.BytesIO()
            with zipfile.ZipFile(data, "w") as stream:
                stream.writestr("terms.txt", text)
            entries.append(("inner.zip", data.getvalue()))
        records, notes = self.extract(self.archive(entries))
        self.assertEqual([r["text"] for r in records],
                         ["First nested evidence", "Second nested evidence"])
        self.assertEqual(len({r["name"] for r in records}), 2)
        self.assertEqual(notes, [])

    def test_nested_and_literal_paths_cannot_collide_or_suppress_evidence(self):
        for identical in (False, True):
            with self.subTest(identical=identical):
                encoded = io.BytesIO()
                with zipfile.ZipFile(encoded, "w") as stream:
                    stream.writestr("terms.txt", b"Nested evidence")
                other = io.BytesIO()
                with zipfile.ZipFile(other, "w") as stream:
                    stream.writestr("terms.txt", b"Other nested evidence")
                literal = "[запись 1] inner.zip/terms.txt"
                path = self.archive([
                    ("inner.zip", encoded.getvalue()),
                    ("inner.zip", other.getvalue()),
                    (literal, b"Nested evidence" if identical else b"Literal evidence"),
                    ("[источник 1] " + literal, b"Reserved prefix evidence"),
                    ("[запись 1] inner.zip/[источник 1] terms.txt/tail.txt", b"Other path evidence")])
                records, notes = self.extract(path)
                self.assertEqual(len(records), 5)
                self.assertEqual(len({r["name"] for r in records}), 5)
                fragments = [hashlib.sha256(r["name"].encode()).hexdigest()[:24] for r in records]
                self.assertEqual(len(set(fragments)), 5)
                self.assertEqual(records[0]["text"], "Nested evidence")
                self.assertEqual(records[2]["text"], "Nested evidence" if identical else "Literal evidence")
                self.assertEqual(notes, [])
                again, _ = self.extract(path)
                self.assertEqual([r["name"] for r in records], [r["name"] for r in again])

    def test_hash_cache_tamper_and_symlink_restore_original_occurrences(self):
        path = self.archive([("terms.txt", b"First original evidence"),
                             ("terms.txt", b"Second original evidence")])
        records, _ = self.extract(path)
        self.assertEqual(len(records), 2)
        other = self.root / "unrelated.txt"
        other.write_bytes(b"Unrelated data")
        records[0]["path"].write_bytes(other.read_bytes())
        records[1]["path"].unlink()
        records[1]["path"].symlink_to(other)
        restored, _ = self.extract(path)
        self.assertEqual([r["text"] for r in restored],
                         ["First original evidence", "Second original evidence"])
        for record in restored:
            self.assertFalse(record["path"].is_symlink())
            self.assertEqual(hashlib.sha256(record["path"].read_bytes()).hexdigest(), record["sha256"])
        self.assertEqual(other.read_bytes(), b"Unrelated data")


class OccurrenceDateChainTests(unittest.TestCase):
    """Generated source identity must not become part of a legal event number."""

    setUp = ArchiveOccurrenceTests.setUp
    archive = ArchiveOccurrenceTests.archive
    extract = ArchiveOccurrenceTests.extract

    def date_chain(self, family, number="1", collision=False):
        import v348_cross_document_legal as cross
        if family == "clarification":
            names = [f"Запрос № {number}.txt", f"Разъяснение на запрос № {number}.txt"]
            texts = [f"Номер запроса: {number}\nДата поступления запроса: 06.10.2026",
                     f"Номер запроса: {number}\nДата публикации разъяснения в ЕИС: 13.10.2026"]
            analyze = cross.cross_doc_clarifications
        else:
            names = [f"Итоговый протокол № {number}.txt", f"Публикация итогового протокола № {number}.txt"]
            texts = [f"Номер протокола: {number}\nДата подписания итогового протокола: 06.10.2026",
                     f"Номер протокола: {number}\nДата размещения итогового протокола в ЕИС: 13.10.2026"]
            analyze = cross.cross_doc_protocols
        if collision:
            # A nested path and a literal member have equal proposed display
            # names. The latter has no legal dates and must not alter identity.
            inner = io.BytesIO()
            with zipfile.ZipFile(inner, "w") as stream:
                stream.writestr(names[0], texts[0].encode())
            entries = [("inner.zip", inner.getvalue()),
                       ("inner.zip/" + names[0], b"Supplementary material"),
                       (names[1], texts[1].encode())]
        else:
            # Exact duplicate entries deduplicate to one source, but its
            # display label still carries a generated occurrence prefix.
            entries = [(names[0], texts[0].encode())] * 2 + [(names[1], texts[1].encode())]
        path = self.archive(entries)
        records, notes = self.extract(path)
        self.assertFalse(notes)
        self.assertEqual(len(records), 3 if collision else 2)
        uid = "A" * 32
        url = "https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid=" + uid
        docs = [{"name": "Материалы.zip / " + record["name"], "text": record["text"],
                 "status": "checked", "url": url,
                 "meta": {"source": "official_eis_archive_member", "source_url": url,
                          "archive_member": record["name"], "sha256": record["sha256"]}}
                for record in records]
        purchase = {"reg": "32619000001", "method": "Аукцион в электронной форме",
                    "published": "2026-10-01", "submission_end": "2026-10-30"}
        return analyze(purchase, docs)

    def test_occurrence_prefix_preserves_explicit_legal_event_number(self):
        for family in ("clarification", "protocol"):
            with self.subTest(family=family):
                self.assertEqual(len(self.date_chain(family)), 1)

    def test_collision_prefix_preserves_explicit_legal_event_number(self):
        for family in ("clarification", "protocol"):
            with self.subTest(family=family):
                self.assertEqual(len(self.date_chain(family, collision=True)), 1)

    def test_generated_prefix_does_not_sanitize_ambiguous_source_number(self):
        for family in ("clarification", "protocol"):
            with self.subTest(family=family):
                self.assertFalse(self.date_chain(family, "1(А)"))


class ReceiptOccurrenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import test_receipt_original_parity as fixtures
        self.fixtures = fixtures
        self.h = fixtures.ReceiptOriginalParity(
            "test_receipt_only_original_has_same_confirmed_finding_in_both_audits")
        # The outer async case owns cleanup; a composed IsolatedAsyncio case
        # has no runner and its doCleanups would silently leave patches active.
        self.h.addCleanup = self.addCleanup
        self.h.setUp()

    async def check_receipt(self, texts, expected_members, expected_findings):
        f, h = self.fixtures, self.h
        reg = h.receipt_purchase()
        h.originals[reg] = []
        data = io.BytesIO()
        encoded = [f.docx_bytes(text) for text in texts]
        if len(set(texts)) == 1:
            encoded = [encoded[0]] * len(texts)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(data, "w") as stream:
                for payload in encoded:
                    stream.writestr(zipfile.ZipInfo("Разъяснение.docx", (2026, 1, 1, 0, 0, 0)), payload)
        original = h.put_original(reg, data.getvalue(), "Разъяснения.zip")
        collected = await f.incoming.offline_audit(h.ns, reg, collect_only=True)
        members = [d for d in collected["docs"] if d["status"] == "checked"]
        self.assertEqual(len(members), expected_members)
        self.assertEqual(len({m["url"] for m in members}), expected_members)
        for member in members:
            self.assertEqual(member["meta"]["uid"], "1".zfill(32))
            self.assertEqual(member["meta"]["source_url"], h.originals[reg][0]["url"])
            self.assertEqual(member["meta"]["archive_sha256"], hashlib.sha256(data.getvalue()).hexdigest())
            self.assertEqual(member["meta"]["sha256"], hashlib.sha256(Path(member["local"]).read_bytes()).hexdigest())
        f.general.initialize(h.ns)
        detail = await h.finish_general(reg)
        self.assertEqual(detail["new_findings"], expected_findings)
        self.assertEqual(detail["documents_read"], expected_members)
        await f.fast.faster_tick(h.ns)
        item, findings = h.themed(reg)
        self.assertEqual((item["status"], len(findings)), ("completed", expected_findings))
        self.assertEqual((await h.finish_general(reg))["new_findings"], 0)
        self.assertEqual(f.fast._prepare(h.ns), [])
        self.assertEqual(original.read_bytes(), data.getvalue())

    async def test_first_duplicate_late_reply_reaches_both_legal_engines(self):
        await self.check_receipt([self.fixtures.LATE_REPLY, self.fixtures.TIMELY_REPLY], 2, 1)

    async def test_identical_copies_remain_one_legal_event(self):
        await self.check_receipt([self.fixtures.LATE_REPLY] * 2, 1, 1)

    async def test_timely_duplicate_documents_do_not_invent_violation(self):
        await self.check_receipt([self.fixtures.TIMELY_REPLY] * 2, 1, 0)
