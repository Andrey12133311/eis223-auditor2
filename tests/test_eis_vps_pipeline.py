"""Offline contracts of VPS -> real site queue -> persisted originals/buyer facts.

All EIS XML, archives, tokens and original files below are SYNTHETIC fixtures.
No user document or credential is included and no external request is made.
The legal-review engine is intentionally unavailable: delivery must never
increase audited-document counters or invent a completed legal review.
"""
from __future__ import annotations

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
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import eis_server_worker as server
import eis_vps_only as intake_policy
import eis_vps_worker as worker
import v294_eis_bridge as archives
import v295_eis_originals as originals
import v298_eis_only as offline
import v301_search_jobs as jobs


REG = "32616233661"
SITE = "https://pipeline-fixture.example"
SITE_TOKEN = "synthetic-site-credential"
EIS_TOKEN = "synthetic-eis-credential"
PURCHASE_NS = "http://zakupki.gov.ru/223fz/purchase/1"
ARCHIVE_URL = "https://int.zakupki.gov.ru/dstore/ip/download/RI223/synthetic.zip"
ORIGINAL_URLS = (
    "https://zakupki.gov.ru/223/purchase/public/download/download.html?id=1234567",
    "https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid=" + "A" * 32,
)
ORIGINAL_BYTES = tuple(
    ("{\\rtf1\\ansi Synthetic pipeline fixture original " + str(i) + ".}").encode()
    for i in (1, 2)
)


def synthetic_zip(reg=REG, buyer_inn=None):
    """Official 16.3 envelope shape, but deliberately invented fixture data."""
    kind = "purchaseNoticeKESMBO"
    q = lambda name: "{" + PURCHASE_NS + "}" + name
    root = ET.Element(q(kind))
    body = ET.SubElement(root, q("body"))
    item = ET.SubElement(body, q("item"))
    data = ET.SubElement(item, q(kind + "Data"))
    for name, value in (("registrationNumber", reg), ("version", "1"),
                        ("publicationDateTime", "2026-10-18T10:00:00+03:00"),
                        ("name", "Synthetic integration fixture; not a real procurement")):
        ET.SubElement(data, q(name)).text = value
    if buyer_inn:
        buyer = ET.SubElement(ET.SubElement(data, q("customer")), q("mainInfo"))
        ET.SubElement(buyer, q("fullName")).text = "Общество с ограниченной ответственностью «Синтетический заказчик»"
        ET.SubElement(buyer, q("inn")).text = buyer_inn
    attachments = ET.SubElement(data, q("attachments"))
    for index, url in enumerate(ORIGINAL_URLS, start=1):
        document = ET.SubElement(attachments, q("document"))
        for name, value in (("fileName", f"synthetic-original-{index}.rtf"),
                            ("url", url), ("guid", f"synthetic-guid-{index}")):
            ET.SubElement(document, q(name)).text = value
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{kind}_{reg}_1_synthetic.xml",
                         ET.tostring(root, encoding="utf-8", xml_declaration=True))
    return stream.getvalue()


class MemoryResponse(io.BytesIO):
    def __init__(self, data, headers=None, url=""):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data)), **(headers or {})}
        self.url = url

    def geturl(self):
        return self.url


class SiteOpener:
    """Adapt urllib to TestClient; preserve actual worker HTTP/auth handling."""
    def __init__(self, client):
        self.client = client

    def open(self, request, timeout):
        parsed = urllib.parse.urlsplit(request.full_url)
        assert parsed.scheme + "://" + parsed.netloc == SITE
        path = parsed.path + (("?" + parsed.query) if parsed.query else "")
        response = self.client.request(request.method, path, content=request.data,
                                       headers=dict(request.header_items()),
                                       follow_redirects=False)
        if response.status_code >= 300:
            raise urllib.error.HTTPError(request.full_url, response.status_code,
                                          "synthetic in-process HTTP response",
                                          dict(response.headers), io.BytesIO(response.content))
        return MemoryResponse(response.content, dict(response.headers), request.full_url)


