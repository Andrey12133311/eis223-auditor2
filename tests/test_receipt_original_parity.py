"""Real receipt files must reach both engines before the intake saves docs.

The originals and SQLite database are temporary. Existing clarification rules
establish the test findings; no remote I/O or new legal threshold is involved.
"""
import asyncio
import ast
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import AsyncMock, patch
import zipfile

from docx import Document

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_source_version as sources
import v298_eis_only as incoming
import v295_eis_originals as originals
import v332_all_procurements_audit as general
import v341_precise_legal as legal
import v342_thematic_queue as thematic
import v345_thematic_accelerator as fast
from test_general_audit_recovery import DatabaseFixture, LATE_REPLY, URL


LATE_REPLY_B = ("Дата поступления запроса разъяснения: 08.10.2026.\n"
                "Дата публикации ответа в ЕИС: 14.10.2026.")
TIMELY_REPLY = ("Дата поступления запроса разъяснения: 01.10.2026.\n"
                "Дата публикации ответа в ЕИС: 02.10.2026.")


def docx_bytes(text):
    document = Document()
    document.add_paragraph(text)
    encoded = io.BytesIO()
    document.save(encoded)
    return encoded.getvalue()


class ReceiptOriginalParity(DatabaseFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        state, tick = dict(thematic.WORKER_STATE), thematic.tick

        def restore():
            thematic.WORKER_STATE.clear()
            thematic.WORKER_STATE.update(state)
            thematic.tick = tick

        self.addCleanup(restore)
        thematic.WORKER_STATE.clear()
        self.stack.enter_context(patch.object(thematic, "storage_health", return_value={"blocked": False}))
        self.stack.enter_context(patch.object(fast, "_request_missing_originals", return_value=0))
        thematic.init(self.ns)
        fast.install(self.ns)
        self.batch = "b" * 24
        with self.connect() as con:
            con.execute("INSERT INTO thematic_batches(id,categories,created_at,status,legal_forms,date_from,date_to) "
                        "VALUES(?,? ,?,'active',?, '', '')",
                        (self.batch, '["dates"]', thematic.now(), '["ООО"]'))

    def receipt_purchase(self, number=1):
        reg = self.purchase(number)
        # Reproduce the interval after the receipt commit and before docs save.
        with self.connect() as con:
            con.execute("DELETE FROM docs WHERE reg_number=?", (reg,))
        return reg

    def put_original(self, reg, data, name, uid=None):
        entries = self.originals.setdefault(reg, [])
        uid = uid or f"{len(entries) + 1:032X}"
        extension = Path(name).suffix
        digest = hashlib.sha256(data).hexdigest()
        folder = self.folder / "eis223_official_originals" / reg / uid
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (digest + extension)
        path.write_bytes(data)
        (folder / "receipt.json").write_text(json.dumps({
            "reg_number": reg, "uid": uid, "extension": extension,
            "sha256": digest, "bytes": len(data)}))
        entry = {"url": URL + uid, "name": name}
        entries[:] = [item for item in entries if item["url"] != entry["url"]] + [entry]
        manifest = self.folder / "eis223_public_browser_intake" / reg / "manifest.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps({"reg_number": reg,
            "source": "public_eis_223_browser", "documents": entries}))
        return path

    def themed(self, reg):
        with self.connect() as con:
            item = dict(con.execute("SELECT * FROM thematic_items WHERE batch_id=? AND reg=?",
                                    (self.batch, reg)).fetchone())
            findings = [dict(row) for row in con.execute(
                "SELECT * FROM thematic_findings WHERE batch_id=? AND reg=?", (self.batch, reg))]
        return item, findings

    async def finish_general(self, reg):
        row = self.row(reg)
        if row["state"] != "running":
            if row["state"] == "completed":
                # Explicit maintenance replay still proves result-token dedup.
                general.mark(self.ns, reg, "pending")
            self.assertEqual(general.claim_next(self.ns), reg)
            row = self.row(reg)
        claim = general.AuditReservation(reg, row["reservation_token"])
        snapshot = await general.file_stage(self.ns, "test_source", general.review_watermark, self.ns, claim)
        state, detail = await general.inspect_one(self.ns, claim)
        self.assertEqual(state, "completed")
        self.assertFalse(detail["source_incomplete"])
        general.mark(self.ns, claim, state,
                     {**{key: value for key, value in detail.items() if key != "source_incomplete"}, **snapshot})
        return detail

    async def test_receipt_only_original_has_same_confirmed_finding_in_both_audits(self):
        reg = self.receipt_purchase()
        before = {path: path.read_bytes() for path in self.contents}
        general.initialize(self.ns)
        self.assertEqual(general.claim_next(self.ns), reg)
        first = await self.finish_general(reg)
        self.assertEqual((first["verified_original_documents"], first["new_findings"]), (1, 1))
        self.assertEqual(await fast.faster_tick(self.ns), 1)
        item, findings = self.themed(reg)
        self.assertEqual((item["status"], len(findings)), ("completed", 1))
        self.assertEqual(findings[0]["code"], "223-3.2-3-CLARIFICATION-RESPONSE-LATE")
        with self.connect() as con:
            general_finding = dict(con.execute("SELECT * FROM findings WHERE reg_number=?", (reg,)).fetchone())
            self.assertEqual(con.execute("SELECT COUNT(*) FROM docs").fetchone()[0], 0)
        for field in ("code", "evidence", "document", "source_url"):
            self.assertEqual(findings[0][field], general_finding[field])
        self.assertEqual(fast._prepare(self.ns), [])
        self.assertEqual(general.refresh_changed(self.ns), 0)
        self.assertEqual((await self.finish_general(reg))["new_findings"], 0)
        self.assertEqual(general.progress(self.ns)["new_evidence_based_findings"], 1)
        self.assertEqual({path: path.read_bytes() for path in before}, before)

    async def test_real_general_worker_completes_receipt_only_original_without_docs_save(self):
        reg = self.receipt_purchase()
        real_sleep = asyncio.sleep
        async def fast_start(seconds, *args, **kwargs):
            return await real_sleep(0 if seconds == 14 else seconds, *args, **kwargs)
        self.stack.enter_context(patch.object(general.asyncio, "sleep", side_effect=fast_start))
        self.stack.enter_context(patch.object(general, "QUEUE_POLL_SECONDS", .02))
        general.install(self.ns)
        await self.ns["app"].startups[0]()
        worker = self.ns["_v332_campaign_worker"]
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if self.row(reg) and self.row(reg)["state"] == "completed":
                    break
                await real_sleep(.01)
            self.assertEqual(self.row(reg)["state"], "completed", self.output.getvalue())
            self.assertEqual((self.row(reg)["documents_read"], self.row(reg)["new_findings"]), (1, 1))
            self.assertEqual(general.runtime_snapshot(self.ns)["live_workers"], 4)
            with self.connect() as con:
                self.assertEqual(con.execute("SELECT COUNT(*) FROM docs").fetchone()[0], 0)
        finally:
            worker.cancel()
            await asyncio.wait_for(asyncio.gather(worker, return_exceptions=True), 5)

    def test_receipt_claim_reaches_later_page_without_holding_a_writer_over_files(self):
        reg = self.receipt_purchase(100)
        with self.connect() as con:
            for number in range(1, general.RECEIPT_CLAIM_PAGE + 1):
                missing = str(32619000000 + number)
                self.regs.append(missing)
                con.execute("INSERT INTO purchases(reg_number,customer,last_seen) VALUES(?,'ООО Тест','2026-10-19')",
                            (missing,))
        general.initialize(self.ns)
        self.assertIsNone(general.claim_next(self.ns))
        self.assertEqual(general.claim_next(dict(self.ns)), reg)
        self.assertTrue(self.row(reg)["reservation_token"])
        general.return_running(self.ns, reg)
        original_ready = general.receipt_readiness
        def taken_during_read(number):
            ready = original_ready(number)
            if ready[0]:
                with self.connect() as writer:
                    writer.execute("UPDATE v332_audit_queue SET state='running',updated_at='other-worker' WHERE reg=?", (number,))
                    writer.commit()
            return ready
        with patch.object(general, "receipt_readiness", side_effect=taken_during_read):
            self.assertIsNone(general.claim_next(self.ns))
            self.assertIsNone(general.claim_next(self.ns))
        self.assertEqual((self.row(reg)["state"], self.row(reg)["updated_at"]), ("running", "other-worker"))

    def test_receipt_highwater_and_stale_hints_do_not_starve_new_work(self):
        regs = [self.receipt_purchase(number) for number in range(1, 21)]
        general.initialize(self.ns)
        with self.connect() as con:
            con.execute("UPDATE v332_audit_queue SET state='completed'")
        self.assertEqual(general.refresh_changed(self.ns), 8)
        self.assertEqual(general.refresh_changed(self.ns), 8)
        self.assertEqual(general.refresh_changed(self.ns), 0)
        with self.connect() as con:
            self.assertEqual(general.ready_work(con), 16)
        for index, reg in enumerate(regs[:16]):
            path = next((self.folder / "eis223_official_originals" / reg).glob("*/receipt.json"))
            if index % 2:path.write_text("invalid receipt JSON")
            else:path.unlink()
        # High-water still blocks pending admissions, but one healthy terminal
        # can use an existing worker before stale receipt hints are cleared.
        direct = general.claim_next(self.ns)
        self.assertEqual(direct, regs[16])
        with self.connect() as con:
            self.assertEqual(general.ready_work(con), 17)
        self.assertIsNone(general.claim_next(self.ns))
        with self.connect() as con:
            self.assertEqual(general.ready_work(con), 1)
        for reg in regs[:16]:
            self.assertEqual((self.row(reg)["state"], self.row(reg)["receipt_ready"]), ("pending", 0))
        general.mark(self.ns, direct, "completed", general.review_watermark(self.ns, direct))
        self.assertEqual(general.refresh_changed(dict(self.ns)), 3)
        claim = general.claim_next(dict(self.ns))
        self.assertIn(claim, regs[17:])
        self.assertTrue(claim.reservation_token)

    async def test_one_stored_archive_member_does_not_hide_the_other_member(self):
        reg = self.receipt_purchase()
        self.originals[reg] = []
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as container:
            container.writestr("Своевременный ответ.docx", docx_bytes(TIMELY_REPLY))
            container.writestr("Просроченный ответ.docx", docx_bytes(LATE_REPLY_B))
        path = self.put_original(reg, archive.getvalue(), "Разъяснения.zip")
        collected = await incoming.offline_audit(self.ns, reg, collect_only=True)
        member = next(doc for doc in collected["docs"] if doc["status"] == "checked")
        with self.connect() as con:
            con.execute("INSERT INTO docs(reg_number,name,url,doc_type,published_at,local_path,status,metadata_json,final_url) "
                        "VALUES(?,?,?,?,?,?,?,?,?)", (reg, member["name"], member["url"], "clarification", "",
                        member["local"], member["status"], json.dumps(member["meta"]), member["final_url"]))
        purchase, rows = general.existing(self.ns, reg)
        old_subset, unreadable = await general.stored_docs(self.ns, reg, rows)
        self.assertEqual((len(old_subset), unreadable), (1, 0))
        self.assertEqual(legal.evaluate(general.as_procurement(purchase), old_subset)[0], [])
        general.initialize(self.ns)
        detail = await self.finish_general(reg)
        self.assertEqual((detail["documents_read"], detail["new_findings"]), (2, 1))
        await fast.faster_tick(self.ns)
        item, findings = self.themed(reg)
        self.assertEqual((item["status"], len(findings)), ("completed", 1))
        self.assertIn("Просроченный ответ.docx", findings[0]["document"])
        self.assertEqual(findings[0]["source_url"], self.originals[reg][0]["url"])
        self.assertEqual(path.read_bytes(), archive.getvalue())
        with self.connect() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM docs").fetchone()[0], 1)

    async def test_archive_member_cache_is_restored_from_original_after_tamper_or_symlink(self):
        reg = self.receipt_purchase()
        self.originals[reg] = []
        archive = io.BytesIO()
        expected = docx_bytes(LATE_REPLY)
        with zipfile.ZipFile(archive, "w") as container:
            container.writestr("Разъяснение.docx", expected)
            container.writestr("../../escape.docx", docx_bytes(TIMELY_REPLY))
            symbolic = zipfile.ZipInfo("link.docx")
            symbolic.create_system = 3
            symbolic.external_attr = 0o120777 << 16
            container.writestr(symbolic, b"/tmp/should-not-read")
        original = self.put_original(reg, archive.getvalue(), "Разъяснения.zip")
        first = await incoming.offline_audit(self.ns, reg, collect_only=True)
        member = next(doc for doc in first["docs"] if doc["status"] == "checked")
        cached = Path(member["local"])
        other = self.folder / "unrelated.docx"
        other.write_bytes(docx_bytes(TIMELY_REPLY))
        for damage in ("bytes", "symlink"):
            with self.subTest(damage=damage):
                if damage == "bytes":
                    cached.write_bytes(other.read_bytes())
                else:
                    cached.unlink()
                    cached.symlink_to(other)
                repaired = await incoming.offline_audit(self.ns, reg, collect_only=True)
                checked = [doc for doc in repaired["docs"] if doc["status"] == "checked"]
                self.assertEqual(len(checked), 1)
                self.assertEqual(cached.read_bytes(), expected)
                self.assertFalse(cached.is_symlink())
                self.assertEqual(checked[0]["meta"]["sha256"], hashlib.sha256(cached.read_bytes()).hexdigest())
                self.assertEqual(len(legal.evaluate(repaired["p"], checked)[0]), 1)
                self.assertEqual(Document(other).paragraphs[0].text, TIMELY_REPLY)
                self.assertEqual(original.read_bytes(), archive.getvalue())
                self.assertFalse((self.folder / "escape.docx").exists())

    async def test_new_receipt_reopens_completed_audits_without_new_doc_id(self):
        reg = self.receipt_purchase()
        general.initialize(self.ns)
        await self.finish_general(reg)
        await fast.faster_tick(self.ns)
        previous = self.themed(reg)[0]
        self.assertEqual(fast._prepare(self.ns), [])
        self.put_original(reg, docx_bytes(LATE_REPLY_B), "Второе разъяснение.docx")
        prepared = fast._prepare(self.ns)
        self.assertEqual([item["reg"] for item in prepared], [reg])
        self.assertEqual(prepared[0]["ready"], 0)
        self.assertTrue(prepared[0]["originals_ready"])
        current, _ = await fast._retry_file(fast._watermark, self.ns, reg)
        self.assertNotEqual(current, previous["last_semantic_version"])
        self.assertEqual(self.themed(reg)[0]["status"], "queued")
        self.assertTrue(prepared[0]["reservation_token"])
        fast._release_items(self.ns, prepared)
        self.assertEqual(general.refresh_changed(self.ns), 1)
        self.assertEqual(general.claim_next(self.ns), reg)
        self.assertEqual((await self.finish_general(reg))["new_findings"], 1)
        await fast.faster_tick(self.ns)
        item, findings = self.themed(reg)
        self.assertEqual((item["status"], len(findings), item["last_doc_id"]), ("completed", 2, 0))
        self.assertEqual((self.findings(reg), general.progress(self.ns)["new_evidence_based_findings"]), (2, 2))
        self.assertEqual(fast._prepare(self.ns), [])
        self.assertEqual(general.refresh_changed(self.ns), 0)

    async def test_unreadable_receipt_original_preserves_finding_and_backs_off_until_repaired(self):
        reg = self.receipt_purchase()
        path = self.put_original(reg, b"damaged original docx", "Второе разъяснение.docx")
        uid = path.parent.name
        await fast.faster_tick(self.ns)
        item, findings = self.themed(reg)
        self.assertEqual((item["status"], item["retry_attempts"], len(findings)), ("retry", 1, 1))
        self.assertEqual(fast._prepare(self.ns), [])
        self.put_original(reg, docx_bytes(LATE_REPLY_B), "Второе разъяснение.docx", uid)
        deadline = item["next_retry_at"]
        self.assertEqual(fast._prepare(self.ns), [])
        self.assertEqual(self.themed(reg)[0]["next_retry_at"], deadline)
        with self.connect() as con:
            con.execute("UPDATE thematic_items SET next_retry_at=0,updated_at='2000-01-01' WHERE reg=?", (reg,))
        await fast.faster_tick(self.ns)
        item, findings = self.themed(reg)
        self.assertEqual((item["status"], item["retry_attempts"], len(findings)), ("completed", 0, 2))
        self.assertEqual(fast._prepare(self.ns), [])

    async def test_archive_partial_extraction_is_retried_with_valid_findings_retained(self):
        reg = self.receipt_purchase()
        self.originals[reg] = []
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as container:
            container.writestr("Разъяснение.docx", docx_bytes(LATE_REPLY))
            container.writestr("Повреждённый документ.docx", b"invalid docx")
        self.put_original(reg, archive.getvalue(), "Разъяснения.zip")
        await fast.faster_tick(self.ns)
        item, findings = self.themed(reg)
        self.assertEqual((item["status"], item["retry_attempts"], len(findings)), ("retry", 1, 1))
        self.assertEqual(fast._prepare(self.ns), [])
        state, detail = await general.inspect_one(self.ns, reg)
        self.assertEqual((state, detail["new_findings"]), ("completed", 1))
        self.assertTrue(detail["source_incomplete"])

    async def test_failed_receipt_collection_does_not_finish_thematic_audit(self):
        reg = self.receipt_purchase()
        with patch.object(general, "offline_audit", side_effect=asyncio.TimeoutError()):
            await fast.faster_tick(self.ns)
        item, findings = self.themed(reg)
        self.assertEqual((item["status"], item["retry_attempts"], findings), ("retry", 1, []))
        self.assertEqual(fast._prepare(self.ns), [])

    async def test_tampered_or_unlisted_receipt_never_becomes_proven_violation(self):
        altered, unlisted = [self.receipt_purchase(number) for number in (1, 2)]
        for path in self.contents:
            if path.parent.parent.name == altered:
                raw = path.read_bytes()
                path.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
        manifest = self.folder / "eis223_public_browser_intake" / unlisted / "manifest.json"
        manifest.write_text(json.dumps({"reg_number": unlisted,
            "source": "public_eis_223_browser", "documents": []}))
        await fast.faster_tick(self.ns)
        self.assertEqual((self.themed(altered)[0]["status"], self.themed(altered)[1]), ("retry", []))
        self.assertEqual((self.themed(unlisted)[0]["status"], self.themed(unlisted)[1]), ("waiting_originals", []))

    async def test_tampered_supplemental_original_does_not_make_readable_subset_complete(self):
        reg = self.receipt_purchase()
        path = self.put_original(reg, docx_bytes(LATE_REPLY_B), "Второе разъяснение.docx")
        raw = path.read_bytes()
        path.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
        await fast.faster_tick(self.ns)
        item, findings = self.themed(reg)
        self.assertEqual((item["status"], len(findings)), ("retry", 1))
        state, detail = await general.inspect_one(self.ns, reg)
        self.assertEqual((state, detail["new_findings"], detail["documents_read"]), ("completed", 1, 1))
        self.assertTrue(detail["source_incomplete"])

    def test_source_snapshot_ignores_receipt_operations_and_does_not_read_original_bytes(self):
        reg = self.receipt_purchase()
        with self.connect() as con:
            before = sources.source_version(con, reg)
        path = next(iter(self.contents))
        receipt_path = path.parent / "receipt.json"
        receipt = json.loads(receipt_path.read_text())
        receipt.update(checked_at="later", state="checked", extraction_ms=99, error="temporary")
        receipt_path.write_text(json.dumps(receipt))
        with self.connect() as con, patch.object(Path, "read_bytes", side_effect=AssertionError("Read binary source")):
            self.assertEqual(sources.source_version(con, reg), before)
        original_scan = sources.original_sources
        def scan_outside_reader(number):
            with self.connect() as writer:
                writer.execute("UPDATE purchases SET last_seen='concurrent writer' WHERE reg_number=?", (reg,))
                writer.commit()
            return original_scan(number)
        with self.connect() as con, patch.object(sources, "original_sources", side_effect=scan_outside_reader):
            self.assertFalse(con.in_transaction)
            self.assertEqual(sources.source_version(con, reg), before)

    async def test_catalogue_limit_isolated_from_other_workers_and_retains_readable_subset(self):
        readable = self.purchase(1)
        blocked = self.receipt_purchase(2)
        good = self.receipt_purchase(3)
        for reg in (readable, blocked):
            self.put_original(reg, docx_bytes(LATE_REPLY_B), "Второй ответ.docx")
        general.initialize(self.ns)
        with self.connect() as con:
            con.execute("UPDATE v332_audit_queue SET original_expected=2,original_received=2 WHERE reg=?", (readable,))
        real_sleep = asyncio.sleep
        async def fast_start(seconds, *args, **kwargs):
            return await real_sleep(0 if seconds == 14 else seconds, *args, **kwargs)
        self.stack.enter_context(patch.object(general.asyncio, "sleep", side_effect=fast_start))
        self.stack.enter_context(patch.object(general, "QUEUE_POLL_SECONDS", .02))
        with patch.object(originals, "MAX_KNOWN_DOCUMENTS", 1):
            blocked_source = sources.original_sources(blocked)
            self.assertFalse(any(item["available"] for item in blocked_source))
            self.assertTrue(blocked_source[0]["scan_error"])
            general.install(self.ns)
            await self.ns["app"].startups[0]()
            worker = self.ns["_v332_campaign_worker"]
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    if (self.row(good)["state"] == "completed" and
                            self.row(readable)["retry_count"] == 1 and
                            self.row(blocked)["retry_count"] == 1):
                        break
                    await real_sleep(.01)
                self.assertEqual(self.row(good)["state"], "completed", self.output.getvalue())
                for reg in (readable, blocked):
                    self.assertEqual((self.row(reg)["state"], self.row(reg)["retry_count"]), ("pending", 1))
                    self.assertIn("limit", self.row(reg)["error"])
                self.assertEqual((self.row(readable)["new_findings"], self.row(readable)["original_received"]), (1, 2))
                self.assertEqual(self.row(blocked)["original_received"], 0)
                self.assertEqual(general.runtime_snapshot(self.ns)["live_workers"], 4)
                await fast.faster_tick(self.ns)
                self.assertEqual((self.themed(good)[0]["status"], len(self.themed(good)[1])), ("completed", 1))
                self.assertEqual((self.themed(readable)[0]["status"], len(self.themed(readable)[1])), ("retry", 1))
                self.assertEqual((self.themed(blocked)[0]["status"], self.themed(blocked)[1]), ("retry", []))
                self.assertEqual(fast._prepare(self.ns), [])
                self.assertEqual(general.refresh_changed(self.ns), 0)
            finally:
                worker.cancel()
                await asyncio.wait_for(asyncio.gather(worker, return_exceptions=True), 5)
        # Resource recovery must preserve the already promised retry deadlines.
        self.assertEqual(general.refresh_changed(self.ns), 0)
        self.assertIsNone(general.claim_next(self.ns))
        self.assertEqual(fast._prepare(self.ns), [])
        for reg in (readable, blocked):
            self.assertEqual(self.row(reg)["last_semantic_version"], "")
            self.assertEqual(self.themed(reg)[0]["last_semantic_version"], "")
        with self.connect() as con:
            con.execute("UPDATE v332_audit_queue SET next_retry_at=0 WHERE state='pending'")
            con.execute("UPDATE thematic_items SET next_retry_at=0,updated_at='2000-01-01' WHERE status='retry'")
        for reg in (readable, blocked):
            await self.finish_general(reg)
        await fast.faster_tick(self.ns)
        self.assertEqual((self.themed(readable)[0]["status"], len(self.themed(readable)[1])), ("completed", 2))
        self.assertEqual((self.themed(blocked)[0]["status"], len(self.themed(blocked)[1])), ("completed", 2))

    async def test_review_manifest_byte_limit_cannot_complete_after_allowlist_succeeds(self):
        reg = self.purchase()
        manifest = self.folder / "eis223_public_browser_intake" / reg / "manifest.json"
        value = json.loads(manifest.read_text())
        value["diagnostic_padding"] = "x" * 2048
        manifest.write_text(json.dumps(value))
        with patch.object(originals, "MAX_SOURCE_MANIFEST_BYTES", 1024):
            self.assertEqual(len(originals.allowed_documents(reg)), 1)
            self.assertTrue(sources.original_sources(reg)[0]["scan_error"])
            general.initialize(self.ns)
            state, detail = await general.inspect_one(self.ns, reg)
            self.assertEqual((state, detail["new_findings"]), ("completed", 1))
            self.assertTrue(detail["source_incomplete"])
            self.assertNotIn("original_received", detail)
            self.assertNotIn("original_expected", detail)
            await fast.faster_tick(self.ns)
            item, findings = self.themed(reg)
            self.assertEqual((item["status"], len(findings)), ("retry", 1))
            self.assertIn("safe read limit", item["detail"])
            self.assertEqual(fast._prepare(self.ns), [])

    async def test_semantic_manifest_change_reopens_receipt_only_audit_without_multiplying_findings(self):
        reg = self.receipt_purchase()
        general.initialize(self.ns)
        await self.finish_general(reg)
        await fast.faster_tick(self.ns)
        before = self.themed(reg)[0]["last_source_version"]
        manifest = self.folder / "eis223_public_browser_intake" / reg / "manifest.json"
        value = json.loads(manifest.read_text())
        value["customer"] = "ООО Тест, уточнённое наименование"
        manifest.write_text(json.dumps(value))
        self.assertEqual(general.refresh_changed(self.ns), 1)
        self.assertEqual(general.claim_next(self.ns), reg)
        self.assertEqual((await self.finish_general(reg))["new_findings"], 0)
        prepared = fast._prepare(self.ns)
        self.assertEqual([item["reg"] for item in prepared], [reg])
        self.assertTrue(prepared[0]["reservation_token"])
        fast._release_items(self.ns, prepared)
        await fast.faster_tick(self.ns)
        item, findings = self.themed(reg)
        self.assertNotEqual(item["last_source_version"], before)
        self.assertEqual((item["status"], len(findings), self.findings(reg)), ("completed", 1, 1))
        self.assertEqual(fast._prepare(self.ns), [])
        self.assertEqual(general.refresh_changed(self.ns), 0)

    async def test_identical_hash_merge_keeps_secondary_dates_roles_and_nested_provenance(self):
        reg = self.purchase()
        purchase, rows = general.existing(self.ns, reg)
        docs, _ = await general.stored_docs(self.ns, reg, rows)
        earlier = deepcopy(docs[0])
        earlier.update(name="Ранее сохранённое имя.docx", published="2026-10-01")
        earlier["meta"].update(protocol_type="review", identical_sources=[{
            "name": "Ещё более раннее имя.docx", "published": "2026-09-30",
            "meta": {"source": "official_eis_original", "source_url": earlier["url"]}}])
        current = deepcopy(docs[0])
        merged = general.merge_docs([current], [earlier])
        self.assertEqual(len(merged), 1)
        aliases = merged[0]["meta"]["identical_sources"]
        retained = next(item for item in aliases if item.get("name") == earlier["name"])
        self.assertEqual((retained["published"], retained["meta"]["protocol_type"]), ("2026-10-01", "review"))
        self.assertTrue(any(item.get("published") == "2026-09-30" for item in aliases))
        self.assertEqual(general.merge_docs(merged, [earlier])[0]["meta"]["identical_sources"], aliases)
        self.assertEqual(len(legal.evaluate(general.as_procurement(purchase), merged)[0]), 1)

    def test_nested_operational_alias_updates_do_not_change_source_identity(self):
        reg = self.purchase()
        with self.connect() as con:
            row = con.execute("SELECT id,metadata_json FROM docs WHERE reg_number=?", (reg,)).fetchone()
            meta = json.loads(row["metadata_json"])
            alias = {"name": "Протокол.docx", "published": "2026-10-01", "error": "old",
                     "meta": {"source": "official_eis_original", "source_url": URL + "A" * 32,
                              "received_at": "old", "state": "stored", "protocol_type": "review"}}
            meta["identical_sources"] = [alias]
            con.execute("UPDATE docs SET metadata_json=? WHERE id=?", (json.dumps(meta), row["id"]))
            con.commit()
            before = sources.source_version(con, reg)
            alias["error"] = "later warning"
            alias["meta"].update(received_at="later", state="checked", extraction_ms=123)
            con.execute("UPDATE docs SET metadata_json=? WHERE id=?", (json.dumps(meta), row["id"]))
            con.commit()
            self.assertEqual(sources.source_version(con, reg), before)
            alias["published"] = "2026-10-02"
            con.execute("UPDATE docs SET metadata_json=? WHERE id=?", (json.dumps(meta), row["id"]))
            con.commit()
            self.assertNotEqual(sources.source_version(con, reg), before)

    async def test_xlsm_is_read_by_actual_extractor_from_stored_row_and_archive(self):
        import openpyxl
        import v347_text_cache as cache
        # Compile only the existing function: importing the app would start
        # unrelated application initialization against its default DB path.
        app_path = Path(__file__).resolve().parents[1] / "app.py"
        parsed = ast.parse(app_path.read_text())
        function = next(node for node in parsed.body if isinstance(node, ast.FunctionDef) and node.name == "extract_text")
        namespace = {"Path": Path, "openpyxl": openpyxl, "Document": Document}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(app_path), "exec"), namespace)
        self.ns["extract_text"] = namespace["extract_text"]
        cache.install(self.ns)
        workbook = openpyxl.Workbook()
        workbook.active.append(["Дата поступления запроса разъяснения: 01.10.2026."])
        workbook.active.append(["Дата публикации ответа в ЕИС: 07.10.2026."])
        workbook.active.append(["=1+1"])
        package = io.BytesIO()
        workbook.save(package)
        workbook.close()
        macro_package = io.BytesIO()
        with zipfile.ZipFile(package) as plain, zipfile.ZipFile(macro_package, "w") as macro:
            for part in plain.infolist():
                body = plain.read(part)
                if part.filename == "[Content_Types].xml":
                    body = body.replace(b"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
                                        b"application/vnd.ms-excel.sheet.macroEnabled.main+xml")
                macro.writestr(part, body)
            macro.writestr("xl/vbaProject.bin", b"inert synthetic macro payload, never executed")
        data = macro_package.getvalue()
        originals.assert_format(data, ".xlsm")
        standalone, archived = self.receipt_purchase(1), self.receipt_purchase(2)
        self.originals[standalone] = []
        path = self.put_original(standalone, data, "Разъяснения.xlsm")
        meta = {"source": "official_eis_original", "source_url": self.originals[standalone][0]["url"],
                "sha256": hashlib.sha256(data).hexdigest()}
        with self.connect() as con:
            con.execute("INSERT INTO docs(reg_number,name,url,local_path,status,metadata_json) VALUES(?,?,?,?,'checked',?)",
                        (standalone, "Разъяснения.xlsm", meta["source_url"], str(path), json.dumps(meta)))
        _, rows = general.existing(self.ns, standalone)
        docs, unreadable = await general.stored_docs(self.ns, standalone, rows)
        self.assertEqual((len(docs), unreadable), (1, 0))
        self.assertNotIn("=1+1", docs[0]["text"])
        self.assertNotIn("macro payload", docs[0]["text"])
        self.originals[archived] = []
        wrapped = io.BytesIO()
        with zipfile.ZipFile(wrapped, "w") as archive:
            archive.writestr("Разъяснения.xlsm", data)
        archive_path = self.put_original(archived, wrapped.getvalue(), "Документы.zip")
        general.initialize(self.ns)
        for reg in (standalone, archived):
            detail = await self.finish_general(reg)
            self.assertEqual((detail["documents_read"], detail["new_findings"]), (1, 1))
        await fast.faster_tick(self.ns)
        for reg in (standalone, archived):
            self.assertEqual((self.themed(reg)[0]["status"], len(self.themed(reg)[1])), ("completed", 1))
        self.assertEqual(path.read_bytes(), data)
        self.assertEqual(archive_path.read_bytes(), wrapped.getvalue())


