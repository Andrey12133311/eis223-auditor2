"""Archive limits bound decompression work before oversized bytes are read."""
import gzip
import io
from pathlib import Path
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

import v310_archive_scan as archives


class ArchiveExpansionBudget(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="eis-archive-budget-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def tar(self, entries):
        path = self.root / "original.tar.gz"
        with tarfile.open(path, "w:gz") as stream:
            for name, body in entries:
                member = tarfile.TarInfo(name)
                member.size = len(body)
                stream.addfile(member, io.BytesIO(body))
        return path

    def zip(self, entries):
        path = self.root / "original.zip"
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as stream:
            for name, body in entries:
                stream.writestr(name, body)
        return path

    def extract(self, path):
        return archives.extract_members(path, "32619000001", "A" * 32,
                                        self.root, lambda member: member.read_text())

    def gzip_bytes_read(self, archive):
        original = gzip._GzipReader.read
        sizes = []

        def counted(reader, *args, **kwargs):
            data = original(reader, *args, **kwargs)
            sizes.append(len(data))
            return data

        with patch.object(gzip._GzipReader, "read", counted):
            records, notes = self.extract(archive)
        return records, notes, sum(sizes)

    def test_oversized_tar_payload_is_rejected_before_inventory_decompresses_it(self):
        for name in ("oversized.txt", "unused.bin"):
            with self.subTest(name=name):
                archive = self.tar([(name, b"x" * (4 * 1024 * 1024)),
                                    ("later.txt", b"Later original evidence")])
                with patch.object(archives, "MAX_MEMBER", 64 * 1024), \
                        patch.object(archives, "MAX_EXPANDED", 256 * 1024):
                    records, notes, decompressed = self.gzip_bytes_read(archive)
                self.assertLess(decompressed, 128 * 1024,
                                "Listing traversed an oversized compressed payload")
                self.assertEqual(records, [])
                self.assertTrue(notes, "Early rejection must remain incomplete coverage")

    def test_tar_inventory_stops_before_traversing_the_expanded_budget(self):
        archive = self.tar([(f"{i}.txt", b"x" * (64 * 1024)) for i in range(10)])
        with patch.object(archives, "MAX_MEMBER", 128 * 1024), \
                patch.object(archives, "MAX_EXPANDED", 128 * 1024):
            records, notes, decompressed = self.gzip_bytes_read(archive)
        # The retained prefix is decompressed once more to read its evidence.
        self.assertLess(decompressed, 300 * 1024,
                        "TAR inventory ignored the expanded-byte budget")
        self.assertEqual([record["name"] for record in records], ["0.txt", "1.txt"])
        self.assertTrue(notes)

    def test_tar_keeps_readable_prefix_without_resuming_rejected_payload(self):
        archive = self.tar([("good.txt", b"Useful legal terms"),
                            ("big.bin", b"x" * (4 * 1024 * 1024)),
                            ("unvisited.txt", b"Unvisited legal terms")])
        with patch.object(archives, "MAX_MEMBER", 1024):
            records, notes, decompressed = self.gzip_bytes_read(archive)
        self.assertEqual([(record["name"], record["text"]) for record in records],
                         [("good.txt", "Useful legal terms")])
        self.assertTrue(notes)
        self.assertLess(decompressed, 128 * 1024,
                        "Reading the prefix resumed the rejected TAR inventory")

    def test_nested_unsupported_tar_inventories_share_one_work_budget(self):
        inner = self.tar([(f"{i}.bin", b"x" * (64 * 1024)) for i in range(4)]).read_bytes()
        archive = self.zip([(f"{i}.tar.gz", inner) for i in range(10)])
        with patch.object(archives, "MAX_MEMBER", 128 * 1024), \
                patch.object(archives, "MAX_EXPANDED", 256 * 1024):
            records, notes, decompressed = self.gzip_bytes_read(archive)
        self.assertLess(decompressed, 512 * 1024,
                        "Every nested unsupported TAR received a fresh work budget")
        self.assertEqual(records, [])
        self.assertTrue(notes, "An exhausted inventory budget must remain incomplete")

    def zip_bytes_read(self, archive, budget):
        original = zipfile.ZipExtFile.read
        sizes = []

        def counted(reader, *args, **kwargs):
            data = original(reader, *args, **kwargs)
            sizes.append(len(data))
            return data

        with patch.object(zipfile.ZipExtFile, "read", counted), \
                patch.object(archives, "MAX_EXPANDED", budget):
            records, notes = self.extract(archive)
        return records, notes, sum(sizes)

    def test_exhausted_zip_budget_does_not_decompress_every_remaining_member(self):
        archive = self.zip([(f"{i}.txt", b"x" * 32) for i in range(10)])
        records, notes, decompressed = self.zip_bytes_read(archive, 64)
        self.assertEqual(decompressed, 64,
                         "Skipped ZIP members still consumed decompression work")
        self.assertEqual([record["name"] for record in records], ["0.txt", "1.txt"])
        self.assertTrue(notes)
        # Cache hits preserve the incomplete warning; they do not redo rejected work.
        repeated, repeated_notes, repeated_bytes = self.zip_bytes_read(archive, 64)
        self.assertEqual(repeated, records)
        self.assertEqual(repeated_notes, notes)
        self.assertEqual(repeated_bytes, 0)

    def test_zip_member_that_will_not_fit_does_not_hide_a_later_small_document(self):
        archive = self.zip([("first.txt", b"A" * 32), ("large.txt", b"B" * 32),
                            ("later.txt", b"Later")])
        records, notes, decompressed = self.zip_bytes_read(archive, 40)
        self.assertEqual(decompressed, 37)
        self.assertEqual([(record["name"], record["text"]) for record in records],
                         [("first.txt", "A" * 32), ("later.txt", "Later")])
        self.assertTrue(notes)

    def test_failed_unknown_size_members_consume_the_shared_work_budget(self):
        # Only the unavailable libarchive boundary is isolated. The real
        # member dispatcher and recursive budget decide whether another
        # extraction may start after an oversized worker failure.
        archive = self.root / "original.7z"
        archive.write_bytes(b"7z synthetic external-reader fixture")
        listing = SimpleNamespace(returncode=0, stdout=b"a.pdf\nb.pdf\nc.pdf\nd.pdf\n")
        attempted = []

        def oversized(path, name):
            attempted.append(name)
            raise ValueError("Decompressed MAX_MEMBER+1 bytes before rejection")

        with patch.object(archives, "MAX_MEMBER", 32), \
                patch.object(archives, "MAX_EXPANDED", 64), \
                patch.object(archives.subprocess, "run", return_value=listing), \
                patch.object(archives, "_bsdtar_read", side_effect=oversized):
            records, notes = self.extract(archive)
        self.assertEqual(attempted, ["a.pdf", "b.pdf"])
        self.assertEqual(records, [])
        self.assertTrue(notes)


if __name__ == "__main__":
    unittest.main()
