"""Retained multi-capture history must not leave the transport queue stuck."""
import json
import unittest
from unittest.mock import patch
import test_v377_server_completion as fixture
import v295_eis_originals as originals
import v301_search_jobs as jobs

class RetainedDownloadCompletion(fixture.ServerCompletionTests):
    def test_all_101_stored_browser_originals_complete(self):
        for number in range(13,102):
            uid=f"{number:032X}"
            item={"uid":uid,"name":f"document-{number}.pdf","ext":".pdf",
                  "url":"https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid="+uid}
            originals._store_original_version(self.reg,uid,item,b"%PDF-1.7\n"+str(number).encode())
            self.documents.append(item)
        self.manifest["declared_attachment_count"]=101;self.write_manifest()
        self.assertTrue(jobs._stored_download_complete(self.reg))
        self.assertEqual(self.report().json()["status"],"done")

    def test_cumulative_document_bound_remains_explicitly_incomplete(self):
        before=self.manifest_path.read_bytes()
        with patch.object(jobs,"MAX_KNOWN_DOCUMENTS",11):
            self.assertFalse(jobs._stored_download_complete(self.reg))
        self.assertEqual(self.manifest_path.read_bytes(),before)

    def test_manifest_byte_bound_remains_incomplete(self):
        before=self.manifest_path.read_bytes()
        with patch.object(jobs,"MAX_SOURCE_MANIFEST_BYTES",100):
            self.assertFalse(jobs._stored_download_complete(self.reg))
        self.assertEqual(self.manifest_path.read_bytes(),before)

if __name__=="__main__":unittest.main()
