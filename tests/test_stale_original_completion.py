"""Exact-source recovery fixes stale pointers, never missing legal evidence."""
import asyncio
import json
import unittest
from unittest.mock import patch

from test_general_audit_recovery import DatabaseFixture, LATE_REPLY, URL
import test_full_recheck_runtime as recheck_fixtures
import full_recheck_runtime as runtime
import v332_all_procurements_audit as general
import v342_thematic_queue as thematic


class StaleOriginalCompletion(DatabaseFixture, unittest.IsolatedAsyncioTestCase):
    activate = recheck_fixtures.FullRecheckRuntime.activate
    member = recheck_fixtures.FullRecheckRuntime.member

    def stale_purchase(self, number=1):
        reg = self.purchase(number)
        path = next(path for path in self.contents if reg in path.parts)
        stale = self.folder / "old-files" / (reg + ".docx")
        with self.connect() as db:
            db.execute("UPDATE purchases SET customer='ГБУ Учреждение' WHERE reg_number=?", (reg,))
            db.execute("UPDATE docs SET local_path=? WHERE reg_number=?", (str(stale), reg))
        return reg, path, stale

    def rows(self, reg):
        with self.connect() as db:
            return [tuple(row) for row in db.execute("SELECT * FROM docs WHERE reg_number=? ORDER BY id", (reg,))]

    async def collect(self, reg):
        _, rows = general.existing(self.ns, reg)
        return await general.collect_saved_evidence(self.ns, reg, rows, reconcile_stale=True)

    async def begin(self):
        general.initialize(self.ns)
        self.activate()
        self.assertTrue(await runtime.run_one(self.ns, {}), self.output.getvalue())

    async def test_same_uid_and_content_complete_without_replacing_rows_or_history(self):
        reg, path, _ = self.stale_purchase()
        with self.connect() as db:
            db.execute("INSERT INTO findings(reg_number,code,kind,title,evidence) "
                       "VALUES(?,'HISTORICAL','violation','Retained history','Original history')", (reg,))
            history = tuple(db.execute("SELECT * FROM findings WHERE code='HISTORICAL'").fetchone())
        before, binary = self.rows(reg), path.read_bytes()
        await self.begin()
        self.assertEqual(self.member(reg)["status"], "completed", self.output.getvalue())
        self.assertEqual(self.member(reg)["documents_unreadable"], 0)
        self.assertEqual(self.member(reg)["verified_original_documents"], 1)
        self.assertEqual(self.findings(reg), 2)
        self.assertIsNone(self.row(reg))  # Explicit noncompany scope stays isolated.
        self.assertEqual(self.rows(reg), before)
        self.assertEqual(path.read_bytes(), binary)
        with self.connect() as db:
            self.assertEqual(tuple(db.execute("SELECT * FROM findings WHERE code='HISTORICAL'").fetchone()), history)
        self.assertFalse(await runtime.run_one(self.ns, {}))

    async def test_duplicate_stale_rows_are_reconciled_individually(self):
        reg, _, _ = self.stale_purchase()
        with self.connect() as db:
            db.execute("INSERT INTO docs(reg_number,name,url,doc_type,local_path,status,metadata_json,final_url) "
                       "SELECT reg_number,name,url,doc_type,local_path,status,metadata_json,final_url "
                       "FROM docs WHERE reg_number=?", (reg,))
        before = self.rows(reg)
        source = await self.collect(reg)
        self.assertEqual((len(source["docs"]), source["unreadable"]), (1, 0))
        await self.begin()
        self.assertEqual(self.member(reg)["status"], "completed")
        self.assertEqual(self.findings(reg), 1)
        self.assertEqual(self.rows(reg), before)

    async def test_valid_persisted_original_also_recovers_same_stale_row(self):
        reg, path, _ = self.stale_purchase()
        with self.connect() as db:
            db.execute("INSERT INTO docs(reg_number,name,url,doc_type,local_path,status,metadata_json,final_url) "
                       "SELECT reg_number,name,url,doc_type,?,status,metadata_json,final_url FROM docs "
                       "WHERE reg_number=?", (str(path), reg))
        with patch.object(general, "offline_audit", side_effect=AssertionError("Reuse authenticated stored input")):
            source = await self.collect(reg)
        self.assertEqual((len(source["docs"]), source["unreadable"]), (1, 0))
        await self.begin()
        self.assertEqual(self.member(reg)["status"], "completed")
        self.assertEqual(self.findings(reg), 1)

    async def test_wrong_version_identity_or_path_never_reconciles(self):
        cases = ("old_sha", "other_uid", "uid_conflict", "row_url_conflict", "unverified_source",
                 "missing_sha", "missing_uid", "malformed_meta", "outside_root", "unsupported_suffix")
        for number, case in enumerate(cases, 1):
            with self.subTest(case=case):
                reg, _, _ = self.stale_purchase(number)
                _, rows = general.existing(self.ns, reg)
                meta = json.loads(rows[0]["metadata_json"])
                values = {}
                if case == "old_sha": meta["sha256"] = "0" * 64
                elif case == "other_uid":
                    meta["uid"] = "F" * 32
                    meta["source_url"] = URL + meta["uid"]
                    values["url"] = meta["source_url"]
                elif case == "uid_conflict": meta["uid"] = "F" * 32
                elif case == "row_url_conflict": values["url"] = URL + "F" * 32
                elif case == "unverified_source": meta["source"] = "existing_local_unverified"
                elif case == "missing_sha": meta.pop("sha256")
                elif case == "missing_uid": meta.pop("uid")
                elif case == "outside_root": values["local_path"] = "/missing-external-evidence/old.docx"
                elif case == "unsupported_suffix": values["local_path"] = str(self.folder / "missing.png")
                values["metadata_json"] = "[]" if case == "malformed_meta" else json.dumps(meta)
                with self.connect() as db:
                    db.execute("UPDATE docs SET " + ",".join(key + "=?" for key in values) + " WHERE reg_number=?",
                               (*values.values(), reg))
                source = await self.collect(reg)
                self.assertEqual(source["unreadable"], 1)
                self.assertEqual(sum(general.is_official(d) for d in source["docs"]), 1)
        general.initialize(self.ns)
        self.activate()
        for reg in self.regs:
            self.assertTrue(await runtime.run_one(self.ns, {}))
            self.assertEqual((self.member(reg)["status"], self.member(reg)["reason"]),
                             ("retry", "unreadable_documents"))

    async def test_different_readable_source_does_not_cover_missing_original(self):
        reg, path, _ = self.stale_purchase()
        path.unlink()
        self.document(reg, "Другой читаемый документ закупки")
        source = await self.collect(reg)
        self.assertEqual(source["unreadable"], 1)
        self.assertEqual((source["original_expected"], source["original_received"]), (2, 1))
        await self.begin()
        self.assertEqual((self.member(reg)["status"], self.member(reg)["reason"]),
                         ("waiting_originals", "missing_originals"))

    async def test_missing_receipt_and_tampered_original_never_complete(self):
        for number, case in enumerate(("missing_receipt", "tamper"), 1):
            reg, path, _ = self.stale_purchase(number)
            if case == "missing_receipt":
                (path.parent / "receipt.json").unlink()
            else:
                data = path.read_bytes()
                path.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
            source = await self.collect(reg)
            self.assertEqual(source["unreadable"], 1)
            self.assertFalse(any(general.is_official(d) for d in source["docs"]))
        general.initialize(self.ns)
        self.activate()
        for reg in self.regs:
            self.assertTrue(await runtime.run_one(self.ns, {}))
            self.assertNotEqual(self.member(reg)["status"], "completed")
            self.assertEqual(self.findings(reg), 0)

    async def test_partial_other_source_still_retries_after_exact_recovery(self):
        reg, _, _ = self.stale_purchase()
        bad = self.document(reg, "fixture")
        data = bad.read_bytes()
        bad.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
        source = await self.collect(reg)
        self.assertEqual(source["unreadable"], 1)
        self.assertTrue(source["source_incomplete"])
        self.assertEqual(sum(general.is_official(d) for d in source["docs"]), 1)
        await self.begin()
        self.assertNotEqual(self.member(reg)["status"], "completed")

    async def test_present_empty_document_still_blocks_after_exact_recovery(self):
        reg, _, _ = self.stale_purchase()
        self.document(reg, "")
        source = await self.collect(reg)
        self.assertEqual(source["unreadable"], 1)
        self.assertTrue(source["source_incomplete"])
        self.assertEqual(sum(general.is_official(d) for d in source["docs"]), 1)
        await self.begin()
        self.assertEqual((self.member(reg)["status"], self.member(reg)["reason"]),
                         ("retry", "unreadable_documents"))
        self.assertEqual(self.findings(reg), 1)

    async def test_general_credit_keeps_thematic_recovery_conservative(self):
        reg, _, _ = self.stale_purchase()
        with self.connect() as db:
            db.execute("UPDATE purchases SET customer='ООО Тест' WHERE reg_number=?", (reg,))
        state, findings = await thematic.independent_review(self.ns, reg, ["dates"])
        self.assertEqual((state, len(findings)), ("retry", 1))
        general.initialize(self.ns)
        self.activate()
        claimed, state, detail = await recheck_fixtures.FullRecheckRuntime.ordinary(self)
        self.assertEqual((state, detail["new_findings"], detail["documents_unreadable"]), ("completed", 1, 0))
        self.assertEqual(self.member(reg)["status"], "completed")
        self.assertEqual(self.row(reg)["state"], "completed")
        await runtime.release(self.ns, claimed)

    async def test_present_file_hash_failure_is_not_a_stale_pointer(self):
        reg = self.purchase()
        with self.connect() as db:
            row = db.execute("SELECT metadata_json FROM docs WHERE reg_number=?", (reg,)).fetchone()
            meta = json.loads(row[0]); meta["sha256"] = "0" * 64
            db.execute("UPDATE docs SET metadata_json=? WHERE reg_number=?", (json.dumps(meta), reg))
        source = await self.collect(reg)
        self.assertEqual((len(source["docs"]), source["unreadable"]), (1, 1))

    async def test_readable_copy_disappearing_later_cannot_cancel_other_missing_row(self):
        reg = self.purchase()
        original = next(iter(self.contents))
        copy = self.folder / "old-readable.docx"
        copy.write_bytes(original.read_bytes())
        with self.connect() as db:
            db.execute("UPDATE docs SET local_path=? WHERE reg_number=?", (str(copy), reg))
            db.execute("INSERT INTO docs(reg_number,name,url,local_path,status,metadata_json) "
                       "VALUES(?,'Other','',?,'checked','{}')", (reg, str(self.folder / "other-missing.docx")))
        real_collect = general.offline_audit
        async def removing_copy(*args, **kwargs):
            copy.unlink()
            return await real_collect(*args, **kwargs)
        with patch.object(general, "offline_audit", side_effect=removing_copy):
            source = await self.collect(reg)
        self.assertEqual((len(source["docs"]), source["unreadable"]), (1, 1))

    async def test_timeout_is_not_recorded_as_missing_path(self):
        reg, _, _ = self.stale_purchase()
        _, rows = general.existing(self.ns, reg)
        missing = []
        with patch.object(general, "run_file_io", side_effect=asyncio.TimeoutError):
            self.assertEqual(await general.stored_docs(self.ns, reg, rows, missing_rows=missing), ([], 1))
        self.assertEqual(missing, [])

    async def test_broken_symlinks_and_non_directory_ancestors_remain_blocked(self):
        for number, case in enumerate(("symlink", "ancestor_symlink", "not_directory"), 1):
            with self.subTest(case=case):
                reg, _, stale = self.stale_purchase(number)
                stale.parent.mkdir(exist_ok=True)
                if case == "symlink":
                    stale.symlink_to(self.folder / "missing-target.docx")
                else:
                    ancestor = self.folder / ("ancestor-" + str(number))
                    if case == "ancestor_symlink":
                        ancestor.symlink_to(self.folder / "missing-target-directory")
                    else:
                        ancestor.write_bytes(b"not a directory")
                    with self.connect() as db:
                        db.execute("UPDATE docs SET local_path=? WHERE reg_number=?",
                                   (str(ancestor / "child.docx"), reg))
                source = await self.collect(reg)
                self.assertEqual((len(source["docs"]), source["unreadable"]), (1, 1))

    async def test_new_file_at_missing_path_is_not_silently_forgiven(self):
        reg, original, stale = self.stale_purchase()
        real_collect = general.offline_audit
        async def arriving_copy(*args, **kwargs):
            stale.parent.mkdir()
            stale.write_bytes(original.read_bytes())
            return await real_collect(*args, **kwargs)
        with patch.object(general, "offline_audit", side_effect=arriving_copy):
            source = await self.collect(reg)
        self.assertEqual((len(source["docs"]), source["unreadable"]), (1, 1))

    async def test_receipt_or_alias_without_allowed_source_never_reconciles(self):
        reg, original, _ = self.stale_purchase()
        _, rows = general.existing(self.ns, reg)
        collected = await general.offline_audit(self.ns, reg, collect_only=True)
        docs = collected["docs"]
        uid = json.loads(rows[0]["metadata_json"])["uid"]
        allowed = general.allowed_documents(reg)
        self.assertEqual(general._recovered_stale_original_rows(reg, rows, docs, {}, {uid}), ())
        self.assertEqual(general._recovered_stale_original_rows(reg, rows, docs, allowed, set()), ())
        # A checked source with a forged stored alias is not that alias's own
        # receipt-backed candidate, even when the bytes happen to match.
        fake = {**docs[0], "meta": {**docs[0]["meta"], "source": "existing_local_unverified",
                                   "identical_sources": [docs[0]]}}
        self.assertEqual(general._recovered_stale_original_rows(reg, rows, [fake], allowed, {uid}), ())
        (original.parent / "receipt.json").unlink()
        self.assertEqual(general._recovered_stale_original_rows(reg, rows, docs, allowed, {uid}), ())

    async def test_default_collector_keeps_conservative_completeness_and_same_evidence(self):
        reg, _, _ = self.stale_purchase()
        _, rows = general.existing(self.ns, reg)
        conservative = await general.collect_saved_evidence(self.ns, reg, rows)
        reconciled = await general.collect_saved_evidence(self.ns, reg, rows, reconcile_stale=True)
        self.assertEqual(conservative["docs"], reconciled["docs"])
        self.assertEqual((conservative["unreadable"], reconciled["unreadable"]), (1, 0))
        self.assertEqual(conservative["_reconciled_rows"], ())
        self.assertEqual(len(reconciled["_reconciled_rows"]), 1)

    async def test_changed_stale_row_before_writer_cannot_commit_new_receipt_or_findings(self):
        cases = ("delete", "replacement_id", "replacement_uid", "new_hash", "new_pointer",
                 "metadata_reformat", "new_status", "new_url")
        purchases = [(case, self.stale_purchase(number)[0]) for number, case in enumerate(cases, 1)]
        with self.connect() as db:
            for _, reg in purchases:
                db.execute("INSERT INTO findings(reg_number,code,kind,title,evidence) "
                           "VALUES(?,'HISTORICAL','violation','Retained history','Original history')", (reg,))
            history = [tuple(row) for row in db.execute("SELECT * FROM findings ORDER BY id")]
        general.initialize(self.ns)
        self.activate()
        real_persist = general._persist_reserved
        for case, reg in purchases:
            with self.subTest(case=case):
                calls = []
                def racing_persist(ns, claimed, findings, checks, token=None, reconciled_rows=()):
                    self.assertEqual(str(claimed), reg)
                    self.assertEqual(len(reconciled_rows), 1)
                    with self.connect() as db:
                        row = db.execute("SELECT * FROM docs WHERE reg_number=?", (reg,)).fetchone()
                        if case == "delete":
                            db.execute("DELETE FROM docs WHERE id=?", (row["id"],))
                        elif case == "replacement_id":
                            db.execute("UPDATE docs SET id=id+10000 WHERE id=?", (row["id"],))
                        elif case in ("replacement_uid", "new_hash", "metadata_reformat"):
                            meta = json.loads(row["metadata_json"])
                            if case == "replacement_uid":
                                meta.update(uid="F" * 32, source_url=URL + "F" * 32)
                            elif case == "new_hash":
                                meta["sha256"] = "0" * 64
                            db.execute("UPDATE docs SET metadata_json=? WHERE id=?",
                                       (json.dumps(meta, indent=2), row["id"]))
                        elif case == "new_pointer":
                            db.execute("UPDATE docs SET local_path=local_path||'.new' WHERE id=?", (row["id"],))
                        elif case == "new_status":
                            db.execute("UPDATE docs SET status='downloaded_no_text' WHERE id=?", (row["id"],))
                        else:
                            db.execute("UPDATE docs SET url=? WHERE id=?", (URL + "F" * 32, row["id"]))
                    calls.append(case)
                    return real_persist(ns, claimed, findings, checks, token, reconciled_rows)
                with patch.object(general, "_persist_reserved", side_effect=racing_persist):
                    self.assertTrue(await runtime.run_one(self.ns, {}))
                self.assertEqual(calls, [case])
                member = self.member(reg)
                self.assertEqual((member["status"], member["reason"]), ("retry", "interrupted_attempt"))
                self.assertEqual((member["full_verified_read"], member["result_token"]), (0, ""))
                self.assertEqual(self.findings(reg), 1)
                self.assertIsNone(self.row(reg))
        with self.connect() as db:
            self.assertEqual([tuple(row) for row in db.execute("SELECT * FROM findings ORDER BY id")], history)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM full_recheck_receipts").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM checks").fetchone()[0], 0)

    async def test_row_changed_during_collection_is_not_credited(self):
        reg, _, _ = self.stale_purchase()
        real_collect = general.offline_audit
        async def changing_metadata(*args, **kwargs):
            result = await real_collect(*args, **kwargs)
            with self.connect() as db:
                row = db.execute("SELECT metadata_json FROM docs WHERE reg_number=?", (reg,)).fetchone()
                meta = json.loads(row[0]); meta["sha256"] = "0" * 64
                db.execute("UPDATE docs SET metadata_json=? WHERE reg_number=?", (json.dumps(meta), reg))
            return result
        with patch.object(general, "offline_audit", side_effect=changing_metadata):
            await self.begin()
        self.assertEqual(self.member(reg)["status"], "retry")
        self.assertEqual(self.member(reg)["full_verified_read"], 0)
        self.assertEqual(self.findings(reg), 0)

    async def test_row_check_and_credit_share_writer_transaction(self):
        import sqlite3
        reg, _, _ = self.stale_purchase()
        real_guard = general._require_reconciled_rows
        protected = []
        def checking_guard(db, claimed, rows):
            self.assertTrue(db.in_transaction)
            # The fence needs only materialized DB values, never filesystem,
            # receipt reads or metadata JSON parsing under the writer.
            with patch.object(general, "saved_document", side_effect=AssertionError("No receipt I/O")), \
                 patch.object(general.json, "loads", side_effect=AssertionError("No JSON parsing")):
                real_guard(db, claimed, rows)
            other = self.connect()
            try:
                other.execute("PRAGMA busy_timeout=0")
                with self.assertRaisesRegex(sqlite3.OperationalError, "locked"):
                    other.execute("UPDATE docs SET local_path=local_path||'.race' WHERE reg_number=?", (reg,))
            finally:
                other.close()
            protected.append(True)
        with patch.object(general, "_require_reconciled_rows", side_effect=checking_guard):
            await self.begin()
        self.assertEqual(protected, [True])
        self.assertEqual(self.member(reg)["status"], "completed")
        self.assertEqual(self.findings(reg), 1)

    async def test_durable_receipt_survives_interrupted_ack(self):
        reg, _, _ = self.stale_purchase()
        general.initialize(self.ns)
        self.activate()
        with patch.object(runtime, "finish_result", side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                await runtime.run_one(self.ns, {})
        self.assertEqual((self.member(reg)["status"], self.member(reg)["full_verified_read"]), ("completed", 1))
        self.assertEqual(self.findings(reg), 1)
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM full_recheck_receipts WHERE reg=?", (reg,)).fetchone()[0], 1)


class StaleRecoverySignature(unittest.TestCase):
    def test_exact_source_and_atomic_row_guard_are_signature_inputs(self):
        import legal_audit_revision as revision
        revision.engine_signature.cache_clear()
        self.addCleanup(revision.engine_signature.cache_clear)
        self.assertIn("_require_reconciled_rows", revision.DEPENDENCY_REGISTRY["v332_all_procurements_audit"])
        baseline = revision.engine_signature()
        read_engine = revision._read_engine
        changes = (("if current!=rows:", "if False:"),
                   ('or (uid,sha,old.suffix.lower()) not in recovered',
                    'or False'),
                   ('"local_path","status","metadata_json","final_url")',
                    '"local_path","status","final_url")'))
        for before, after in changes:
            with self.subTest(before=before):
                revision.engine_signature.cache_clear()
                def changed(path):
                    value = read_engine(path)
                    if path.name == "v332_all_procurements_audit.py":
                        self.assertIn(before, value)
                        value = value.replace(before, after, 1)
                    return value
                with patch.object(revision, "_read_engine", changed):
                    self.assertNotEqual(baseline, revision.engine_signature())
