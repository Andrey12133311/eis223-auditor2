"""Rejected uploads expose fixed diagnostics without logging payloads or secrets."""
from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
import v294_eis_bridge as archive
import v295_eis_originals as originals

REG="32616474277"
UID="A"*32
TOKEN="synthetic-upload-token-do-not-log"
SECRET="synthetic-sensitive-content-do-not-log"


class UploadRejectionLoggingTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory=Path(temp.name)
        env=patch.dict(os.environ,{
            "EIS223_UPLOAD_TOKEN":TOKEN,"EIS223_DATA_DIR":temp.name,
            "EIS223_SOURCE_MODE":"OFFICIAL_EIS_VPS_ONLY"})
        env.start();self.addCleanup(env.stop)
        app=FastAPI()
        archive.install({"app":app})
        originals.install({"app":app})
        self.client=TestClient(app)
        self.addCleanup(self.client.close)

    def reject(self,path,data):
        log=io.StringIO()
        with redirect_stdout(log):
            response=self.client.post(path,content=data,
                headers={"x-eis223-upload-token":TOKEN})
        self.assertEqual(response.status_code,422,response.text)
        self.assertNotIn(TOKEN,log.getvalue())
        self.assertNotIn(SECRET,log.getvalue())
        self.assertNotIn("https://",log.getvalue())
        self.assertFalse(any(self.directory.rglob("receipt.json")))
        return response,log.getvalue()

    def test_invalid_zip_keeps_rejection_and_logs_only_code_and_size(self):
        data=SECRET.encode()
        response,log=self.reject("/api/v294/eis223/archive",data)
        self.assertEqual(response.json()["detail"],"Invalid ZIP")
        self.assertEqual(log,f"V294 ARCHIVE_REJECTED code=invalid_zip bytes={len(data)}\n")
        self.assertFalse((self.directory/"eis223_official_archive_inbox").exists())

    def test_unsupported_xml_type_is_not_copied_into_logs(self):
        kind="purchaseNotice"+SECRET
        data=io.BytesIO()
        with zipfile.ZipFile(data,"w") as zipped:
            zipped.writestr("purchaseNotice_"+REG+"_fixture.xml",
                '<'+kind+' xmlns="http://zakupki.gov.ru/223fz/purchase/1"/>')
        response,log=self.reject("/api/v294/eis223/archive",data.getvalue())
        self.assertTrue(response.json()["detail"].startswith("Unsupported official EIS XML type: "))
        self.assertIn("code=unsupported_xml_type ",log)

    def test_html_instead_of_original_keeps_rejection_and_logs_fixed_code(self):
        allowed={UID:{"ext":".pdf","url":"https://zakupki.gov.ru/?token="+SECRET}}
        with patch.object(originals,"allowed_documents",return_value=allowed):
            response,log=self.reject("/api/v295/eis223/original/"+REG+"/"+UID,
                                     ("<html>"+SECRET+"</html>").encode())
        self.assertEqual(response.json()["detail"],"ЕИС вернула HTML вместо оригинального файла")
        self.assertIn("V295 ORIGINAL_REJECTED reg="+REG+" code=original_is_html ",log)

    def test_unknown_validation_text_never_enters_logs(self):
        error=ValueError(SECRET+"\nhttps://example.invalid/?token="+TOKEN)
        with patch.object(archive,"parse_notices",side_effect=error):
            response,log=self.reject("/api/v294/eis223/archive",b"fixture")
        self.assertEqual(response.json()["detail"],str(error))
        self.assertIn("code=unclassified_validation_error ",log)
        with patch.object(originals,"allowed_documents",return_value={UID:{"ext":".pdf"}}), \
             patch.object(originals,"assert_format",side_effect=error):
            response,log=self.reject("/api/v295/eis223/original/"+REG+"/"+UID,b"fixture")
        self.assertEqual(response.json()["detail"],str(error))
        self.assertIn("code=unclassified_validation_error ",log)


if __name__=="__main__":
    unittest.main()
