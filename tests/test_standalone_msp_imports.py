"""Keep Docker's script-only entrypoints working without package-root imports."""
import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


class StandaloneMspImportsTests(unittest.TestCase):
    def run_entrypoint(self, filename):
        # A subprocess executing a file has scripts/, not the repository root,
        # on sys.path. Do not add '.' as the main account-test suite does.
        result = subprocess.run([sys.executable, str(ROOT / "scripts" / filename)],
            cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT / "scripts")},
            text=True, capture_output=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_general_card_with_script_only_path(self):
        self.run_entrypoint("v343_card_selftest.py")

    def test_thematic_initialization_with_script_only_path(self):
        self.run_entrypoint("v345_thematic_fast_selftest.py")

    def test_thematic_card_and_print_with_script_only_path(self):
        self.run_entrypoint("v346_thematic_card_selftest.py")

    def test_history_counts_with_script_only_path(self):
        self.run_entrypoint("v351_capacity_selftest.py")

    def test_overview_with_script_only_path(self):
        self.run_entrypoint("v353_thematic_eis_dispatch_selftest.py")


if __name__ == "__main__":
    unittest.main()
