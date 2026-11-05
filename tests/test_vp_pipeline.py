"""Exercise the sheet hook through the actual persisted general/thematic flow."""
from functools import wraps
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_general_thematic_parity as fixture
import vp_inspection_engine as engine
import vp_inspection_export as export


class InspectionPipeline(unittest.TestCase):
    def test_committed_audit_fills_same_sheet_for_general_and_thematic(self):
        original_install = fixture.protocols.install
        stages = []

        @wraps(original_install)
        def install(ns):
            original_install(ns)
            engine.install(ns)

            def save(p, docs, findings, checks, detail, card):
                # General evidence commits before the sheet is published.
                with ns["conn"]() as connection:
                    self.assertGreater(connection.execute("SELECT COUNT(*) FROM findings").fetchone()[0], 0)
                snapshot = export.save_snapshot(ns, p, docs, findings, checks, detail, card)
                stages.append(snapshot)

            ns["_vp_save_snapshot"] = save

        with tempfile.TemporaryDirectory() as directory, \
             patch.object(export, "root", return_value=Path(directory)), \
             patch.object(fixture.protocols, "install", side_effect=install):
            first, second, thematic, _, _ = fixture.compare(fixture.docs)
            snapshot = export.read_snapshot(fixture.REG)
        self.assertEqual((first[0], second[0], thematic[0]), ("completed",) * 3)
        self.assertEqual(len(stages), 3)
        self.assertEqual(len(snapshot["rows"]), 19)
        self.assertEqual(snapshot["fields"]["REG_NUMBER"], fixture.REG)
        self.assertTrue(all(row["comment"] == "" for row in snapshot["rows"]))
        rows = {row["key"]: row for row in snapshot["rows"]}
        self.assertEqual(rows["protocol_compliance"]["has_violation"], "✓")
        self.assertEqual(rows["national_1875"]["no_violation"], "")
        self.assertEqual(stages[0]["document_fingerprint"], stages[-1]["document_fingerprint"])

    def test_sheet_write_failure_is_not_reported_as_completed_audit(self):
        original_install = fixture.protocols.install

        def install(ns):
            original_install(ns)
            def unavailable(*args):
                raise OSError("inspection snapshot unavailable")
            ns["_vp_save_snapshot"] = unavailable

        with patch.object(fixture.protocols, "install", side_effect=install):
            with self.assertRaisesRegex(OSError, "inspection snapshot"):
                fixture.compare(fixture.docs)


if __name__ == "__main__":
    unittest.main()