class SavedCardContext(DatabaseFixture, unittest.IsolatedAsyncioTestCase):
    """Receipt-backed saved-doc rechecks must use the same current notice as intake."""

    def setUp(self):
        super().setUp()
        import v341_eis_facts as facts
        import v348_cross_document_legal as cross
        import v372_auto_regulation_protocols as protocols
        with self.connect() as con:
            for declaration in ('source TEXT', 'url TEXT', 'customer_inn TEXT', 'price TEXT', 'price_num REAL', 'submission_start TEXT',
                                'deadline TEXT', 'card_fields_json TEXT'):
                con.execute('ALTER TABLE purchases ADD COLUMN ' + declaration)
        self.observed = []
        self.observed_checks = []
        self.ns['analyze'] = lambda p, docs: self.observed.append(deepcopy(p)) or []
        self.ns['build_checks'] = lambda p, detail, card, docs: self.observed_checks.append(
            deepcopy((p, detail, card))) or []
        from v311_verified_customer import _initialize
        _initialize(self.ns)
        facts.initialize(self.ns)
        legal.install(self.ns)
        cross.install(self.ns)
        protocols.install(self.ns)

    def soap(self, reg, *, version=1, method='Открытый конкурс',
             published='2026-10-01T10:00:00+03:00', deadline='2026-10-30',
             price='125000.00', kind='purchaseNotice', xml_reg=None):
        import eis223_soap_xml as xml_reader
        from xml.sax.saxutils import escape
        xml_reg = xml_reg or reg
        identity = (f'<registrationNumber>{xml_reg}</registrationNumber>'
                    if kind in xml_reader.NOTICE_TYPES else
                    f'<registrationNumber>{xml_reg}-01</registrationNumber>'
                    f'<purchaseInfo><purchaseNoticeNumber>{xml_reg}</purchaseNoticeNumber></purchaseInfo>')
        attachments = ''.join('<document><fileName>' + escape(item['name']) +
                              '</fileName><url>' + escape(item['url']) + '</url></document>'
                              for item in self.originals[reg])
        name = f'{kind}_{xml_reg}_{version}.xml'
        raw = (f'<{kind} xmlns="{xml_reader.PURCHASE_NS}"><{kind}Data>{identity}'
               f'<guid>notice-{version}</guid><version>{version}</version>'
               f'<name>Поставка материалов</name><purchaseCodeName>{escape(method)}</purchaseCodeName>'
               f'<publicationDateTime>{published}</publicationDateTime>'
               f'<applSubmisionStartDate>2026-10-01</applSubmisionStartDate>'
               f'<submissionCloseDateTime>{deadline}</submissionCloseDateTime>'
               f'<lots><lot><lotData><initialSum>{price}</initialSum></lotData></lot></lots>'
               f'<attachments>{attachments}</attachments></{kind}Data></{kind}>').encode()
        record, _ = xml_reader.parse_document(raw, name)
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr(name, raw)
        data = stream.getvalue()
        digest = hashlib.sha256(data).hexdigest()
        folder = self.folder / 'eis223_official_archive_inbox' / reg
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (digest + '.zip')
        path.write_bytes(data)
        receipt = folder / (digest + '.json')
        receipt.write_text(json.dumps({'reg_number': reg, 'source': 'official_eis_soap_archive',
                                      'archive_sha256': digest, 'notices': [record]}))
        return path, receipt

    def stale_purchase(self, reg, **values):
        fields = {'method': '', 'published_at': '', 'submission_start': '',
                  'submission_end': '', 'deadline': '', 'price': '', 'price_num': None,
                  'card_fields_json': '{}', **values}
        with self.connect() as con:
            con.execute('UPDATE purchases SET ' + ','.join(key + '=?' for key in fields) +
                        ' WHERE reg_number=?', (*fields.values(), reg))

    async def source(self, reg):
        _, rows = general.existing(self.ns, reg)
        return await general.collect_saved_evidence(self.ns, reg, rows)

    async def assert_both(self, reg, expected):
        first = await general.inspect_one(self.ns, reg)
        selected = await thematic.independent_review(self.ns, reg, ['dates'])
        self.assertEqual(first[0], 'completed')
        self.assertEqual(self.findings(reg), expected)
        self.assertEqual(len(selected[1]), expected)
        self.assertEqual((await general.inspect_one(self.ns, reg))[1]['new_findings'], 0)
        return first, selected

    async def test_all_persisted_originals_receive_same_context_without_second_extraction(self):
        reg = self.purchase()
        self.stale_purchase(reg)
        self.soap(reg)
        before = {path: path.read_bytes() for path in self.folder.rglob('*')
                  if path.is_file() and path.suffix in ('.docx', '.zip', '.json')}
        # This helper reads original bytes only in the supplemental collector.
        # stored_docs still performs its own receipt/hash authentication.
        with patch.object(incoming, '_read_original', side_effect=AssertionError('duplicate original read')):
            source = await self.source(reg)
            first, selected = await self.assert_both(reg, 1)
        self.assertFalse(source['source_incomplete'])
        self.assertEqual(source['original_received'], 1)
        self.assertEqual(source['p']['method'], 'Открытый конкурс')
        self.assertEqual(source['p']['price_num'], 125000)
        self.assertEqual(source['card']['summary']['submission_end'], '2026-10-30')
        self.assertEqual(source['detail']['purchaseMethodName'], 'Открытый конкурс')
        self.assertEqual(first[1]['new_findings'], 1)
        self.assertEqual(selected[1][0]['code'], '223-3.2-3-CLARIFICATION-RESPONSE-LATE')
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        with self.connect() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM docs').fetchone()[0], 1)

    async def test_persisted_and_receipt_only_paths_have_identical_legal_result(self):
        reg = self.purchase()
        self.stale_purchase(reg)
        self.soap(reg)
        _, selected = await self.assert_both(reg, 1)
        with self.connect() as con:
            con.execute("UPDATE docs SET status='downloaded' WHERE reg_number=?", (reg,))
        # No source bytes or source metadata changed, only docs readiness.
        self.stale_purchase(reg)
        result = await thematic.independent_review(self.ns, reg, ['dates'])
        self.assertEqual(result, selected)
        self.assertEqual((await general.inspect_one(self.ns, reg))[1]['new_findings'], 0)

    async def test_latest_explicit_revision_replaces_stale_price_publication_method_and_deadline(self):
        reg = self.purchase()
        self.stale_purchase(reg, method='Закупка у единственного поставщика',
                            published_at='2018-10-01', submission_end='2026-09-01',
                            price='999.00', price_num=999)
        self.soap(reg, version=2, price='250000.00')
        # Written later but explicitly older: never select by file/hash order.
        self.soap(reg, version=1, method='Закупка у единственного поставщика',
                  published='2018-10-01', deadline='2026-09-01', price='999.00')
        await self.assert_both(reg, 1)
        self.assertTrue(self.observed)
        for purchase in self.observed:
            self.assertEqual((purchase['method'], purchase['published'][:10],
                              purchase['submission_end'], purchase['price_num']),
                             ('Открытый конкурс', '2026-10-01', '2026-10-30', 250000))

    async def test_explicit_zero_price_does_not_keep_stale_nonzero_number(self):
        reg = self.purchase()
        self.stale_purchase(reg, price='999.00', price_num=999)
        self.soap(reg, price='0.00')
        await self.assert_both(reg, 1)
        self.assertTrue(all(p['price'] == '0.00' and p['price_num'] == 0 for p in self.observed))

    async def test_latest_noncompetitive_notice_cannot_inherit_old_competitive_scope(self):
        reg = self.purchase()
        self.soap(reg, version=1)
        self.soap(reg, version=2, method='Закупка у единственного поставщика')
        await self.assert_both(reg, 0)
        self.assertTrue(all(p['method'] == 'Закупка у единственного поставщика' for p in self.observed))

    async def test_legacy_publication_from_current_notice_stays_outside_modern_rule(self):
        reg = self.purchase()
        self.soap(reg, version=2, published='2018-10-01')
        await self.assert_both(reg, 0)
        self.assertTrue(all(p['published'].startswith('2018-') for p in self.observed))

    async def test_generic_document_word_does_not_supply_unknown_method(self):
        reg = self.purchase()
        self.document(reg, 'Общий раздел: конкурс, аукцион, запрос предложений проводятся по положению.')
        self.stale_purchase(reg)
        self.soap(reg, method='')
        await self.assert_both(reg, 0)
        self.assertTrue(all(not p['method'] for p in self.observed))

    async def test_tampered_or_foreign_soap_does_not_supply_context(self):
        for number, mode in enumerate(('bytes', 'receipt_hash', 'foreign_registry'), 1):
            with self.subTest(mode=mode):
                reg = self.purchase(number)
                self.stale_purchase(reg)
                path, receipt = self.soap(reg, xml_reg='32619999999' if mode == 'foreign_registry' else reg)
                if mode == 'bytes':
                    path.write_bytes(path.read_bytes() + b'tampered')
                elif mode == 'receipt_hash':
                    value = json.loads(receipt.read_text())
                    value['notices'][0]['xml_sha256'] = '0' * 64
                    receipt.write_text(json.dumps(value))
                await self.assert_both(reg, 0)
                self.assertFalse((await self.source(reg))['p'].get('method'))

    async def test_protocol_fields_cannot_replace_current_notice_context(self):
        reg = self.purchase()
        self.stale_purchase(reg)
        self.soap(reg, kind='purchaseProtocol')
        await self.assert_both(reg, 0)
        self.assertTrue(all(not p['method'] for p in self.observed))

    async def test_context_read_failure_remains_retryable_with_readable_documents(self):
        reg = self.purchase()
        self.stale_purchase(reg)
        self.soap(reg)
        with patch.object(general, 'saved_review_context',
                          new=AsyncMock(side_effect=asyncio.TimeoutError), create=True) as read_context:
            source = await self.source(reg)
        read_context.assert_awaited_once()
        self.assertTrue(source['source_incomplete'])
        self.assertEqual(len(source['docs']), 1)
        self.assertEqual(source['p'], {})


    def card_snapshot(self, reg, *, captured='2026-09-01T10:00:00+03:00', fields=None, price=None):
        source = 'https://zakupki.gov.ru/223/purchase/public/purchase/info/common-info.html?regNumber=' + reg
        payload = {'reg_number': reg, 'source_page': source, 'captured_at': captured,
                   'fields': fields or {}, 'field_evidence': {key: str(value) for key, value in (fields or {}).items()},
                   'prices': ([{'price': price, 'price_num': float(price), 'price_kind': 'nmcd',
                                'price_label': 'НМЦД', 'currency': 'RUB'}] if price is not None else [])}
        with self.connect() as con:
            con.execute('INSERT OR REPLACE INTO v341_card_facts VALUES(?,?,?,?)',
                        (reg, source, captured, json.dumps(payload, ensure_ascii=False)))
        return payload

    def price_snapshot(self, reg, *, captured, price='999.00'):
        import v338_card_prices as prices
        prices.initialize(self.ns)
        source = 'https://zakupki.gov.ru/223/purchase/public/purchase/info/common-info.html?regNumber=' + reg
        with self.connect() as con:
            con.execute('INSERT OR REPLACE INTO v338_card_price(reg,source_page,captured_at,price,price_num,'
                        'price_kind,price_label,conflict,alternative_price) VALUES(?,?,?,?,?,?,?,0,?)',
                        (reg, source, captured, price, float(price), 'nmcd', 'НМЦД', ''))

    def snapshots(self):
        with self.connect() as con:
            return [tuple(row) for row in con.execute('SELECT * FROM v341_card_facts ORDER BY reg')]

    async def test_demonstrably_older_card_cannot_override_current_soap_fields(self):
        reg = self.purchase()
        self.soap(reg)
        self.card_snapshot(reg, fields={'method': 'Закупка у единственного поставщика',
            'published': '2018-10-01', 'submission_start': '2018-10-01', 'submission_end': '2018-10-30'},
            price='999.00')
        before = self.snapshots()
        await self.assert_both(reg, 1)
        for p in self.observed:
            self.assertEqual((p['method'], p['published'][:10], p['submission_end'], p['price_num']),
                             ('Открытый конкурс', '2026-10-01', '2026-10-30', 125000))
            self.assertEqual(p['fact_evidence']['method']['source'], 'official_eis_xml')
            self.assertEqual(p['_official_context_conflicts'], {})
        self.assertEqual(self.snapshots(), before)

    async def test_current_noncompetitive_soap_defeats_older_competitive_card(self):
        reg = self.purchase()
        self.soap(reg, method='Закупка у единственного поставщика')
        self.card_snapshot(reg, fields={'method': 'Открытый конкурс'})
        await self.assert_both(reg, 0)
        self.assertTrue(all(p['method'] == 'Закупка у единственного поставщика' for p in self.observed))

    async def test_later_undated_and_same_day_imprecise_conflicts_remain_review(self):
        for number, captured in enumerate(('2026-10-02T10:00:00+03:00', '', 'invalid',
                                           '2026-10-01', '2026-10-01T09:00:00'), 1):
            with self.subTest(captured=captured):
                reg = self.purchase(number)
                self.soap(reg)
                self.card_snapshot(reg, captured=captured,
                                   fields={'method': 'Закупка у единственного поставщика'})
                before = self.snapshots()
                await self.assert_both(reg, 0)
                observed = [p for p in self.observed if p['reg'] == reg]
                self.assertTrue(all(not p['method'] and 'method' in p['_official_context_conflicts'] for p in observed))
                with self.connect() as con:
                    self.assertTrue(con.execute("SELECT 1 FROM checks WHERE reg_number=? AND status='check' "
                                                "AND field_path='v341-clarification-applicability'", (reg,)).fetchone())
                self.assertEqual(self.snapshots(), before)
                for p, detail, card in self.observed_checks:
                    if p['reg'] != reg:continue
                    self.assertFalse(detail['purchaseMethodName'])
                    self.assertFalse(card['summary']['method'])
                    self.assertFalse(p['card_fields_json']['summary']['method'])

    async def test_publication_only_conflict_blocks_modern_applicability(self):
        reg = self.purchase()
        self.soap(reg, published='2018-10-01T10:00:00+03:00')
        self.card_snapshot(reg, captured='2026-10-02T10:00:00+03:00',
                           fields={'published': '2026-10-01'})
        await self.assert_both(reg, 0)
        self.assertTrue(all(not p['method'] and 'published' in p['_official_context_conflicts'] for p in self.observed))

    async def test_deadline_conflict_cannot_fall_back_to_stored_deadline(self):
        reg = self.purchase()
        self.soap(reg)
        self.card_snapshot(reg, captured='2026-10-02T10:00:00+03:00',
                           fields={'submission_end': '2026-11-20'})
        await self.assert_both(reg, 0)
        self.assertTrue(all(not p['submission_end'] and not p['deadline'] for p in self.observed))

    async def test_agreeing_later_card_does_not_block_verified_context(self):
        reg = self.purchase()
        self.soap(reg)
        self.card_snapshot(reg, captured='2026-10-02T10:00:00+03:00',
            fields={'method': 'Открытый конкурс', 'published': '01.10.2026', 'submission_end': '30.10.2026'},
            price='125000.00')
        await self.assert_both(reg, 1)
        self.assertTrue(all(not p['_official_context_conflicts'] for p in self.observed))

    async def test_v338_price_uses_its_own_capture_provenance(self):
        for number, direct_time, price_time, expected in (
            (1, '2026-09-01T10:00:00+03:00', '2026-10-02T10:00:00+03:00', None),
            (2, '2026-10-02T10:00:00+03:00', '2026-09-01T10:00:00+03:00', 125000),
            (3, '2026-09-01T10:00:00+03:00', '', None),
        ):
            with self.subTest(expected=expected, direct_time=direct_time, price_time=price_time):
                reg = self.purchase(number)
                self.soap(reg)
                self.card_snapshot(reg, captured=direct_time, fields={'method': 'Открытый конкурс'})
                self.price_snapshot(reg, captured=price_time)
                with self.connect() as con:
                    before = [tuple(row) for row in con.execute('SELECT * FROM v338_card_price ORDER BY reg')]
                # Price uncertainty alone does not disable unrelated date rules.
                await self.assert_both(reg, 1)
                observed = [p for p in self.observed if p['reg'] == reg]
                self.assertTrue(all(p['price_num'] == expected for p in observed))
                self.assertTrue(all(('price' in p['_official_context_conflicts']) == (expected is None) for p in observed))
                with self.connect() as con:
                    self.assertEqual([tuple(row) for row in con.execute('SELECT * FROM v338_card_price ORDER BY reg')], before)

    async def test_unordered_price_does_not_repopulate_from_original_or_saved_evidence(self):
        import v336_legal_matrix as matrix
        reg = self.purchase()
        self.document(reg, 'НМЦД: 125000 рублей. Обеспечение заявки: 1000 рублей.')
        # Give the price clause its ordinary documentation identity, rather
        # than allowing this fixture's default clarification filename to hide it.
        with self.connect() as con:
            con.execute("UPDATE docs SET name='Документация.docx' WHERE id=(SELECT MAX(id) FROM docs)")
        self.soap(reg)
        self.card_snapshot(reg, captured='2026-10-02T10:00:00+03:00', price='999.00')
        card_source = self.snapshots()[0][1]
        prior = {'previous_prices': [{'price': '500.00'}],
                 'price_evidence': {'source': 'official_eis_card', 'price': '999.00',
                                   'price_kind': 'nmcd', 'source_url': card_source}}
        with self.connect() as con:
            con.execute('UPDATE purchases SET price=?,price_num=?,card_fields_json=? WHERE reg_number=?',
                        ('999.00', 999, json.dumps(prior), reg))
        source = await self.source(reg)
        p = source['p']
        import v341_eis_facts as facts
        for _ in range(2):
            facts.enrich(self.ns, p, source['docs'], source['card'], source['detail'])
            self.assertIsNone(p['price_num'])
            self.assertNotIn('verified_price_source', p)
            self.assertFalse(source['detail']['initialSum'])
            self.assertIsNone(source['card']['summary']['price_num'])
            self.assertIsNone(p['card_fields_json']['summary']['price_num'])
            self.assertIsNone(matrix.verified_price(p, source['docs']))
            findings, checks = matrix.security_findings(p, source['docs'])
            self.assertEqual(findings, [])
            self.assertTrue(any(c['field_path'] == 'v336-bid-security-needs-verified-nmcd' for c in checks))
        await self.assert_both(reg, 1)  # Only the independent, proven date event.
        with self.connect() as con:
            saved = json.loads(con.execute('SELECT card_fields_json FROM purchases WHERE reg_number=?', (reg,)).fetchone()[0])
        self.assertEqual(saved['previous_prices'], prior['previous_prices'])
        self.assertEqual(saved['price_evidence'], prior['price_evidence'])

    async def test_semantic_context_relation_changes_once_without_capture_replay_churn(self):
        reg = self.purchase()
        self.soap(reg)
        def version():
            with self.connect() as con:
                return sources.semantic_source_version(con, reg)
        self.card_snapshot(reg, fields={'method': 'Закупка у единственного поставщика'})
        before = version()
        self.card_snapshot(reg, captured='2026-09-02T10:00:00+03:00',
                           fields={'method': 'Закупка у единственного поставщика'})
        self.assertEqual(version(), before)
        self.card_snapshot(reg, captured='2026-10-02T10:00:00+03:00',
                           fields={'method': 'Закупка у единственного поставщика'})
        conflict = version()
        self.assertNotEqual(conflict, before)
        self.card_snapshot(reg, captured='2026-10-03T10:00:00+03:00',
                           fields={'method': 'Закупка у единственного поставщика'})
        self.assertEqual(version(), conflict)
        await self.assert_both(reg, 0)
        self.assertEqual(version(), conflict)

    async def test_source_labelled_blank_purchase_has_semantic_fixed_point(self):
        for number, with_card in enumerate((False, True), 1):
            with self.subTest(with_card=with_card):
                reg = self.purchase(number)
                source_url = 'https://zakupki.gov.ru/223/purchase/public/purchase/info/common-info.html?regNumber=' + reg
                self.stale_purchase(reg, source='official_eis_getDocsIP', url=source_url)
                self.soap(reg)
                if with_card:
                    self.card_snapshot(reg, fields={'method': 'Закупка у единственного поставщика'})
                def version():
                    with self.connect() as con:
                        return sources.semantic_source_version(con, reg)
                before = version()
                await self.assert_both(reg, 1)
                self.assertEqual(version(), before)
                with self.connect() as con:
                    row = con.execute('SELECT method,published_at,submission_start,submission_end FROM purchases WHERE reg_number=?',
                                      (reg,)).fetchone()
                self.assertEqual(tuple(row), ('', '', '', ''))

    def test_capture_order_uses_actual_instants_and_requires_precision(self):
        from v341_eis_facts import card_predates_notice
        notice = {'publication': '2026-10-01T10:00:00+03:00', 'modified': '2026-10-10T10:00:00+03:00'}
        for captured, expected in (
            ('2026-10-01T06:59:59+00:00', True),
            ('2026-10-01T08:00:00+01:00', False),
            ('2026-10-01T09:00:00+00:00', False),
            ('2026-10-02T10:00:00+03:00', False),
            ('2026-10-01', False), ('2026-10-01T06:00:00', False), ('', False), ('invalid', False),
        ):
            with self.subTest(captured=captured):
                self.assertIs(card_predates_notice(captured, notice), expected)
        self.assertFalse(card_predates_notice('2026-10-01T00:00:00+00:00', {'publication': '2026-10-01'}))


if __name__ == "__main__":
    unittest.main()