class SyntheticEisOpener:
    """Only the external EIS responses are simulated."""
    def __init__(self, testcase, archive):
        self.testcase = testcase
        self.archive = archive
        self.missing = {ORIGINAL_URLS[1]}
        self.calls = []

    def open(self, request, timeout):
        self.calls.append(request.full_url)
        headers = {key.lower(): value for key, value in request.header_items()}
        self.testcase.assertEqual(headers["individualperson_token"], EIS_TOKEN)
        self.testcase.assertNotIn("x-eis223-upload-token", headers)
        if request.full_url == worker.ENDPOINT:
            envelope = ET.fromstring(request.data)
            header = envelope.find("{" + worker.SOAP_NS + "}Header")
            self.testcase.assertEqual(header.findtext("individualPerson_token"), EIS_TOKEN)
            operation = envelope.find("{" + worker.SOAP_NS + "}Body")[0]
            self.testcase.assertEqual(operation.tag,
                                     "{" + worker.API_NS + "}getDocsByReestrNumberRequest")
            self.testcase.assertEqual([child.tag for child in operation],
                                     ["index", "selectionParams"])
            self.testcase.assertEqual(operation.findtext("selectionParams/subsystemType"), "RI223")
            self.testcase.assertEqual(operation.findtext("selectionParams/reestrNumber"), REG)
            self.testcase.assertEqual(operation.findtext("index/mode"), "PROD")
            raw = (f'<soap:Envelope xmlns:soap="{worker.SOAP_NS}"><soap:Body>'
                   f'<ws:getDocsByReestrNumberResponse xmlns:ws="{worker.API_NS}">'
                   f'<dataInfo><archiveUrl>{ARCHIVE_URL}</archiveUrl></dataInfo>'
                   '</ws:getDocsByReestrNumberResponse></soap:Body></soap:Envelope>').encode()
            return MemoryResponse(raw, {"Content-Type": "text/xml"}, request.full_url)
        if request.full_url == ARCHIVE_URL:
            return MemoryResponse(self.archive, {"Content-Type": "application/zip"}, request.full_url)
        self.testcase.assertIn(request.full_url, ORIGINAL_URLS)
        if request.full_url in self.missing:
            raise urllib.error.HTTPError(request.full_url, 404, "synthetic missing original", {}, None)
        return MemoryResponse(ORIGINAL_BYTES[ORIGINAL_URLS.index(request.full_url)], url=request.full_url)


class InProcessTransport(worker.Transport):
    def __init__(self, config, client, eis):
        self.config = config
        self.site_opener = SiteOpener(client)
        self.eis_opener = eis
        self.last_file_at = 0.0


class SyntheticInnEisOpener:
    """A search intentionally includes one procurement belonging to another buyer."""
    def __init__(self, testcase, inn, buyers):
        self.testcase = testcase
        self.inn = inn
        self.buyers = buyers
        self.soap_registries = []
        self.archives = {
            f"https://int.zakupki.gov.ru/dstore/ip/download/RI223/synthetic-{reg}.zip":
                synthetic_zip(reg, buyer_inn)
            for reg, buyer_inn in buyers.items()
        }

    def open(self, request, timeout):
        headers = {key.lower(): value for key, value in request.header_items()}
        self.testcase.assertNotIn("x-eis223-upload-token", headers)
        if request.full_url == worker.inn_search_url(self.inn, 1):
            self.testcase.assertNotIn("individualperson_token", headers)
            links = "".join(
                f'<a href="/epz/order/notice/notice223/common-info.html?regNumber={reg}">№ {reg}</a>'
                for reg in self.buyers
            )
            html = f'<html><body><input name="customerInn" value="{self.inn}">{links}</body></html>'
            return MemoryResponse(html.encode(), {"Content-Type": "text/html"}, request.full_url)
        self.testcase.assertEqual(headers["individualperson_token"], EIS_TOKEN)
        if request.full_url == worker.ENDPOINT:
            envelope = ET.fromstring(request.data)
            operation = envelope.find("{" + worker.SOAP_NS + "}Body")[0]
            reg = operation.findtext("selectionParams/reestrNumber")
            self.testcase.assertIn(reg, self.buyers)
            self.testcase.assertEqual(operation.findtext("selectionParams/subsystemType"), "RI223")
            self.soap_registries.append(reg)
            archive_url = f"https://int.zakupki.gov.ru/dstore/ip/download/RI223/synthetic-{reg}.zip"
            raw = (f'<soap:Envelope xmlns:soap="{worker.SOAP_NS}"><soap:Body>'
                   f'<ws:getDocsByReestrNumberResponse xmlns:ws="{worker.API_NS}">'
                   f'<dataInfo><archiveUrl>{archive_url}</archiveUrl></dataInfo>'
                   '</ws:getDocsByReestrNumberResponse></soap:Body></soap:Envelope>').encode()
            return MemoryResponse(raw, {"Content-Type": "text/xml"}, request.full_url)
        if request.full_url in self.archives:
            return MemoryResponse(self.archives[request.full_url], {"Content-Type": "application/zip"}, request.full_url)
        self.testcase.assertIn(request.full_url, ORIGINAL_URLS)
        return MemoryResponse(ORIGINAL_BYTES[ORIGINAL_URLS.index(request.full_url)], url=request.full_url)


