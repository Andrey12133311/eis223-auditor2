import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from patch_v256 import patch_file, patch_text, RULES


class SourcePatchTests(unittest.TestCase):
    def test_exact_anchor_guard(self):
        with self.assertRaisesRegex(RuntimeError, "Expected one patch anchor"):
            patch_text("def noop(): return None")

    def test_changed_checksum_refuses_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "orig.py"
            dst = Path(tmp) / "target.py"
            src.write_text("# unexpected version\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "checksum"):
                patch_file(src, dst)
            self.assertFalse(dst.exists())

    def test_identical_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "entry.py"
            f.write_text("\n")
            with self.assertRaises(ValueError):
                patch_file(f, f)

    def test_transport_and_metadata_patterns(self):
        self.assertIn("ns['httpx'].TransportError", RULES[0][1])
        self.assertIn("ns['httpx'].TransportError", RULES[1][1])
        self.assertIn("isinstance(d.get('meta'),dict)", RULES[2][1])
        self.assertIn("isinstance(alt_urls,str)", RULES[3][1])
        self.assertIn("u.startswith(('https://','http://'))", RULES[3][1])

    def test_in_place_patches_not_a_data_migration(self):
        for before, after in RULES:
            self.assertNotIn("DELETE FROM", after)
            self.assertNotIn("DROP TABLE", after)
            self.assertNotIn("program-env.json", after)


if __name__ == "__main__":
    unittest.main()
