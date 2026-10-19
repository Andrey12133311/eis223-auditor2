"""Cumulative official captures are retained without creating extra documents."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
import test_amendment_history_intake as fixture
import v299_browser_intake as intake
from test_ingestion_data_retention import document

class ManifestRetention(fixture.AmendmentHistoryIntakeTests):
    def test_later_capture_keeps_attachment_101_instead_of_acknowledged_truncation(self):
        first=self.save(fixture.payload(documents=[document(i) for i in range(1,101)]))
        self.assertEqual(len(first["documents"]),100)
        saved=self.save(fixture.payload(documents=[document(101)]))
        self.assertEqual(len(saved["documents"]),101)
        self.assertEqual(saved["declared_attachment_count"],101)

    def test_same_uid_filename_revisions_retain_every_reference_without_double_count(self):
        for name in ("first.pdf","middle.pdf","current.pdf"):
            saved=self.save(fixture.payload(documents=[document(1,name=name)]))
        self.assertEqual(len(saved["documents"]),1)
        item=saved["documents"][0]
        self.assertEqual(item["name"],"current.pdf")
        self.assertEqual({ref["name"] for ref in item["reference_history"]},
                         {"first.pdf","middle.pdf","current.pdf"})

    def test_cumulative_limit_rejects_without_replacing_any_stored_attachment(self):
        self.save(fixture.payload(documents=[document(1)]))
        path=intake._manifest_path(fixture.REG);before=path.read_bytes()
        with patch.object(intake,"MAX_STORED_DOCS",1):
            with self.assertRaises(intake.AmendmentHistoryConflict):
                self.save(fixture.payload(documents=[document(2)]))
        self.assertEqual(path.read_bytes(),before)

    def test_cumulative_byte_limit_rejects_without_replacing_previous_capture(self):
        self.save(fixture.payload(documents=[document(1)]))
        path=intake._manifest_path(fixture.REG);before=path.read_bytes()
        with patch.object(intake,"MAX_SOURCE_MANIFEST_BYTES",len(before)):
            with self.assertRaises(intake.AmendmentHistoryConflict):
                self.save(fixture.payload(documents=[document(2)]))
        self.assertEqual(path.read_bytes(),before)

    def test_concurrent_capture_crossing_100_keeps_both_new_documents(self):
        self.save(fixture.payload(documents=[document(i) for i in range(1,100)]))
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda number:self.save(fixture.payload(documents=[document(number)])),(100,101)))
        saved=json.loads(intake._manifest_path(fixture.REG).read_text())
        self.assertEqual({ref["uid"] for ref in saved["documents"]},{document(i)["uid"] for i in range(1,102)})

    def test_repeated_identical_capture_does_not_change_evidence_identity(self):
        value=fixture.payload(documents=[document(1)])
        self.assertTrue(intake._save_manifest(intake._parse_manifest(value),True)[1])
        before=intake._manifest_source_evidence(json.loads(intake._manifest_path(fixture.REG).read_text()))
        self.assertFalse(intake._save_manifest(intake._parse_manifest(value),True)[1])
        after=intake._manifest_source_evidence(json.loads(intake._manifest_path(fixture.REG).read_text()))
        self.assertEqual(before,after)

    def test_legacy_absent_empty_event_array_does_not_trigger_recheck(self):
        value=intake._parse_manifest(fixture.payload(documents=[document(1)]))
        value.pop("changes",None)
        path=intake._manifest_path(fixture.REG);path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(value))
        self.assertFalse(intake._save_manifest(intake._parse_manifest(fixture.payload(documents=[document(1)])),True)[1])

    def test_legacy_manifest_api_replay_does_not_force_review(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        import os
        value=intake._parse_manifest(fixture.payload(documents=[document(1)]));value.pop("changes",None)
        path=intake._manifest_path(fixture.REG);path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(value));reviews=[]
        app=FastAPI();intake.install({"app":app,"_v295_enqueue_review":lambda *args,**kwargs:reviews.append((args,kwargs))})
        headers={"origin":fixture.BASE,"x-eis223-upload-token":"synthetic-test-token"}
        with patch.dict(os.environ,{"EIS223_UPLOAD_TOKEN":"synthetic-test-token"}), \
             patch.object(intake,"_apply_customer",return_value=False),TestClient(app) as client:
            response=client.post("/api/v299/eis223/public-manifest",json=fixture.payload(documents=[document(1)]),headers=headers)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(reviews,[])

    def test_same_uid_format_conflict_is_explicit_and_preserves_original_reference(self):
        self.save(fixture.payload(documents=[document(1,name="original.pdf")]))
        path=intake._manifest_path(fixture.REG);before=path.read_bytes()
        with self.assertRaises(intake.AmendmentHistoryConflict):
            self.save(fixture.payload(documents=[document(1,name="changed.xlsx")]))
        self.assertEqual(path.read_bytes(),before)

if __name__=="__main__":unittest.main()