class VpsPipelineIntegrationTest(unittest.TestCase):
    def test_inn_candidates_need_soap_buyer_proof_and_old_worker_claims_only_regs(self):
        target_inn, other_inn = "9900000017", "9900000024"
        other_reg = "32616233662"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = {"EIS223_DATA_DIR": str(root / "data"), "EIS223_UPLOAD_TOKEN": SITE_TOKEN,
                   "EIS223_SOURCE_MODE": "OFFICIAL_EIS_VPS_ONLY"}
            with patch.dict(os.environ, env):
                db_path = root / "site.sqlite3"

                def connect():
                    db = sqlite3.connect(db_path, timeout=2)
                    db.row_factory = sqlite3.Row
                    return db

                with connect() as db:
                    db.executescript("""
                        CREATE TABLE purchases(reg_number TEXT PRIMARY KEY, title TEXT,
                          customer TEXT, customer_inn TEXT, price TEXT, price_num REAL,
                          violations INTEGER DEFAULT 0, documents_checked INTEGER DEFAULT 0,
                          documents_found INTEGER DEFAULT 0, last_seen TEXT, card_fields_json TEXT,
                          source TEXT, card_source_status TEXT);
                        CREATE TABLE v313_migrations(marker TEXT PRIMARY KEY, migrated_at TEXT NOT NULL);
                        INSERT INTO v313_migrations VALUES('repair_404_noticeId','synthetic-test');
                    """)
                    db.execute("INSERT INTO purchases(reg_number,title) VALUES(?,?)",
                               (other_reg, "Synthetic candidate without buyer evidence"))
                ns = {"app": FastAPI(), "conn": connect}
                archives.install(ns)
                originals.install(ns)
                offline.install(ns)
                jobs.install(ns)
                server.install(ns)
                intake_policy.install(ns)
                # An old mapping must not turn an unverified candidate into an INN result.
                with connect() as db:
                    db.execute("INSERT INTO v301_inn_purchases(inn,reg,found_at) VALUES(?,?,?)",
                               (target_inn, other_reg, "synthetic-stale-mapping"))
                eis = SyntheticInnEisOpener(self, target_inn, {REG: target_inn, other_reg: other_inn})
                config = {"site_url": SITE, "site_token": SITE_TOKEN, "eis_token": EIS_TOKEN,
                          "timeout_seconds": 10, "interval_seconds": 300}
                state = worker.State(root / "worker-state")
                try:
                    with TestClient(ns["app"]) as client:
                        transport = InProcessTransport(config, client, eis)
                        runner = worker.Worker(config, state, transport=transport)
                        submitted = client.post("/api/v301/search", json={"kind": "inn", "value": target_inn})
                        self.assertEqual(submitted.status_code, 200, submitted.text)
                        search_id = submitted.json()["id"]
                        self.assertEqual(submitted.json()["existing"], [])

                        query = {"limit": 2, "worker_id": state.worker_id}
                        legacy = transport.site("/api/eis-worker/jobs/next?" + urllib.parse.urlencode(query))
                        self.assertEqual(legacy["jobs"], [], "Worker 1.0.1 must never receive an INN job")
                        capable = transport.site("/api/eis-worker/jobs/next?" + urllib.parse.urlencode(
                            {**query, "capabilities": "inn_search"}))
                        self.assertEqual([j["kind"] for j in capable["jobs"]], ["inn"])
                        report = runner.process_inn(capable["jobs"][0])
                        self.assertEqual(report["status"], "completed", report)
                        self.assertEqual(set(report["reg_numbers"]), {REG, other_reg})
                        self.assertEqual(eis.soap_registries, [], "Search must not masquerade as SOAP proof")
                        state.save_report(search_id, report)
                        runner.retry_reports()
                        self.assertEqual(state.pending_reports(), [])

                        candidate_status = client.get("/api/v301/job/" + search_id).json()
                        self.assertEqual(candidate_status["candidate_count"], 2)
                        self.assertEqual(candidate_status["verification_pending"], 2)
                        self.assertEqual(candidate_status["verified_matches"], 0)
                        self.assertEqual(candidate_status["existing"], [])
                        with connect() as db:
                            self.assertEqual(db.execute("SELECT COUNT(*) FROM v301_inn_purchases").fetchone()[0], 1)
                            self.assertEqual(db.execute("SELECT COUNT(*) FROM v319_manual_inputs").fetchone()[0], 0)

                        # Another higher-priority INN request remains invisible to an old worker.
                        repeated = client.post("/api/v301/search", json={"kind": "inn", "value": target_inn})
                        self.assertNotEqual(repeated.json()["id"], search_id)
                        downloaded = transport.site("/api/eis-worker/jobs/next?" + urllib.parse.urlencode(query))
                        self.assertEqual({j["reg_number"] for j in downloaded["jobs"]}, {REG, other_reg})
                        self.assertTrue(all(j["kind"] == "reg" for j in downloaded["jobs"]))
                        for job in downloaded["jobs"]:
                            delivery = runner.process(job)
                            self.assertEqual(delivery["status"], "completed", delivery)
                            state.save_report(job["id"], delivery)
                            runner.retry_reports()
                            for url in ORIGINAL_URLS:
                                self.assertIsNotNone(originals.saved_document(job["reg_number"], originals.uid_from_url(url)))

                        result = client.get("/api/v301/job/" + search_id).json()
                        self.assertEqual([r["reg_number"] for r in result["existing"]], [REG])
                        self.assertEqual(result["verified_matches"], 1)
                        self.assertEqual(result["excluded_candidates"], 1)
                        self.assertEqual(result["verification_pending"], 0)
                        self.assertEqual(result["queued_downloads"], 0)
                        self.assertEqual(set(eis.soap_registries), {REG, other_reg})
                        with connect() as db:
                            buyers = dict(db.execute("SELECT reg_number,customer_inn FROM purchases"))
                            self.assertEqual(buyers, {REG: target_inn, other_reg: other_inn})
                            self.assertEqual(db.execute("SELECT SUM(documents_checked),SUM(violations) FROM purchases").fetchone()[:], (0, 0))
                            self.assertEqual(db.execute("SELECT COUNT(*) FROM v319_manual_inputs").fetchone()[0], 0)
                finally:
                    state.db.close()

    def test_real_queue_partial_resume_duplicate_and_delivery_are_not_audit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = {"EIS223_DATA_DIR": str(root / "data"),
                   "EIS223_UPLOAD_TOKEN": SITE_TOKEN,
                   "EIS223_SOURCE_MODE": "OFFICIAL_EIS_VPS_ONLY"}
            with patch.dict(os.environ, env):
                db_path = root / "site.sqlite3"

                def connect():
                    db = sqlite3.connect(db_path, timeout=2)
                    db.row_factory = sqlite3.Row
                    return db

                with connect() as db:
                    db.executescript("""
                        CREATE TABLE purchases(reg_number TEXT PRIMARY KEY, title TEXT,
                          customer TEXT, customer_inn TEXT, price TEXT, price_num REAL,
                          violations INTEGER DEFAULT 0, documents_checked INTEGER DEFAULT 0,
                          documents_found INTEGER DEFAULT 0, last_seen TEXT);
                        CREATE TABLE v313_migrations(marker TEXT PRIMARY KEY, migrated_at TEXT NOT NULL);
                        INSERT INTO v313_migrations VALUES('repair_404_noticeId','synthetic-test');
                    """)
                    db.execute("INSERT INTO purchases(reg_number,title) VALUES(?,?)",
                               (REG, "Synthetic fixture procurement"))
                ns = {"app": FastAPI(), "conn": connect}
                archives.install(ns)
                originals.install(ns)
                jobs.install(ns)
                server.install(ns)
                intake_policy.install(ns)
                data = synthetic_zip()
                eis = SyntheticEisOpener(self, data)
                config = {"site_url": SITE, "site_token": SITE_TOKEN, "eis_token": EIS_TOKEN,
                          "timeout_seconds": 10, "interval_seconds": 300}
                state = worker.State(root / "worker-state")
                try:
                    with TestClient(ns["app"]) as client:
                        transport = InProcessTransport(config, client, eis)
                        runner = worker.Worker(config, state, transport=transport)
                        auth = {"x-eis223-upload-token": SITE_TOKEN}

                        # Unauthenticated data/queue access cannot mutate or reveal work.
                        self.assertEqual(client.post("/api/v294/eis223/archive", content=data).status_code, 401)
                        self.assertEqual(client.get(f"/api/v295/eis223/manifest/{REG}").status_code, 401)
                        self.assertEqual(client.get("/api/eis-worker/jobs/next?worker_id=unauthorized").status_code, 401)
                        original_id = originals.uid_from_url(ORIGINAL_URLS[0])
                        self.assertEqual(client.post(f"/api/v295/eis223/original/{REG}/{original_id}",
                                                     content=ORIGINAL_BYTES[0]).status_code, 401)

                        submitted = client.post("/api/v301/search", json={"kind": "reg", "value": REG})
                        self.assertEqual(submitted.status_code, 200, submitted.text)
                        job_id = submitted.json()["id"]
                        repeated = client.post("/api/v301/search", json={"kind": "reg", "value": REG})
                        self.assertEqual(repeated.json()["id"], job_id)

                        claim = transport.site("/api/eis-worker/jobs/next?" + urllib.parse.urlencode(
                            {"limit": 2, "worker_id": state.worker_id}))
                        self.assertEqual(len(claim["jobs"]), 1)
                        job = claim["jobs"][0]
                        report = runner.process(job)
                        self.assertEqual(report["status"], "partial", report)
                        self.assertEqual(report["originals_received"], 1)
                        self.assertEqual(report["originals_expected"], 2)
                        self.assertEqual(report["originals_failed"], 1)
                        manifest = transport.site(f"/api/v295/eis223/manifest/{REG}")
                        self.assertFalse(manifest["counted_as_audited"])
                        self.assertEqual(sum(d["received"] for d in manifest["documents"]), 1)

                        # The authoritative server must reject fake completion counters.
                        forged = {**report, "status": "completed", "originals_received": 2,
                                  "originals_failed": 0, "retry_after_seconds": 0}
                        transport.site(f"/api/eis-worker/jobs/{job_id}/report", forged)
                        with connect() as db:
                            self.assertNotEqual(db.execute(
                                "SELECT status FROM v301_search_jobs WHERE id=?", (job_id,)).fetchone()[0], "done")
                            # Advance only the retry schedule; preserve the real state/lease transitions.
                            db.execute("UPDATE v301_search_jobs SET not_before=0 WHERE id=?", (job_id,))

                        replay = client.post("/api/v294/eis223/archive", content=data, headers=auth)
                        self.assertTrue(replay.json()["already_received"])
                        inbox = root / "data" / "eis223_official_archive_inbox" / REG
                        self.assertEqual(len(list(inbox.glob("*.zip"))), 1)
                        self.assertEqual(next(inbox.glob("*.zip")).read_bytes(), data)

                        eis.missing.clear()
                        claim2 = transport.site("/api/eis-worker/jobs/next?" + urllib.parse.urlencode(
                            {"limit": 2, "worker_id": state.worker_id}))
                        self.assertEqual(len(claim2["jobs"]), 1, claim2)
                        report2 = runner.process(claim2["jobs"][0])
                        self.assertEqual(report2["status"], "completed", report2)
                        self.assertEqual(report2["originals_uploaded"], 1)
                        self.assertEqual(eis.calls.count(ORIGINAL_URLS[0]), 1)
                        state.save_report(job_id, report2)
                        runner.retry_reports()
                        self.assertEqual(state.pending_reports(), [])

                        for url, expected in zip(ORIGINAL_URLS, ORIGINAL_BYTES):
                            uid = originals.uid_from_url(url)
                            saved = originals.saved_document(REG, uid)
                            self.assertIsNotNone(saved)
                            self.assertEqual(saved[0].read_bytes(), expected)
                            self.assertEqual(saved[1]["sha256"], hashlib.sha256(expected).hexdigest())
                            duplicate = client.post(f"/api/v295/eis223/original/{REG}/{uid}",
                                                    content=expected, headers=auth)
                            self.assertEqual(duplicate.status_code, 200, duplicate.text)
                            self.assertEqual(duplicate.json()["audited_documents_increment"], 0)
                            self.assertEqual(len(list(saved[0].parent.glob("*.rtf"))), 1)
                        with connect() as db:
                            row = db.execute("SELECT status FROM v301_search_jobs WHERE id=?", (job_id,)).fetchone()
                            self.assertEqual(row[0], "done")
                            purchase = db.execute("SELECT documents_checked,violations FROM purchases WHERE reg_number=?",
                                                  (REG,)).fetchone()
                            self.assertEqual(tuple(purchase), (0, 0))
                        review = transport.site(f"/api/v295/eis223/recheck-status/{REG}")
                        self.assertNotEqual(review["status"], "completed")
                finally:
                    state.db.close()


if __name__ == "__main__":
    unittest.main()
