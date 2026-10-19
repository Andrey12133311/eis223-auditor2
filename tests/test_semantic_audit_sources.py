"""Local fixture evidence only; no remote services or production writes."""
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
from xml.sax.saxutils import escape
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import audit_source_version as source
import eis223_soap_xml
import v295_eis_originals as originals

REG = "32616233661"
UID = "00000000000000000000000000000001"
URL = "https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid=" + UID
CARD = "https://zakupki.gov.ru/223/purchase/public/purchase/info/common-info.html?regNumber=" + REG


class SemanticSources(unittest.TestCase):
    def setUp(self):
        source.clear_semantic_cache()
        self.addCleanup(source.clear_semantic_cache)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        env = patch.dict(os.environ, {"EIS223_DATA_DIR": str(self.root)})
        env.start(); self.addCleanup(env.stop)
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.executescript('''
            CREATE TABLE docs(id INTEGER PRIMARY KEY,reg_number TEXT,name TEXT,url TEXT,
                doc_type TEXT,published_at TEXT,local_path TEXT,status TEXT,
                metadata_json TEXT,final_url TEXT,text_chars INTEGER,checked_at TEXT);
            CREATE TABLE purchases(reg_number TEXT PRIMARY KEY,title TEXT,customer TEXT,
                method TEXT,published_at TEXT,submission_start TEXT,submission_end TEXT,
                deadline TEXT,price TEXT,price_num REAL,url TEXT,source TEXT,
                card_fields_json TEXT,violations INTEGER,checks_ok INTEGER,last_seen TEXT);
            CREATE TABLE v341_card_facts(reg TEXT PRIMARY KEY,source_page TEXT,captured_at TEXT,payload TEXT);
            CREATE TABLE v338_card_price(reg TEXT PRIMARY KEY,source_page TEXT,captured_at TEXT,
                price TEXT,price_num REAL,price_kind TEXT,price_label TEXT,conflict INTEGER,alternative_price TEXT);
        ''')
        self.db.execute("INSERT INTO purchases(reg_number,url,source,method,card_fields_json) VALUES(?,?,?,?,?)",
                        (REG,CARD,"official_eis_card","Открытый конкурс","{}"))
        self.db.commit()
        self.entry = {"uid": UID, "name": "Документация.pdf", "url": URL, "ext": ".pdf"}
        self.blob = b"%PDF-1.7\nOriginal source version one"
        self.store()

    def store(self, blob=None):
        originals._store_original_version(REG, UID, self.entry, blob or self.blob)
        self.path, self.receipt = originals.saved_document(REG, UID)

    def version(self):
        self.db.commit()
        return source.semantic_source_version(self.db, REG)

    def add_doc(self, **overrides):
        values = dict(reg_number=REG,name=self.entry["name"],url=URL,doc_type="documentation",
                      published_at="2026-10-01",local_path=str(self.path),status="checked",
                      metadata_json=json.dumps({"source":"official_eis_original", "uid":UID,
                        "source_url":URL,"sha256":self.receipt["sha256"]}),text_chars=20)
        values.update(overrides)
        self.db.execute("INSERT INTO docs("+",".join(values)+") VALUES("+",".join("?" for _ in values)+")",tuple(values.values()))

    def card(self, **overrides):
        payload = {"source_page": CARD,"fields":{"method":"Открытый конкурс", "published":"2026-10-01",
            "submission_end":"2026-10-30"},"prices":[],"captured_at":"first",
            "field_evidence":{"method":"Способ закупки: Открытый конкурс"}}
        payload.update(overrides)
        self.db.execute("INSERT OR REPLACE INTO v341_card_facts VALUES(?,?,?,?)",(REG,CARD,"first",json.dumps(payload)))
        return payload

    def manifest(self, **overrides):
        payload = {"source":"public_eis_223_browser", "reg_number":REG,"source_page":CARD,
                   "documents":[self.entry]}
        payload.update(overrides)
        path = self.root / "eis223_public_browser_intake" / REG / "manifest.json"
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(payload))
        return path, payload

    def soap(self, publication="2026-10-12T12:00:00+03:00", version=1, compression=zipfile.ZIP_STORED,
             repeats=1, padding=0, filename=None):
        kind = "purchaseProtocol"
        name = filename or f"{kind}_{REG}_{version}.xml"
        raw = (f'<{kind} xmlns="{eis223_soap_xml.PURCHASE_NS}"><{kind}Data>'
               f'<purchaseInfo><purchaseNoticeNumber>{REG}</purchaseNoticeNumber></purchaseInfo>'
               f'<guid>protocol-1</guid><version>{version}</version><publicationDateTime>{publication}</publicationDateTime>'
               f'<padding>{"A"*padding}</padding>'
               f'<attachments><document><fileName>Протокол.pdf</fileName><url>{escape(URL)}</url>'
               f'</document></attachments></{kind}Data></{kind}>').encode()
        record, _ = eis223_soap_xml.parse_document(raw, name)
        stream = io.BytesIO()
        with zipfile.ZipFile(stream,"w",compression=compression) as archive:
            archive.writestr(name,raw)
        blob = stream.getvalue(); digest=hashlib.sha256(blob).hexdigest()
        folder=self.root/"eis223_official_archive_inbox"/REG
        folder.mkdir(parents=True,exist_ok=True)
        path=folder/(digest+".zip");path.write_bytes(blob)
        receipt=folder/(digest+".json")
        receipt.write_text(json.dumps({"source":"official_eis_soap_archive","reg_number":REG,
            "archive_sha256":digest,"notices":[record]*repeats}))
        return path, receipt

    def test_current_original_change_without_doc_reconciliation(self):
        before=self.version(); self.store(self.blob+b" next")
        self.assertNotEqual(self.version(),before)

    def test_identical_bytes_with_touched_stat_do_not_recheck(self):
        before=self.version(); os.utime(self.path, ns=(1,2))
        self.assertEqual(self.version(),before)
        self.store(); self.assertEqual(self.version(),before)

    def test_corrupted_and_repaired_same_length_original(self):
        before=self.version(); self.path.write_bytes(b"x"*len(self.blob))
        corrupt=self.version(); self.assertNotEqual(corrupt,before)
        self.assertEqual(corrupt,self.version())
        self.path.write_bytes(self.blob)
        self.assertEqual(self.version(),before)

    def test_missing_and_restored_original(self):
        before=self.version(); self.path.unlink(); missing=self.version()
        self.assertNotEqual(before,missing)
        self.path.write_bytes(self.blob); self.assertEqual(self.version(),before)

    def test_operational_persistence_has_fixed_point(self):
        self.add_doc(); before=self.version()
        meta=json.loads(self.db.execute("SELECT metadata_json FROM docs").fetchone()[0])
        meta.update(coverage={"documents":45},warnings=["new"],text_chars=5000,
                    official_publication_evidence={"state":"confirmed","publication_date":"2030-01-01"})
        self.db.execute("UPDATE docs SET checked_at='later',status='downloaded_no_text',text_chars=9000,metadata_json=?",(json.dumps(meta),))
        self.db.execute("UPDATE purchases SET violations=80,checks_ok=90,last_seen='later',card_fields_json=?",(json.dumps({"coverage":{"complete":True},"summary":{"method":"derived"},"fact_evidence":{"sme_scope":{"state":"yes"},"published":{"source":"official_eis_document","value":"2040-01-01"}}}),))
        receipt_path=self.path.parent/"receipt.json"
        value=json.loads(receipt_path.read_text()); value.update(extracted_chars=9000,state="reviewed",checked_at="later")
        receipt_path.write_text(json.dumps(value))
        for _ in range(3): self.assertEqual(self.version(),before)

    def test_unverified_docs_only_rows_have_no_effect(self):
        before=self.version()
        self.add_doc(local_path="/tmp/untrusted.pdf",metadata_json=json.dumps({"sha256":"a"*64,"source":"manual"}))
        self.add_doc(local_path="/tmp/untrusted.pdf",metadata_json="not json")
        self.assertEqual(self.version(),before)

    def test_derived_archive_member_rows_are_not_independent_evidence(self):
        before=self.version()
        self.add_doc(local_path="/tmp/invented-member.pdf",metadata_json=json.dumps({
            "source":"official_eis_archive_member","source_url":URL,
            "archive_sha256":self.receipt["sha256"],"sha256":"a"*64,
            "archive_member":"claimed member.pdf"}))
        self.assertEqual(self.version(),before)

    def test_receiptless_claim_of_verification_is_ignored(self):
        before=self.version(); uid="2"*32
        self.add_doc(url=URL.replace(UID,uid),metadata_json=json.dumps({"source":"official_eis_original",
            "source_url":URL.replace(UID,uid),"sha256":"a"*64}))
        self.assertEqual(self.version(),before)

    def test_authenticated_source_role_and_date_changes(self):
        self.add_doc(); before=self.version()
        self.db.execute("UPDATE docs SET doc_type='protocol'")
        role=self.version(); self.assertNotEqual(role,before)
        self.db.execute("UPDATE docs SET published_at='2026-10-02'")
        self.assertNotEqual(self.version(),role)

    def test_current_eis_method_publication_deadline_and_price_changes(self):
        for column,value in (("method","Запрос котировок"),("published_at","2026-10-02"),
                ("submission_end","2026-10-29"),("deadline","2026-10-31"),("price_num",1000000)):
            before=self.version();self.db.execute("UPDATE purchases SET "+column+"=?",(value,))
            self.assertNotEqual(self.version(),before,column)

    def test_card_facts_revision_and_replayed_capture(self):
        payload=self.card();before=self.version()
        payload.update(captured_at="later",checked_at="later",coverage={"rows":99},warnings=["new"])
        self.db.execute("UPDATE v341_card_facts SET captured_at='later',payload=?",(json.dumps(payload),))
        self.assertEqual(self.version(),before)
        for key,value in (("method","Запрос котировок"),("published","2026-10-03"),("submission_end","2026-10-21")):
            before=self.version();payload["fields"][key]=value
            self.db.execute("UPDATE v341_card_facts SET payload=?",(json.dumps(payload),))
            self.assertNotEqual(self.version(),before,key)

    def test_direct_card_persistence_does_not_recheck(self):
        payload=self.card();before=self.version()
        card={"fact_evidence":{key:{"value":value,"source":"official_eis_card","source_url":CARD,"captured_at":"later"}
                              for key,value in payload["fields"].items()}}
        self.db.execute("UPDATE purchases SET method=?,published_at=?,submission_end=?,deadline=?,card_fields_json=?",
            (payload["fields"]["method"],payload["fields"]["published"],payload["fields"]["submission_end"],payload["fields"]["submission_end"],json.dumps(card)))
        self.assertEqual(self.version(),before)

    def test_card_price_capture_and_meaning_conflict_changes(self):
        self.db.execute("INSERT INTO v338_card_price VALUES(?,?,?,?,?,?,?,?,?)",(REG,CARD,"first","100000",100000,"nmcd","НМЦД",0,""))
        before=self.version(); self.db.execute("UPDATE v338_card_price SET captured_at='later'")
        self.assertEqual(self.version(),before)
        for key,value in (("price_num",200000),("price_kind","subject_cost"),("conflict",1)):
            before=self.version();self.db.execute("UPDATE v338_card_price SET "+key+"=?",(value,))
            self.assertNotEqual(self.version(),before)

    def test_nonofficial_card_rows_are_not_evidence(self):
        before=self.version();self.card(source_page="https://example.org/")
        self.assertEqual(self.version(),before)

    def test_saved_authoritative_card_fallback_changes(self):
        before=self.version()
        self.db.execute("UPDATE purchases SET card_fields_json=?",(json.dumps({"fact_evidence":{"sme_only":{
            "source":"official_eis_card","source_url":CARD,"value":True}}}),))
        self.assertNotEqual(self.version(),before)

    def test_manifest_replay_order_and_operational_values(self):
        path,payload=self.manifest();before=self.version()
        payload.update(captured_at="later",checked_at="later",warnings=["changed"],coverage={"rows":60})
        payload["documents"][0]["checked_at"]="later"
        path.write_text(json.dumps(payload));self.assertEqual(self.version(),before)
        payload["changes"]=[{"type":"Изменение","version":2,"publication_date":"2026-10-03","source_url":URL}]
        path.write_text(json.dumps(payload));self.assertNotEqual(self.version(),before)

    def test_verified_xml_publication_and_revision_changes(self):
        before=self.version();self.soap();published=self.version();self.assertNotEqual(before,published)
        self.soap(version=2);revision=self.version();self.assertNotEqual(published,revision)
        self.soap(publication="2026-10-14T12:00:00+03:00",version=3)
        self.assertNotEqual(revision,self.version())

    def test_soap_repack_and_receipt_capture_do_not_recheck(self):
        _,receipt=self.soap();before=self.version()
        value=json.loads(receipt.read_text());value.update(captured_at="later",warnings=["different"])
        receipt.write_text(json.dumps(value));self.assertEqual(before,self.version())
        self.soap(compression=zipfile.ZIP_DEFLATED);self.assertEqual(before,self.version())

    def test_unverified_publication_annotation_is_ignored(self):
        self.add_doc();before=self.version()
        value=json.loads(self.db.execute("SELECT metadata_json FROM docs").fetchone()[0])
        value.update(official_publication_evidence={"state":"confirmed","publication_date":"2026-12-31"})
        self.db.execute("UPDATE docs SET metadata_json=?",(json.dumps(value),))
        self.assertEqual(before,self.version())

    def test_snapshot_diagnostic_is_bounded_and_does_not_include_details(self):
        error=source.SemanticSnapshotError("byte_budget", "/private/path")
        self.assertEqual(error.diagnostic_code,"source_snapshot_byte_budget")
        self.assertEqual(source.SemanticSnapshotError("bad reason\n/private").diagnostic_code,
                         "source_snapshot_unknown")
        self.assertEqual(source.SemanticSnapshotError("x"*49).diagnostic_code,
                         "source_snapshot_unknown")

    def test_budget_exhaustion_and_read_failure_are_explicit(self):
        with patch.object(source,"SEMANTIC_MAX_BYTES",2):
            with self.assertRaisesRegex(source.SemanticSnapshotError,"byte_budget"):self.version()
        with patch.object(source.os,"open",side_effect=PermissionError(13,"denied")):
            with self.assertRaisesRegex(source.SemanticSnapshotError,"file_unreadable"):self.version()

    def test_large_original_set_resumes_across_bounded_passes(self):
        for index in range(2,5):
            uid=f"{index:032X}"
            entry={**self.entry,"uid":uid,"url":URL.replace(UID,uid)}
            originals._store_original_version(REG,uid,entry,b"%PDF-1.7\n"+bytes([index])*8192)
        errors=0
        with patch.object(source,"SEMANTIC_MAX_BYTES",11000):
            for attempt in range(8):
                try:
                    completed=self.version(); break
                except source.SemanticSnapshotError as exc:
                    self.assertEqual(exc.reason,"byte_budget");errors+=1
            else:self.fail("Stable legal sources never completed after partial passes")
        self.assertGreater(errors,0)
        source.clear_semantic_cache()
        self.assertEqual(completed,self.version())

    def test_partial_progress_survives_interleaved_procurement_cache_churn(self):
        other_reg="32616233662"
        for reg in (REG,other_reg):
            for index in range(1,4):
                uid=f"{index:032X}"
                entry={**self.entry,"uid":uid,"url":URL.replace(UID,uid)}
                originals._store_original_version(reg,uid,entry,b"%PDF-1.7\n"+bytes([index])*8192)
        source.clear_semantic_cache()
        self.db.commit()
        with patch.object(source,"SEMANTIC_CACHE_ENTRIES",3), patch.object(source,"SEMANTIC_MAX_BYTES",11000):
            for attempt in range(10):
                try:
                    completed=source.semantic_source_version(self.db,REG);break
                except source.SemanticSnapshotError as exc:
                    self.assertEqual(exc.reason,"byte_budget")
                try:source.semantic_source_version(self.db,other_reg)
                except source.SemanticSnapshotError as exc:self.assertEqual(exc.reason,"byte_budget")
            else:self.fail("Interleaved procurements repeatedly evicted partial progress")
            self.assertIsNone(source._PROTECTED_SOURCE)
            for attempt in range(8):
                try:
                    second=source.semantic_source_version(self.db,other_reg);break
                except source.SemanticSnapshotError as exc:self.assertEqual(exc.reason,"byte_budget")
            else:self.fail("The next partial source could not progress after protection released")
        self.assertEqual(len(second),64)
        self.assertIsNone(source._PROTECTED_SOURCE)
        self.assertLessEqual(len(source._HASH_CACHE),3)
        self.assertLessEqual(len(source._JSON_CACHE),3)
        source.clear_semantic_cache()
        self.assertEqual(completed,self.version())

    def test_partial_source_exceeding_cache_capacity_fails_explicitly(self):
        for index in range(1,5):
            uid=f"{index:032X}"
            originals._store_original_version(REG,uid,{**self.entry,"uid":uid,"url":URL.replace(UID,uid)},
                                              b"%PDF-1.7\n"+bytes([index])*8192)
        source.clear_semantic_cache()
        with patch.object(source,"SEMANTIC_CACHE_ENTRIES",3), patch.object(source,"SEMANTIC_MAX_BYTES",11000):
            for attempt in range(8):
                try:self.version()
                except source.SemanticSnapshotError as exc:
                    if exc.reason=="resume_capacity":break
                    self.assertEqual(exc.reason,"byte_budget")
                else:self.fail("An unsupported snapshot was falsely acknowledged")
            else:self.fail("Unsupported protected metadata repeatedly restarted")
        self.assertIsNone(source._PROTECTED_SOURCE)
        self.assertLessEqual(len(source._HASH_CACHE),3)
        self.assertLessEqual(len(source._JSON_CACHE),3)

    def test_rewritten_receipt_retires_only_obsolete_physical_key_and_still_resumes(self):
        for index in range(2,4):
            uid=f"{index:032X}"
            originals._store_original_version(REG,uid,{**self.entry,'uid':uid,'url':URL.replace(UID,uid)},
                                              b'%PDF-1.7\n'+bytes([index])*8192)
        receipt=self.path.parent/'receipt.json'
        with patch.object(source,'SEMANTIC_CACHE_ENTRIES',3), patch.object(source,'SEMANTIC_MAX_BYTES',11000):
            with self.assertRaisesRegex(source.SemanticSnapshotError,'byte_budget'):self.version()
            old_keys={key for key in source._JSON_CACHE if key[0]==str(receipt)}
            self.assertEqual(len(old_keys),1)
            value=json.loads(receipt.read_text());value['checked_at']='operational rewrite'
            receipt.write_text(json.dumps(value))
            for _ in range(5):
                try:completed=self.version();break
                except source.SemanticSnapshotError as exc:self.assertEqual(exc.reason,'byte_budget')
            else:self.fail('Current source fits cache but obsolete receipt prevents completion')
            current_keys={key for key in source._JSON_CACHE if key[0]==str(receipt)}
            self.assertEqual(len(current_keys),1)
            self.assertFalse(old_keys & current_keys)
            self.assertLessEqual(len(source._JSON_CACHE),3)
        source.clear_semantic_cache()
        self.assertEqual(completed,self.version())

    def test_stale_cache_writer_cannot_evict_or_reinsert_over_current_identity(self):
        old_info=self.path.stat();old=(str(self.path),source._physical_identity(old_info),self.receipt['sha256'])
        self.path.write_bytes(b'changed content')
        current=(str(self.path),source._physical_identity(self.path.stat()),self.receipt['sha256'])
        value=(hashlib.sha256(self.path.read_bytes()).hexdigest(),self.path.stat().st_size,None)
        source._cache_set(source._HASH_CACHE,current,value)
        with self.assertRaisesRegex(source.SemanticSnapshotError,'concurrent_file_change'):
            source._cache_set(source._HASH_CACHE,old,('stale',old_info.st_size,None))
        self.assertEqual(dict(source._HASH_CACHE),{current:value})

    def test_cache_retirement_keeps_current_receipt_views_and_distinct_physical_paths(self):
        path,_=self.soap()
        identity=source._physical_identity(path.stat())
        first=(str(path),identity,path.stem,'receipt-view-one')
        second=(str(path),identity,path.stem,'receipt-view-two')
        source._cache_set(source._XML_CACHE,first,([],2))
        source._cache_set(source._XML_CACHE,second,([],2))
        self.assertEqual(set(source._XML_CACHE),{first,second})
        copy=path.with_name('different-physical-path.zip');copy.write_bytes(path.read_bytes())
        third=(str(copy),source._physical_identity(copy.stat()),path.stem,'receipt-view-one')
        source._cache_set(source._XML_CACHE,third,([],2))
        self.assertEqual(set(source._XML_CACHE),{first,second,third})
        path.write_bytes(b'changed')
        current=(str(path),source._physical_identity(path.stat()),path.stem,'receipt-view-one')
        source._cache_set(source._XML_CACHE,current,([],2))
        self.assertEqual(set(source._XML_CACHE),{current,third})

    def test_single_file_limits_remain_explicit_and_do_not_create_partial_hash_trust(self):
        self.store(self.blob+bytes(10000))
        with patch.object(source,'SEMANTIC_MAX_BYTES',5000):
            for _ in range(3):
                with self.assertRaisesRegex(source.SemanticSnapshotError,'byte_budget'):self.version()
                self.assertEqual(len(source._HASH_CACHE),0)
        # Default accepted original size is smaller than the unchanged pass cap.
        self.assertLess(25*1024*1024,source.SEMANTIC_MAX_BYTES)
        with self.path.open('wb') as stream:stream.truncate(25*1024*1024+1)
        with self.assertRaisesRegex(source.SemanticSnapshotError,'file_size'):self.version()
        self.assertEqual(len(source._HASH_CACHE),0)

    def test_non_budget_failure_releases_partial_progress_protection(self):
        self.store(self.blob+bytes(10000))
        with patch.object(source,"SEMANTIC_MAX_BYTES",5000):
            with self.assertRaisesRegex(source.SemanticSnapshotError,"byte_budget"):self.version()
        self.assertIsNotNone(source._PROTECTED_SOURCE)
        (self.path.parent/"receipt.json").write_text("invalid receipt")
        with self.assertRaisesRegex(source.SemanticSnapshotError,"invalid_metadata"):self.version()
        self.assertIsNone(source._PROTECTED_SOURCE)

    def test_abandoned_partial_protection_expires_after_six_hours(self):
        self.store(self.blob+bytes(10000))
        other_reg="32616233662"
        originals._store_original_version(other_reg,UID,self.entry,self.blob+bytes(10000))
        with patch.object(source,"SEMANTIC_MAX_BYTES",5000):
            with patch.object(source,"_progress_time",return_value=0):
                with self.assertRaisesRegex(source.SemanticSnapshotError,"byte_budget"):self.version()
            self.assertEqual(source._PROTECTED_SOURCE[1],REG)
            with patch.object(source,"_progress_time",return_value=source.SEMANTIC_PROGRESS_LEASE_SECONDS+1):
                with self.assertRaisesRegex(source.SemanticSnapshotError,"byte_budget"):
                    source.semantic_source_version(self.db,other_reg)
            self.assertEqual(source._PROTECTED_SOURCE[1],other_reg)
        self.assertEqual(source.SEMANTIC_PROGRESS_LEASE_SECONDS,6*60*60)

    def test_periodic_partial_revisit_renews_inactivity_lease(self):
        self.store(self.blob+bytes(10000))
        lease=source.SEMANTIC_PROGRESS_LEASE_SECONDS
        with patch.object(source,"SEMANTIC_MAX_BYTES",5000):
            for stamp in (0,lease-1,2*lease-2):
                with patch.object(source,"_progress_time",return_value=stamp):
                    with self.assertRaisesRegex(source.SemanticSnapshotError,"byte_budget"):self.version()
                self.assertEqual(source._PROTECTED_SOURCE[1],REG)
                self.assertEqual(source._PROTECTED_LAST_ACTIVE,stamp)
        source.clear_semantic_cache()
        self.assertIsNone(source._PROTECTED_SOURCE)
        self.assertEqual(source._PROTECTED_LAST_ACTIVE,0)

    def test_xml_member_budget_error_keeps_typed_reason(self):
        path,_=self.soap()
        with zipfile.ZipFile(path) as archive:member_size=archive.infolist()[0].file_size
        original=source._SemanticIO.charge
        def charge(reader,size=0):
            if size==member_size:raise source.SemanticSnapshotError("byte_budget")
            return original(reader,size)
        with patch.object(source._SemanticIO,"charge",charge):
            with self.assertRaises(source.SemanticSnapshotError) as raised:self.version()
        self.assertEqual(raised.exception.reason,"byte_budget")

    def test_cached_original_corruption_is_detected_with_restored_mtime(self):
        before=self.version();stat=self.path.stat()
        self.path.write_bytes(b"x"*len(self.blob))
        os.utime(self.path,ns=(stat.st_atime_ns,stat.st_mtime_ns))
        self.assertNotEqual(before,self.version())
        self.path.write_bytes(self.blob)
        os.utime(self.path,ns=(stat.st_atime_ns,stat.st_mtime_ns))
        self.assertEqual(before,self.version())

    def test_large_xml_history_resumes_across_bounded_passes(self):
        for version in range(1,5):self.soap(version=version)
        source.clear_semantic_cache()
        errors=0
        with patch.object(source,"SEMANTIC_MAX_BYTES",4500):
            for attempt in range(8):
                try:
                    completed=self.version();break
                except source.SemanticSnapshotError as exc:
                    self.assertEqual(exc.reason,"byte_budget");errors+=1
            else:self.fail("XML history never completed after partial passes")
        self.assertGreater(errors,0)
        source.clear_semantic_cache()
        self.assertEqual(completed,self.version())

    def test_cached_xml_corruption_and_repair_changes_source(self):
        path,_=self.soap();raw=path.read_bytes();before=self.version();stat=path.stat()
        path.write_bytes(b"x"*len(raw));os.utime(path,ns=(stat.st_atime_ns,stat.st_mtime_ns))
        corrupt=self.version();self.assertNotEqual(before,corrupt)
        path.write_bytes(raw);os.utime(path,ns=(stat.st_atime_ns,stat.st_mtime_ns))
        self.assertEqual(before,self.version())

    def test_repeated_xml_references_reuse_only_verified_member_with_default_budgets(self):
        path,receipt=self.soap(compression=zipfile.ZIP_DEFLATED,repeats=100,padding=3*1024*1024)
        with zipfile.ZipFile(path) as archive:
            self.assertGreater(archive.infolist()[0].file_size*100,source.SEMANTIC_MAX_BYTES)
        with patch.object(eis223_soap_xml,'parse_document',wraps=eis223_soap_xml.parse_document) as parse:
            repeated=self.version()
        parse.assert_called_once()
        self.assertEqual(len(source._XML_CACHE),1)
        value=json.loads(receipt.read_text());value['notices']=value['notices'][:1]
        receipt.write_text(json.dumps(value));source.clear_semantic_cache()
        self.assertEqual(repeated,self.version())

    def test_repeated_xml_reference_with_conflicting_hash_is_not_silently_skipped(self):
        _,receipt=self.soap(repeats=2)
        value=json.loads(receipt.read_text());value['notices'][1]['xml_sha256']='f'*64
        for reverse in (False,True):
            if reverse:value['notices'].reverse()
            receipt.write_text(json.dumps(value));source.clear_semantic_cache()
            with self.assertRaisesRegex(source.SemanticSnapshotError,'xml_receipt_hash_mismatch'):
                self.version()
            self.assertEqual(len(source._XML_CACHE),0)

    def test_repeated_reference_cannot_promote_malformed_authenticated_xml(self):
        folder=self.root/'eis223_official_archive_inbox'/REG;folder.mkdir(parents=True)
        name=f'purchaseProtocol_{REG}_malformed.xml';raw=b'<broken>'
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as archive:archive.writestr(name,raw)
        blob=stream.getvalue();digest=hashlib.sha256(blob).hexdigest()
        (folder/(digest+'.zip')).write_bytes(blob)
        (folder/(digest+'.json')).write_text(json.dumps({
            'source':'official_eis_soap_archive','reg_number':REG,'archive_sha256':digest,
            'notices':[{'reg_number':REG,'xml_filename':name,'xml_sha256':hashlib.sha256(raw).hexdigest()}]*100}))
        with self.assertRaisesRegex(source.SemanticSnapshotError,'xml_unreadable'):
            self.version()
        self.assertEqual(len(source._XML_CACHE),0)

    def test_same_member_name_in_distinct_archives_preserves_changed_bytes_and_provenance(self):
        self.soap(version=1,repeats=3,filename=f'purchaseProtocol_{REG}_same.xml')
        self.soap(version=2,repeats=3,filename=f'purchaseProtocol_{REG}_same.xml')
        with patch.object(eis223_soap_xml,'parse_document',wraps=eis223_soap_xml.parse_document) as parse:
            value=self.version()
        self.assertEqual(parse.call_count,2)
        self.assertEqual(len(source._XML_CACHE),2)
        records,invalid=source._semantic_xml(source._SemanticIO(),self.root,REG)
        self.assertEqual(len(records),2)
        self.assertEqual(len({record['xml_sha256'] for record in records}),2)
        self.assertEqual({record['xml_filename'] for record in records},{f'purchaseProtocol_{REG}_same.xml'})
        self.assertEqual(invalid,[])
        source.clear_semantic_cache()
        self.assertEqual(value,self.version())

    def test_corrupt_archive_prefix_resumes_without_promoting_any_xml_facts(self):
        for version in range(1,4):
            path,_=self.soap(version=version)
            path.write_bytes(bytes([64+version])*4000)
        attempts=0
        with patch.object(source,"SEMANTIC_MAX_BYTES",7000), \
             patch.object(eis223_soap_xml,"parse_document",side_effect=AssertionError("Corrupt XML must not parse")):
            for _ in range(5):
                try:completed=self.version();break
                except source.SemanticSnapshotError as exc:
                    self.assertEqual(exc.reason,"byte_budget");attempts+=1
            else:self.fail("Stable corrupt archive prefix never completed")
        self.assertEqual(attempts,2)
        self.assertEqual(len(source._XML_CACHE),0)
        corrupt=[value for key,value in source._HASH_CACHE.items() if key[0].endswith('.zip')]
        self.assertEqual(len(corrupt),3)
        self.assertTrue(all(value[2] is None for value in corrupt))
        records,invalid=source._semantic_xml(source._SemanticIO(),self.root,REG)
        self.assertEqual(records,[])
        self.assertEqual(len(invalid),3)
        self.assertTrue(all(value['state']=='corrupt' for value in invalid))
        source.clear_semantic_cache()
        self.assertEqual(completed,self.version())

    def test_corrupt_archive_cache_invalidates_after_same_path_repair_with_restored_mtime(self):
        path,_=self.soap();raw=path.read_bytes()
        path.write_bytes(b'x'*len(raw));before_stat=path.stat()
        corrupt=self.version()
        self.assertEqual(len(source._XML_CACHE),0)
        self.assertTrue(any(key[0]==str(path) for key in source._HASH_CACHE))
        path.write_bytes(raw)
        os.utime(path,ns=(before_stat.st_atime_ns,before_stat.st_mtime_ns))
        with patch.object(eis223_soap_xml,'parse_document',wraps=eis223_soap_xml.parse_document) as parse:
            repaired=self.version()
        self.assertNotEqual(repaired,corrupt)
        parse.assert_called_once()
        self.assertEqual(len(source._XML_CACHE),1)
        source.clear_semantic_cache()
        self.assertEqual(repaired,self.version())

    def test_matching_digest_only_cache_cannot_skip_xml_authentication(self):
        path,_=self.soap()
        expected=path.stem
        reader=source._SemanticIO()
        reader.read(path,15*1024*1024,retain=False,expected=expected)
        self.assertEqual(len(source._XML_CACHE),0)
        with patch.object(eis223_soap_xml,'parse_document',wraps=eis223_soap_xml.parse_document) as parse:
            self.version()
        parse.assert_called_once()
        self.assertEqual(len(source._XML_CACHE),1)

    def test_corrupt_archive_cache_rejects_racing_change_before_reuse(self):
        path,_=self.soap();raw=path.read_bytes();path.write_bytes(b'x'*len(raw))
        self.version()
        original=source._cache_get
        changed=False
        def racing(cache,key):
            nonlocal changed
            value=original(cache,key)
            if cache is source._HASH_CACHE and key[0]==str(path) and value is not None:
                path.write_bytes(raw);changed=True
            return value
        with patch.object(source,'_cache_get',side_effect=racing):
            with self.assertRaisesRegex(source.SemanticSnapshotError,'concurrent_file_change'):
                self.version()
        self.assertTrue(changed)
        self.assertEqual(len(source._XML_CACHE),0)
        self.assertEqual(len(self.version()),64)

    def test_untrusted_archive_receipt_does_not_prime_or_reuse_digest_cache(self):
        path,receipt=self.soap();value=json.loads(receipt.read_text())
        value['source']='private_untrusted_archive';receipt.write_text(json.dumps(value))
        path.write_bytes(b'x'*4096)
        self.version()
        self.assertFalse(any(key[0]==str(path) for key in source._HASH_CACHE))
        self.assertEqual(len(source._XML_CACHE),0)
        value['source']='official_eis_soap_archive';receipt.write_text(json.dumps(value))
        self.version()
        self.assertTrue(any(key[0]==str(path) for key in source._HASH_CACHE))
        self.assertEqual(len(source._XML_CACHE),0)

    def test_malformed_current_receipt_is_not_a_stable_snapshot(self):
        (self.path.parent/"receipt.json").write_text("broken")
        with self.assertRaisesRegex(source.SemanticSnapshotError,"invalid_metadata"):self.version()

    def test_file_io_is_rejected_inside_a_writer_transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        with patch.object(source.os,"open",side_effect=AssertionError("must not touch filesystem")):
            with self.assertRaisesRegex(source.SemanticSnapshotError,"active_sql_transaction"):
                source.semantic_source_version(self.db,REG)
        self.db.rollback()

    def test_sql_readers_are_released_before_filesystem_work(self):
        self.db.commit();original=source._SemanticIO.read
        def read(reader,*args,**kwargs):
            self.assertFalse(self.db.in_transaction)
            return original(reader,*args,**kwargs)
        with patch.object(source._SemanticIO,"read",read):self.version()

    def test_minimal_schema_is_supported_without_docs_or_purchase(self):
        db=sqlite3.connect(":memory:")
        try:self.assertEqual(len(source.semantic_source_version(db,REG)),64)
        finally:db.close()

    def test_concurrent_authorized_source_baseline_is_preserved(self):
        import ast
        current=Path(source.__file__).read_text()
        # Authorized PR67 source format 2 at c3211e2; no git needed in Docker.
        expected={
            "source_version":"18b4580b5ec5337bfe6b9a592ffe2a81d928c1214fb499bd51bf2a7e7313cfe6",
            "original_sources":"2e9109b8753a1fe8e8c9253e106fdc9a4f9606e7348ccaf20ab9191288723850",
            "originals_ready":"1033a2a5e7c525e771c4d395d953a184fede4365e4365fe26c7ae8d7428d2d06",
            "originals_blocked":"f1a7447cbe7deb402f5ee8ac81f89e65ca02d8e50bb09408db8d30b50cc1e7ee",
            "_metadata":"4471ef9df696caa2ff5ab05c242898f4374b00a6c5304599c98b5093df41c7d6",
        }
        nodes={node.name:node for node in ast.parse(current).body if isinstance(node,ast.FunctionDef)}
        for name,digest in expected.items():
            with self.subTest(name=name):
                self.assertEqual(hashlib.sha256(ast.dump(nodes[name],include_attributes=False).encode()).hexdigest(),digest)

    def test_legacy_stat_based_behavior_is_preserved_for_other_consumers(self):
        self.add_doc();self.db.commit()
        before=source.source_version(self.db,REG)
        os.utime(self.path,ns=(1,2))
        self.assertNotEqual(before,source.source_version(self.db,REG))
        before=source.source_version(self.db,REG)
        self.db.execute("UPDATE docs SET status='downloaded_no_text'");self.db.commit()
        self.assertNotEqual(before,source.source_version(self.db,REG))


if __name__ == "__main__": unittest.main()
