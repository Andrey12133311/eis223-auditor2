"""First-pass archive extraction reuses bounded handles without losing evidence."""
import hashlib
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import v310_archive_scan as archives


class ArchiveFirstPass(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="eis-archive-first-pass-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def tar_bytes(self, entries):
        result = io.BytesIO()
        with tarfile.open(fileobj=result, mode="w:gz") as stream:
            for name, body in entries:
                member = tarfile.TarInfo(name)
                member.size = len(body)
                stream.addfile(member, io.BytesIO(body))
        return result.getvalue()

    def extract(self, archive):
        return archives.extract_members(archive, "32619000001", "A" * 32,
                                        self.root, lambda path: path.read_text())

    def test_compressed_tar_is_opened_a_constant_number_of_times(self):
        entries = [(f"document-{i:02}.txt", (f"Evidence {i}\n" * 1024).encode())
                   for i in range(41)]
        archive = self.root / "original.tar.gz"
        archive.write_bytes(self.tar_bytes(entries))
        with patch.object(archives.tarfile, "open", wraps=archives.tarfile.open) as opened:
            records, notes = self.extract(archive)
        self.assertEqual(notes, [])
        self.assertEqual([(item["name"], item["text"], item["sha256"]) for item in records],
                         [(name, body.decode(), hashlib.sha256(body).hexdigest())
                          for name, body in entries])
        self.assertLessEqual(opened.call_count, 3,
                             "Every member reopened and rescanned the compressed TAR")

    def test_zip_central_directory_is_not_reparsed_for_each_member(self):
        archive = self.root / "original.zip"
        with zipfile.ZipFile(archive, "w") as stream:
            for i in range(41):
                stream.writestr(f"document-{i:02}.txt", f"Original evidence {i}")
        with patch.object(archives.zipfile.ZipFile, "__init__", autospec=True,
                          side_effect=zipfile.ZipFile.__init__) as opened:
            records, notes = self.extract(archive)
        self.assertEqual(len(records), 41)
        self.assertEqual(notes, [])
        self.assertEqual(opened.call_count, 1,
                         "ZIP central directory was reparsed for each document")

    def test_nested_tar_duplicate_occurrences_and_outer_order_remain_distinct(self):
        inner = self.tar_bytes([("contract.txt", b"First nested evidence"),
                                ("contract.txt", b"Second nested evidence")])
        archive = self.root / "original.tar.gz"
        archive.write_bytes(self.tar_bytes([
            ("before.txt", b"Before nested archive"),
            ("inner.tar.gz", inner),
            ("after.txt", b"After nested archive")]))
        records, notes = self.extract(archive)
        self.assertEqual(notes, [])
        self.assertEqual([item["name"] for item in records], [
            "before.txt", "inner.tar.gz/[запись 1] contract.txt",
            "inner.tar.gz/[запись 2] contract.txt", "after.txt"])
        self.assertEqual([item["text"] for item in records], [
            "Before nested archive", "First nested evidence",
            "Second nested evidence", "After nested archive"])

    def test_open_readers_are_closed_after_all_member_reads_fail(self):
        archive = self.root / "original.tar.gz"
        archive.write_bytes(self.tar_bytes([("contract.txt", b"Original evidence")]))
        opened = []
        original = tarfile.open

        def tracked(*args, **kwargs):
            result = original(*args, **kwargs)
            opened.append(result)
            return result

        with patch.object(archives.tarfile, "open", side_effect=tracked), \
                patch.object(archives, "_read_member", side_effect=OSError("read failed")):
            records, notes = self.extract(archive)
        self.assertEqual(records, [])
        self.assertTrue(notes)
        self.assertTrue(opened)
        self.assertTrue(all(reader.closed for reader in opened))
        # A transient failed extraction cannot become a successful cache entry.
        recovered, notes = self.extract(archive)
        self.assertEqual([item["text"] for item in recovered], ["Original evidence"])
        self.assertEqual(notes, [])

    def test_shared_reader_preserves_tar_symlink_and_traversal_rejection(self):
        archive = self.root / "original.tar.gz"
        with tarfile.open(archive, "w:gz") as stream:
            link = tarfile.TarInfo("contract.txt")
            link.type = tarfile.SYMTYPE
            link.linkname = "/tmp/unrelated-evidence"
            stream.addfile(link)
            for name, body in [("../outside.txt", b"Invalid evidence"),
                               ("contract.txt", b"Real original evidence")]:
                member = tarfile.TarInfo(name)
                member.size = len(body)
                stream.addfile(member, io.BytesIO(body))
        records, notes = self.extract(archive)
        self.assertEqual([item["name"] for item in records], ["contract.txt"])
        self.assertEqual([item["text"] for item in records], ["Real original evidence"])
        self.assertEqual(notes, ["Пропущен небезопасный путь внутри архива"])

    def test_oversized_tar_inventory_stops_at_limit(self):
        archive = self.root / "original.tar.gz"
        archive.write_bytes(self.tar_bytes([
            (f"document-{i:03}.txt", b"Original evidence") for i in range(120)]))
        observed = []
        original = tarfile.TarFile.next

        def counted(reader):
            result = original(reader)
            if result is not None:
                observed.append(result.name)
            return result

        with patch.object(archives, "MAX_MEMBERS", 10), \
                patch.object(archives.tarfile.TarFile, "next", counted):
            records, notes = self.extract(archive)
        self.assertEqual(records, [])
        self.assertEqual(notes, ["Слишком много файлов в TAR"])
        # Includes the initial header probe; must not enumerate all 120 entries.
        self.assertLessEqual(len(observed), 15)


if __name__ == "__main__":
    unittest.main()
