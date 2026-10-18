"""Infrastructure deferrals preserve a bounded original-download retry budget."""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import v301_search_jobs as jobs


class QueueRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        path = Path(self.tmp.name) / 'queue.sqlite'
        self.ns = {'conn': lambda: sqlite3.connect(path)}
        jobs._init(self.ns)
        # Initialization includes historical repair jobs; keep the fixture
        # independent by directly addressing our own unique queue entry.
        with self.ns['conn']() as db:
            db.execute("INSERT INTO v301_search_jobs(id,kind,target,status,attempts,created_at,updated_at) VALUES('fixture','reg','32610000001','leased',3,'now','now')")

    def tearDown(self):
        self.tmp.cleanup()

    def finish(self, status, accepted=0):
        return jobs._finish(self.ns, 'fixture', status, 'fixture', 0, accepted, defer=60)

    def test_deferred_refunds_only_one_active_lease(self):
        deferred = self.finish('deferred')
        self.assertEqual(deferred['attempts'], 2)
        self.assertEqual(deferred['status'], 'queued')
        self.assertGreater(deferred['not_before'], 0)
        duplicate = self.finish('deferred')
        self.assertEqual(duplicate['attempts'], 2)
        self.assertEqual(duplicate['accepted'], 0)

    def test_deferral_does_not_fabricate_progress(self):
        row = self.finish('deferred')
        self.assertEqual((row['found'], row['accepted']), (0, 0))
        self.assertNotEqual(row['status'], 'done')

    def test_unavailable_original_still_exhausts_budget(self):
        row = self.finish('partial')
        self.assertEqual(row['status'], 'partial')
        self.assertEqual(row['attempts'], 3)

    def test_real_uploaded_progress_renews_budget(self):
        row = self.finish('partial', accepted=1)
        self.assertEqual(row['status'], 'queued')
        self.assertEqual(row['attempts'], 0)

    def test_completed_report_is_idempotent(self):
        row = self.finish('done', accepted=1)
        replay = self.finish('deferred')
        self.assertEqual(replay, row)

    def test_zero_attempts_never_become_negative(self):
        with self.ns['conn']() as db:
            db.execute("UPDATE v301_search_jobs SET attempts=0 WHERE id='fixture'")
        self.assertEqual(self.finish('deferred')['attempts'], 0)


if __name__ == '__main__':
    unittest.main()
