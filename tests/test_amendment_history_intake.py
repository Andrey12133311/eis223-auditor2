"""Bounded offline amendment history survives official-browser manifest intake."""
import hashlib
import json
import multiprocessing
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
import v299_browser_intake as intake
import v330_amendments_223 as amendments

REG="32616233661"
OTHER_REG="32616233662"
BASE="https://zakupki.gov.ru"
PAGE=BASE+"/223/purchase/public/purchase/info/documents.html?regNumber="+REG
FILE=BASE+"/223/filestore/public/1.0/download/fz223/file.html?uid="+"A"*32
OTHER_FILE=FILE[:-32]+"B"*32


def event(**overrides):
    return {"type":"amendment","source_page":PAGE,"source_url":FILE,
            "publication_date":"2026-10-13",
            "publication_evidence":"Дата размещения изменений в ЕИС: 13.10.2026",
            "decision_date":"2026-10-06",
            "decision_evidence":"Дата принятия решения о внесении изменений: 06.10.2026",
            "submission_end":"2026-10-30","document_name":"Изменения.pdf",**overrides}


def payload(changes=None,**overrides):
    result={"reg_number":REG,"source_page":PAGE,"published":"2026-09-01",
            "documents":[{"url":FILE,"name":"Изменения.pdf"}],**overrides}
    if changes is not None:result["changes"]=changes
    return result


def concurrent_writer(value,ready,release,finished,errors,hold_before_write=False):
    """Process-isolated instrumentation: pause A after its read, B at its lock."""
    try:
        original_write=intake.save_atomic
        original_lock=intake.fcntl.flock
        def write(path,data):
            if hold_before_write:
                ready.set()
                if not release.wait(10):raise TimeoutError("First writer was not released")
            return original_write(path,data)
        def lock(fd,operation):
            if not hold_before_write and operation==intake.fcntl.LOCK_EX:ready.set()
            return original_lock(fd,operation)
        intake.save_atomic=write
        intake.fcntl.flock=lock
        intake._save_manifest(intake._parse_manifest(value))
    except Exception as exc:errors.put(repr(exc))
    finally:finished.set()


class AmendmentHistoryIntakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="amendment-intake-")
        self.env=patch.dict(os.environ,{"EIS223_DATA_DIR":self.tmp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.tmp.cleanup)

    def save(self,value):
        parsed=intake._parse_manifest(value)
        intake._save_manifest(parsed)
        return json.loads(intake._manifest_path(REG).read_text())

    def test_positive_roundtrip_preserves_page_attachment_and_explicit_dates(self):
        saved=self.save(payload([event()]))
        change=saved["changes"][0]
        self.assertEqual(change["source_page"],PAGE)
        self.assertEqual(change["source_url"],FILE)
        self.assertEqual(change["decision_date"],"2026-10-06")
        self.assertEqual(intake._parse_manifest(saved)["changes"],saved["changes"])
        actual=amendments.manifest_changes(REG)
        self.assertEqual(len(actual),1)
        self.assertEqual(actual[0]["publication_date"],date(2026,10,13))
        self.assertEqual(actual[0]["decision_date"],date(2026,10,6))
        found,_=amendments.evaluation({"reg":REG,"method":"Конкурс","published":"2026-09-01"},[])
        self.assertTrue(any(x["code"]=="223-4-11-AMENDMENT-PUBLICATION-DELAY" for x in found))

    def test_revision_roundtrip_reader_and_exact_original_join_survive_doc_only_update(self):
        revision=event(type="revision")
        del revision["decision_date"]
        del revision["decision_evidence"]
        initial=self.save(payload([revision]))
        self.assertEqual(initial["changes"][0]["type"],"revision")
        before=amendments.manifest_changes(REG)
        self.assertEqual(len(before),1)
        self.assertEqual(before[0]["source"],PAGE)
        self.assertEqual(before[0]["attachment_source"],FILE)
        self.assertIsNone(before[0]["decision_date"])
        self.assertEqual(before[0]["publication_date"],date(2026,10,13))

        # Capture a different document and page without supplying any history.
        saved=self.save(payload(source_page=PAGE.replace("documents.html","common-info.html"),
            documents=[{"url":OTHER_FILE,"name":"Другая редакция.pdf"}]))
        self.assertEqual(saved["changes"],initial["changes"])
        self.assertEqual(amendments.manifest_changes(REG),before)

        text="Дата принятия решения о внесении изменений: 06.10.2026. Решение заказчика об изменении документации."
        source=FILE+"&download=1"
        original={"name":"Решение.pdf","text":text,"status":"checked","url":source,
                  "meta":{"source":"official_eis_original","source_url":source,
                          "sha256":hashlib.sha256(text.encode()).hexdigest()}}
        purchase={"reg":REG,"method":"Конкурс","published":"2026-09-01"}
        findings,_=amendments.evaluation(purchase,[original])
        self.assertEqual(len(findings),1)
        self.assertEqual(findings[0]["code"],"223-4-11-AMENDMENT-PUBLICATION-DELAY")
        self.assertEqual(findings[0]["source_url"],PAGE)
        self.assertIn(source,findings[0]["evidence"])
        self.assertIn("2026-10-06",findings[0]["evidence"])
        self.assertIn("2026-10-13",findings[0]["evidence"])

        # A shared filename or an unverified original cannot supply the date.
        other={**original,"url":OTHER_FILE,"meta":{**original["meta"],"source_url":OTHER_FILE}}
        for candidate in (other,{**original,"status":"unverified"},
                          {**original,"text":text+" Дата принятия решения о внесении изменений: 07.10.2026."}):
            with self.subTest(candidate=candidate["meta"]["source_url"],status=candidate["status"]):
                found,review=amendments.evaluation(purchase,[candidate])
                self.assertEqual(found,[])
                self.assertTrue(review)

    def test_later_document_only_intakes_retain_history_and_exact_duplicates(self):
        first=self.save(payload([event(),event()]))
        self.assertEqual(len(first["changes"]),1)
        second=self.save(payload(documents=[{"url":OTHER_FILE,"name":"Документация.pdf"}]))
        self.assertEqual(second["changes"],first["changes"])
        self.assertEqual(len(second["documents"]),2)
        third=self.save(payload([event()]))
        self.assertEqual(third["changes"],first["changes"])

    def test_history_survives_multiple_doc_only_notice_id_updates(self):
        old_page=PAGE.split("?")[0]+"?noticeId=123"
        landing=PAGE.replace("documents.html","common-info.html")
        original=self.save(payload([event(source_page=old_page)],source_page=old_page,landing_page=landing))
        for notice_id in (456,789):
            latest=PAGE.split("?")[0]+"?noticeId="+str(notice_id)
            saved=self.save(payload(source_page=latest,landing_page=landing))
            self.assertEqual(saved["changes"],original["changes"])
        self.assertEqual(len(amendments.manifest_changes(REG)),1)

    def test_page_only_explicit_history_and_date_alias_supported(self):
        change=event(publication_date="13.10.2026",published_at="2026-10-13T15:20:00+03:00")
        del change["source_url"]
        saved=self.save(payload([change]))
        self.assertEqual(saved["changes"][0]["publication_date"],"2026-10-13")

    def test_missing_decision_is_not_inferred_from_publication_or_document_date(self):
        change=event(document_date="2026-10-06")
        del change["decision_date"]
        del change["decision_evidence"]
        saved=self.save(payload([change]))
        self.assertNotIn("decision_date",saved["changes"][0])
        self.assertIsNone(amendments.manifest_changes(REG)[0]["decision_date"])
        found,review=amendments.evaluation({"reg":REG,"method":"Конкурс"},[])
        self.assertEqual(found,[])
        self.assertTrue(review)

    def test_normal_notice_publication_never_becomes_an_amendment(self):
        self.assertEqual(self.save(payload())["changes"],[])
        change=event()
        del change["publication_date"]
        with self.assertRaises(ValueError):intake._parse_manifest(payload([change]))
        for kind in ("notice publication","unchanged","no amendment","Размещение извещения"):
            with self.subTest(kind=kind),self.assertRaises(ValueError):
                intake._parse_manifest(payload([event(type=kind)]))

    def test_cross_purchase_and_unsafe_page_urls_rejected(self):
        for page in (PAGE.replace(REG,OTHER_REG),PAGE+"&regNumber="+OTHER_REG,
                     PAGE+"&purchaseNoticeNumber="+REG,PAGE+"&noticeId=999",
                     PAGE.replace("https:","http:"),PAGE.replace("zakupki.gov.ru","zakupki.gov.ru.evil.test"),
                     PAGE.replace("zakupki.gov.ru","name:secret@zakupki.gov.ru"),
                     PAGE.replace("zakupki.gov.ru","zakupki.gov.ru:444"),PAGE+"#history",
                     BASE+"/arbitrary.html?regNumber="+REG,PAGE.split("?")[0],
                     PAGE+"&regNumber=",PAGE+"\n"):
            with self.subTest(page=page),self.assertRaises(ValueError):
                intake._parse_manifest(payload([event(source_page=page)]))

    def test_notice_identity_must_match_established_notice_namespace(self):
        for key in ("noticeId","noticeInfoId"):
            page=PAGE.split("?")[0]+"?"+key+"=123"
            landing=PAGE.replace("documents.html","common-info.html")
            parsed=intake._parse_manifest(payload([event(source_page=page)],source_page=page,landing_page=landing))
            self.assertEqual(parsed["changes"][0]["source_page"],page)
            for bad in (page.replace("123","999"),page.replace(key,"noticeInfoId" if key=="noticeId" else "noticeId"),
                        page+"&"+key+"=123",page+"&regNumber="+OTHER_REG):
                with self.subTest(page=bad),self.assertRaises(ValueError):
                    intake._parse_manifest(payload([event(source_page=bad)],source_page=page,landing_page=landing))

    def test_unbound_attachments_credentials_and_hidden_purchase_id_rejected(self):
        for url in (OTHER_FILE,BASE+"/unbound.pdf",FILE.replace("https:","http:"),
                    FILE.replace("zakupki.gov.ru","user:password@zakupki.gov.ru"),
                    FILE+"&regNumber="+OTHER_REG,FILE+"#fragment"):
            with self.subTest(url=url),self.assertRaises(ValueError):
                intake._parse_manifest(payload([event(source_url=url)]))
        with self.assertRaises(ValueError):
            intake._parse_manifest(payload([event(source_page="")]))

    def test_caller_provenance_cannot_override_real_manifest_context(self):
        fake_page=PAGE.split("?")[0]+"?noticeId=999"
        with self.assertRaises(ValueError):
            intake._parse_manifest(payload([event(source_page=fake_page,
                provenance={"source_page":fake_page,"landing_page":PAGE})]))

    def test_dates_evidence_and_arrays_are_strictly_typed_and_bounded(self):
        invalid=[{"publication_date":True},{"publication_date":20261013},{"publication_date":"2026-02-30"},
                 {"publication_date":"published 2026-10-13"},{"publication_date":"2026-10-13 garbage"},
                 {"published_at":"2026-10-14"},{"decision_date":"not known"},
                 {"decision_evidence":""},{"decision_evidence":{"date":"2026-10-06"}},
                 {"publication_evidence":None},{"publication_evidence":["2026-10-13"]},
                 {"publication_evidence":"x"*1001},{"type":["amendment"]},
                 {"source_page":PAGE+"x"*1000},{"event_id":"x"*81},
                 {"submission_end":"2026-99-01"}]
        for override in invalid:
            with self.subTest(override=override),self.assertRaises(ValueError):
                intake._parse_manifest(payload([event(**override)]))
        for value in (None,{},"changes",[None],[event()]*81):
            value_payload=payload();value_payload["changes"]=value
            with self.subTest(value=type(value)),self.assertRaises(ValueError):
                intake._parse_manifest(value_payload)

    def test_conflicting_versions_fail_before_overwriting_saved_evidence(self):
        self.save(payload([event(event_id="amendment-1")]))
        path=intake._manifest_path(REG)
        before=path.read_bytes()
        for overrides in ({"publication_date":"2026-10-14"},{"decision_date":"2026-10-07"},
                          {"decision_evidence":"Other evidence"},{"document_name":"Other version.pdf"}):
            with self.subTest(overrides=overrides),self.assertRaises(intake.AmendmentHistoryConflict):
                self.save(payload([event(event_id="amendment-1",**overrides)]))
            self.assertEqual(path.read_bytes(),before)
        with self.assertRaises(intake.AmendmentHistoryConflict):
            intake._parse_manifest(payload([event(),event(publication_date="2026-10-14")]))

    def test_distinct_stable_events_retained_and_ambiguous_same_day_rejected(self):
        first=event(event_id="1")
        second=event(event_id="2",source_url=OTHER_FILE)
        docs=payload()["documents"]+[{"url":OTHER_FILE,"name":"Изменения 2.pdf"}]
        saved=self.save(payload([first,second],documents=docs))
        self.assertEqual(len(saved["changes"]),2)
        first.pop("source_url");first.pop("event_id")
        second.pop("source_url");second.pop("event_id");second["document_name"]="Different revision.pdf"
        with self.assertRaises(intake.AmendmentHistoryConflict):
            intake._parse_manifest(payload([first,second]))

    def test_cumulative_limits_do_not_truncate_previous_history(self):
        events=[event(event_id=str(i),publication_evidence="x"*700) for i in range(30)]
        for change in events:change.pop("source_url")
        with self.assertRaises(intake.AmendmentHistoryConflict):
            intake._parse_manifest(payload(events))
        base=event();base.pop("source_url")
        events=[dict(base,event_id=str(i)) for i in range(60)]
        # The byte budget is checked after merging, as well as per request.
        first=self.save(payload(events[:30]));path=intake._manifest_path(REG);before=path.read_bytes()
        with self.assertRaises(intake.AmendmentHistoryConflict):
            self.save(payload(events[30:]))
        self.assertEqual(path.read_bytes(),before)
        self.assertEqual(len(first["changes"]),30)

    def test_concurrent_process_intakes_merge_without_losing_documents_or_events(self):
        self.save(payload([event(event_id="A")]))
        context=multiprocessing.get_context("fork")
        first_ready=context.Event();second_ready=context.Event();release=context.Event()
        first_done=context.Event();second_done=context.Event();errors=context.Queue()
        first_payload=payload([event(event_id="B",source_url=OTHER_FILE)],
            documents=[{"url":OTHER_FILE,"name":"Изменения B.pdf"}])
        third_file=FILE[:-32]+"C"*32
        second_payload=payload([event(event_id="C",source_url=third_file)],
            documents=[{"url":third_file,"name":"Изменения C.pdf"}])
        first=context.Process(target=concurrent_writer,
            args=(first_payload,first_ready,release,first_done,errors,True))
        second=context.Process(target=concurrent_writer,
            args=(second_payload,second_ready,release,second_done,errors))
        try:
            first.start()
            self.assertTrue(first_ready.wait(5),"First writer did not reach its pre-replace pause")
            second.start()
            self.assertTrue(second_ready.wait(5),"Second writer did not attempt the lock")
            self.assertFalse(second_done.wait(0.1),"Second writer bypassed the held manifest lock")
            release.set()
            first.join(5);second.join(5)
            self.assertEqual(first.exitcode,0);self.assertEqual(second.exitcode,0)
            self.assertTrue(errors.empty())
        finally:
            release.set()
            for process in (first,second):
                if process.pid is not None:
                    process.join(1)
                    if process.is_alive():process.terminate();process.join()
            errors.close();errors.join_thread()
        saved=json.loads(intake._manifest_path(REG).read_text())
        self.assertEqual({x["event_id"] for x in saved["changes"]},{"A","B","C"})
        self.assertEqual({x["url"] for x in saved["documents"]},{FILE,OTHER_FILE,third_file})

    def test_api_returns_validation_and_conflict_status_without_overwrite(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app=FastAPI();intake.install({"app":app})
        headers={"origin":BASE,"x-eis223-upload-token":"synthetic-test-token"}
        with patch.dict(os.environ,{"EIS223_UPLOAD_TOKEN":"synthetic-test-token"}), \
             patch.object(intake,"_apply_customer",return_value=False),TestClient(app) as client:
            endpoint="/api/v299/eis223/public-manifest"
            self.assertEqual(client.post(endpoint,json=payload([event()]),headers=headers).status_code,200)
            before=intake._manifest_path(REG).read_bytes()
            response=client.post(endpoint,json=payload([event(decision_date="2026-10-07")]),headers=headers)
            self.assertEqual(response.status_code,409)
            self.assertIn("previous evidence was not replaced",response.json()["detail"])
            self.assertEqual(intake._manifest_path(REG).read_bytes(),before)
            self.assertEqual(client.post(endpoint,json=payload([event(reg_number=OTHER_REG)]),headers=headers).status_code,422)
            self.assertEqual(client.post(endpoint,json=payload([event()]),headers={"origin":BASE}).status_code,401)

    def test_corrupt_or_untrusted_retained_history_is_not_silently_replaced(self):
        self.save(payload([event()]))
        path=intake._manifest_path(REG)
        original=json.loads(path.read_text())
        original["changes"][0]["source_page"]=PAGE.replace(REG,OTHER_REG)
        path.write_text(json.dumps(original))
        before=path.read_bytes()
        with self.assertRaises(intake.AmendmentHistoryConflict):self.save(payload())
        self.assertEqual(path.read_bytes(),before)


if __name__=="__main__":unittest.main()
