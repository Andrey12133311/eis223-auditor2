"""Duplicate rows must not discard their distinct evidence metadata."""
import json
import unittest
import test_reconciliation_lock_scope as fixture

class ReconciliationProvenance(fixture.ReconciliationLockScope):
    def test_deleted_duplicate_is_retained_as_complete_source_snapshot(self):
        metadata={"source":"official_eis_original","publication":"2026-10-02",
                  "protocol_type":"consideration",
                  "identical_sources":[{"name":"earlier.pdf","url":"https://example.org/prior"}]}
        with self.connect() as db:
            db.execute("ALTER TABLE docs ADD COLUMN published_at TEXT")
            db.execute("ALTER TABLE docs ADD COLUMN doc_type TEXT")
            db.execute("UPDATE docs SET metadata_json=?,published_at='2026-10-02',doc_type='protocol' WHERE id=2",
                       (json.dumps(metadata),))
        self.assertEqual(self.reconcile('123')["removed"],1)
        with self.connect() as db:
            row=dict(db.execute("SELECT * FROM docs").fetchone())
        sources=json.loads(row["metadata_json"])["identical_sources"]
        removed=next(item for item in sources if item.get("id")==2)
        self.assertEqual(removed["published_at"],"2026-10-02")
        self.assertEqual(removed["doc_type"],"protocol")
        self.assertEqual(json.loads(removed["metadata_json"]),metadata)
        self.assertTrue(all(path.exists() for path in self.files))

if __name__=="__main__":unittest.main()
