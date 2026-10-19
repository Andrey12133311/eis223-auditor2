"""Terminal catch-up under a replenished first-pass backlog, without new capacity."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time
import unittest
from unittest.mock import patch

from test_general_audit_recovery import DatabaseFixture
from audit_source_version import SemanticSnapshotError
import v332_all_procurements_audit as general


class GeneralRecheckFairness(DatabaseFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.sources = {}
        self.semantic = self.stack.enter_context(patch.object(
            general, 'semantic_source_version', side_effect=lambda c, reg: self.sources.get(str(reg), 'current')))
        self.engine = self.stack.enter_context(patch.object(general, 'engine_signature', return_value='engine'))
        self.receipts = self.stack.enter_context(patch.object(general, 'receipt_readiness', return_value=(False, None)))

    def add_pending(self, number, *, queue=True):
        reg = str(32619000000 + number)
        self.regs.append(reg)
        with self.connect() as c:
            c.execute("INSERT INTO purchases(reg_number,customer) VALUES(?,'ООО Тест')", (reg,))
            c.execute("INSERT INTO docs(reg_number,local_path,status) VALUES(?,'fixture','checked')", (reg,))
            if queue:
                c.execute("INSERT INTO v332_audit_queue(campaign,reg) VALUES(?,?)", (general.CAMPAIGN, reg))
        return reg

    def prepare(self, count=1):
        targets = [self.add_pending(900 + i, queue=False) for i in range(count)]
        backlog = [self.add_pending(i, queue=False) for i in range(1, 17)]
        general.initialize(self.ns)
        with self.connect() as c:
            for reg in targets:
                c.execute("""UPDATE v332_audit_queue SET state='completed',audit_revision='old',
                    last_semantic_version='old',last_engine_signature='engine',updated_at='before',
                    new_findings=7,new_checks=3,documents_read=9,last_result_token='prior',
                    last_counted_token='prior' WHERE reg=?""", (reg,))
            c.execute("CREATE TABLE preserved_history(id INTEGER PRIMARY KEY,reg TEXT,decision TEXT)")
            c.execute("INSERT INTO preserved_history VALUES(1,?,'approved')", (targets[0],))
            c.execute("INSERT INTO findings(reg_number,kind,title) VALUES(?,'violation','Prior finding')", (targets[0],))
        return targets, backlog

    def handoff(self):
        with self.connect() as c:
            value = general.scan_cursor(c, 'general-recheck-handoff:' + general.CAMPAIGN)
        return json.loads(value) if value else None

    def finish(self, claimed):
        snapshot = general.review_watermark(self.ns, claimed)
        general.mark(self.ns, claimed, 'completed', snapshot)
        return snapshot

    def ready(self):
        with self.connect() as c:
            return general.ready_work(c)

    def test_replenished_finite_backlog_makes_both_classes_progress_once(self):
        targets, backlog = self.prepare(3)
        claims = []
        for cycle in range(40):
            # Restart-like namespaces must retain handoff, scan cursor and turn.
            self.assertEqual(general.refresh_changed(dict(self.ns)), 0)
            claimed = general.claim_next(dict(self.ns))
            claims.append(str(claimed))
            self.assertLessEqual(self.ready(), 17)
            self.finish(claimed)
            if str(claimed) not in targets:
                self.add_pending(100 + cycle)
            self.assertEqual(self.ready(), 16)
        self.assertEqual(claims[:6], [targets[0], backlog[0], targets[1], backlog[1], targets[2], backlog[2]])
        for reg in targets:
            self.assertEqual(claims.count(reg), 1)
            self.assertEqual(self.row(reg)['last_semantic_version'], 'current')
        self.assertEqual(len(claims), 40)
        self.assertEqual(self.findings(targets[0]), 1)
        with self.connect() as c:
            self.assertEqual(tuple(c.execute('SELECT * FROM preserved_history').fetchone()), (1, targets[0], 'approved'))
        self.assertEqual((self.row(targets[0])['new_findings'], self.row(targets[0])['new_checks']), (7, 3))

    def test_stage_is_one_hint_not_pending_admission_or_reservation(self):
        targets, backlog = self.prepare(3)
        before = {reg: self.row(reg) for reg in targets + backlog}
        with self.connect() as c:
            tables_before = [tuple(r) for r in c.execute('SELECT type,name,sql FROM sqlite_master ORDER BY name')]
        self.assertEqual(general.refresh_changed(self.ns), 0)
        self.assertEqual(self.handoff()['reg'], targets[0])
        self.assertNotIn('claim_token', self.handoff())
        self.assertEqual(self.semantic.call_count, 1)
        self.assertEqual({reg: self.row(reg) for reg in targets + backlog}, before)
        self.assertEqual(self.ready(), 16)
        self.assertEqual(general.refresh_changed(self.ns), 0)
        self.assertEqual(self.semantic.call_count, 1)
        with self.connect() as c:
            self.assertEqual([tuple(r) for r in c.execute('SELECT type,name,sql FROM sqlite_master ORDER BY name')], tables_before)
        self.assertEqual((general.READY_HIGH_WATER, general.GENERAL_WORKERS), (16, 4))

    def test_at_most_one_direct_terminal_claim_remains_running(self):
        targets, backlog = self.prepare(3)
        general.refresh_changed(self.ns)
        direct = general.claim_next(self.ns)
        self.assertEqual(direct, targets[0])
        for _ in range(3):
            self.assertEqual(general.refresh_changed(self.ns), 0)
        with ThreadPoolExecutor(max_workers=3) as pool:
            regular = list(pool.map(lambda _: general.claim_next(self.ns), range(3)))
        self.assertTrue(all(str(reg) in backlog for reg in regular))
        self.assertEqual(sum(self.row(reg)['state'] == 'running' for reg in targets), 1)
        self.assertEqual(self.handoff(), {'reg': str(direct), 'claim_token': direct.reservation_token})
        self.assertEqual(self.ready(), 17)
        self.finish(direct)
        general.refresh_changed(self.ns)
        self.assertEqual(self.handoff()['reg'], targets[1])
        self.assertEqual(general.claim_next(self.ns), targets[1])

    def test_two_scanners_and_claimers_cannot_duplicate_handoff(self):
        targets, backlog = self.prepare()
        barrier = threading.Barrier(2)
        def snapshot(c, reg):
            self.assertFalse(c.in_transaction)
            barrier.wait(timeout=5)
            return 'current'
        with patch.object(general, 'semantic_source_version', side_effect=snapshot):
            with ThreadPoolExecutor(max_workers=2) as pool:
                self.assertEqual(list(pool.map(lambda _: general.refresh_changed(self.ns), range(2))), [0, 0])
        self.assertEqual(self.handoff()['reg'], targets[0])
        barrier = threading.Barrier(2)
        with patch.object(general, 'semantic_source_version', side_effect=snapshot):
            with ThreadPoolExecutor(max_workers=2) as pool:
                claims = list(pool.map(lambda _: general.claim_next(self.ns), range(2)))
        self.assertEqual(sum(str(reg) == targets[0] for reg in claims), 1)
        self.assertEqual(sum(str(reg) in backlog for reg in claims), 1)
        direct = next(reg for reg in claims if str(reg) == targets[0])
        self.assertEqual(self.handoff()['claim_token'], direct.reservation_token)

    def test_restart_preserves_hint_turn_and_rejects_old_running_owner(self):
        targets, backlog = self.prepare(2)
        general.refresh_changed(self.ns)
        general.initialize(dict(self.ns))
        old = general.claim_next(self.ns)
        self.assertEqual(old, targets[0])
        self.finish(old)
        general.refresh_changed(self.ns)
        general.initialize(dict(self.ns))
        self.assertEqual(general.claim_next(self.ns), backlog[0])
        second = general.claim_next(self.ns)
        self.assertEqual(second, targets[1])
        general.initialize(dict(self.ns))
        recovered = self.row(second)
        self.assertEqual((recovered['state'], recovered['reservation_token'], recovered['last_semantic_version']), ('pending', '', 'old'))
        general.mark(self.ns, second, 'completed', {'last_source_version': 'late', 'last_semantic_version': 'late'})
        self.assertEqual(self.row(second), recovered)
        general.refresh_changed(self.ns)
        self.assertIsNone(self.handoff())

    def test_source_reverted_before_claim_drops_hint_without_rerun(self):
        targets, backlog = self.prepare()
        before = self.row(targets[0])
        general.refresh_changed(self.ns)
        self.sources[targets[0]] = 'old'
        self.assertEqual(general.claim_next(self.ns), backlog[0])
        self.assertIsNone(self.handoff())
        self.assertEqual(self.row(targets[0]), before)
        general.refresh_changed(self.ns)
        self.assertIsNone(self.handoff())

    def test_changed_source_before_claim_uses_preanalysis_latest_watermark(self):
        targets, _ = self.prepare()
        general.refresh_changed(self.ns)
        self.sources[targets[0]] = 'newest'
        claimed = general.claim_next(self.ns)
        self.assertEqual(claimed, targets[0])
        self.assertEqual(self.row(claimed)['last_semantic_version'], 'old')
        self.assertEqual(self.finish(claimed)['last_semantic_version'], 'newest')
        self.assertEqual(general.refresh_changed(self.ns), 0)
        self.assertIsNone(self.handoff())

    def test_change_during_active_review_gets_one_subsequent_handoff(self):
        targets, backlog = self.prepare()
        general.refresh_changed(self.ns)
        claimed = general.claim_next(self.ns)
        snapshot = general.review_watermark(self.ns, claimed)
        self.sources[targets[0]] = 'arrived-during-review'
        self.assertEqual(general.refresh_changed(self.ns), 0)
        general.mark(self.ns, claimed, 'completed', snapshot)
        general.refresh_changed(self.ns)
        self.assertEqual(general.claim_next(self.ns), backlog[0])
        followup = general.claim_next(self.ns)
        self.assertEqual(followup, targets[0])
        self.finish(followup)
        general.refresh_changed(self.ns)
        self.assertIsNone(self.handoff())

    def test_snapshot_errors_do_not_stamp_or_block_other_claims(self):
        targets, backlog = self.prepare()
        before = self.row(targets[0])
        with patch.object(general, 'semantic_source_version', side_effect=SemanticSnapshotError('temporary')):
            self.assertEqual(general.refresh_changed(self.ns), 0)
        self.assertIsNone(self.handoff())
        general.refresh_changed(self.ns)
        with patch.object(general, 'semantic_source_version', side_effect=SemanticSnapshotError('temporary')):
            self.assertEqual(general.claim_next(self.ns), backlog[0])
        self.assertIsNone(self.handoff())
        self.assertEqual(self.row(targets[0]), before)
        general.refresh_changed(self.ns)
        self.assertEqual(general.claim_next(self.ns), targets[0])

    def test_racing_reservation_wins_and_no_hash_runs_in_writer_transaction(self):
        targets, backlog = self.prepare()
        general.refresh_changed(self.ns)
        def race(c, reg):
            self.assertFalse(c.in_transaction)
            with self.connect() as other:
                other.execute("UPDATE v332_audit_queue SET state='running',reservation_token='other-owner',updated_at='raced' WHERE reg=?", (reg,))
            return 'current'
        with patch.object(general, 'semantic_source_version', side_effect=race):
            self.assertEqual(general.claim_next(self.ns), backlog[0])
        self.assertEqual((self.row(targets[0])['state'], self.row(targets[0])['reservation_token']), ('running', 'other-owner'))
        self.assertEqual(self.row(targets[0])['last_semantic_version'], 'old')
        self.assertIsNone(self.handoff())

    def test_ineligible_customer_and_other_campaign_cannot_receive_handoff(self):
        targets, backlog = self.prepare(2)
        general.refresh_changed(self.ns)
        with self.connect() as c:
            c.execute("UPDATE purchases SET customer='ГБУ Учреждение' WHERE reg_number=?", (targets[0],))
        before = self.row(targets[0])
        self.assertEqual(general.claim_next(self.ns), backlog[0])
        self.assertEqual(self.row(targets[0]), before)
        general.refresh_changed(self.ns)
        self.assertEqual(self.handoff()['reg'], targets[1])
        with self.connect() as c:
            c.execute("UPDATE v332_audit_queue SET campaign='other-campaign' WHERE reg=?", (targets[1],))
        self.assertEqual(general.claim_next(self.ns), backlog[1])
        with self.connect() as c:
            self.assertEqual(c.execute("SELECT state FROM v332_audit_queue WHERE reg=?", (targets[1],)).fetchone()[0], 'completed')

    def test_pending_running_and_future_retry_guards_survive(self):
        targets, backlog = self.prepare()
        general.refresh_changed(self.ns)
        with self.connect() as c:
            c.execute("UPDATE v332_audit_queue SET next_retry_at=?,retry_count=4 WHERE reg=?", (time.time() + 3600, targets[0]))
            c.execute("UPDATE v332_audit_queue SET state='running',reservation_token='active' WHERE reg=?", (backlog[0],))
            c.execute("UPDATE v332_audit_queue SET next_retry_at=?,retry_count=5 WHERE reg=?", (time.time() + 3600, backlog[1]))
        protected = {reg: self.row(reg) for reg in [targets[0], *backlog[:2]]}
        self.assertEqual(general.claim_next(self.ns), backlog[2])
        self.assertEqual({reg: self.row(reg) for reg in protected}, protected)

    def test_unknown_legacy_watermarks_are_not_seeded_and_receipt_only_works(self):
        targets, _ = self.prepare()
        with self.connect() as c:
            c.execute("UPDATE v332_audit_queue SET last_semantic_version='',last_engine_signature='' WHERE reg=?", (targets[0],))
            c.execute("UPDATE docs SET local_path='' WHERE reg_number=?", (targets[0],))
        self.receipts.side_effect = lambda reg: (str(reg) == targets[0], None)
        general.initialize(self.ns)
        self.assertEqual(self.row(targets[0])['last_semantic_version'], '')
        general.refresh_changed(self.ns)
        claimed = general.claim_next(self.ns)
        self.assertEqual(claimed, targets[0])
        self.assertEqual(self.row(claimed)['receipt_ready'], 1)
        self.assertEqual(self.row(claimed)['last_semantic_version'], '')
        self.finish(claimed)
        general.refresh_changed(self.ns)
        self.assertIsNone(self.handoff())

    def test_unclaimable_cached_receipts_cannot_veto_terminal_handoff(self):
        targets, backlog = self.prepare(2)
        general.refresh_changed(self.ns)
        self.finish(general.claim_next(self.ns))
        with self.connect() as c:
            c.execute("UPDATE docs SET local_path='' WHERE reg_number<?", (targets[0],))
            c.execute("UPDATE v332_audit_queue SET receipt_ready=1 WHERE state='pending'")
        before = {reg: self.row(reg) for reg in backlog}
        general.refresh_changed(self.ns)
        self.assertEqual(self.handoff()['reg'], targets[1])
        def unavailable(reg):
            if str(reg) in backlog:
                raise OSError('temporary receipt access')
            return False, None
        self.receipts.side_effect = unavailable
        self.assertIsNone(general.claim_next(self.ns))
        self.assertEqual(general.claim_next(dict(self.ns)), targets[1])
        self.assertEqual({reg: self.row(reg) for reg in backlog}, before)

    def test_zero_admission_limit_stages_nothing(self):
        targets, _ = self.prepare()
        self.assertEqual(general.refresh_changed(self.ns, admission_limit=0), 0)
        self.assertEqual(self.semantic.call_count, 0)
        self.assertIsNone(self.handoff())
        self.assertEqual(self.row(targets[0])['state'], 'completed')

    def test_saturated_scan_remains_paged_durable_and_reaches_late_stale_row(self):
        targets, _ = self.prepare()
        with self.connect() as c:
            for i in range(100, 399):
                reg = str(32619000000 + i)
                c.execute("INSERT INTO purchases(reg_number,customer) VALUES(?,'ООО Тест')", (reg,))
                c.execute("INSERT INTO docs(reg_number,local_path,status) VALUES(?,'fixture','checked')", (reg,))
                c.execute("INSERT INTO v332_audit_queue(campaign,reg,state,last_semantic_version,last_engine_signature) VALUES(?,?,'completed','current','engine')", (general.CAMPAIGN, reg))
        for expected in (128, 256, 300):
            self.assertEqual(general.refresh_changed(dict(self.ns)), 0)
            self.assertEqual(self.semantic.call_count, expected)
        self.assertEqual(self.handoff()['reg'], targets[0])
        self.assertEqual(general.claim_next(self.ns), targets[0])


class DirectRecheckLegalOutcomes(DatabaseFixture, unittest.IsolatedAsyncioTestCase):
    async def run_terminal(self, *, competitive):
        reg = self.purchase(900)
        with self.connect() as c:
            if not competitive:
                c.execute("UPDATE purchases SET method='Закупка у единственного поставщика' WHERE reg_number=?", (reg,))
            c.execute("CREATE TABLE manager_decisions(id INTEGER PRIMARY KEY,reg TEXT,decision TEXT)")
            c.execute("INSERT INTO manager_decisions VALUES(1,?,'approved')", (reg,))
        general.initialize(self.ns)
        with self.connect() as c:
            # Unknown legacy completion, not a seeded current watermark.
            c.execute("UPDATE v332_audit_queue SET state='completed' WHERE reg=?", (reg,))
            for i in range(1, 17):
                pending = str(32619000000 + i)
                c.execute("INSERT INTO purchases(reg_number,customer) VALUES(?,'ООО Тест')", (pending,))
                c.execute("INSERT INTO docs(reg_number,local_path,status) VALUES(?,'fixture','checked')", (pending,))
                c.execute("INSERT INTO v332_audit_queue(campaign,reg) VALUES(?,?)", (general.CAMPAIGN, pending))
        self.assertEqual(general.refresh_changed(self.ns), 0)
        claimed = general.claim_next(self.ns)
        self.assertEqual(claimed, reg)
        snapshot = await general.file_stage(self.ns, 'source', general.review_watermark, self.ns, claimed)
        state, detail = await general.inspect_one(self.ns, claimed)
        self.assertEqual(state, 'completed')
        self.assertFalse(detail.pop('source_incomplete', False))
        general.mark(self.ns, claimed, state, {**detail, **snapshot})
        self.assertEqual(self.findings(reg), int(competitive))
        self.assertEqual(detail['new_findings'], int(competitive))
        self.assertEqual(general.refresh_changed(self.ns), 0)
        self.assertNotEqual(general.claim_next(self.ns), reg)
        with self.connect() as c:
            self.assertEqual(c.execute('SELECT decision FROM manager_decisions WHERE id=1').fetchone()[0], 'approved')
        return reg

    async def test_direct_legacy_recheck_finds_real_episode_and_preserves_decision(self):
        reg = await self.run_terminal(competitive=True)
        with self.connect() as c:
            found = dict(c.execute("SELECT * FROM findings WHERE reg_number=? AND kind='violation'", (reg,)).fetchone())
        self.assertIn('zakupki.gov.ru', found['source_url'])
        self.assertTrue(found['law'])
        self.assertEqual(self.row(reg)['new_findings'], 1)

    async def test_direct_noncompetitive_recheck_does_not_inflate_findings(self):
        reg = await self.run_terminal(competitive=False)
        self.assertEqual(self.row(reg)['new_findings'], 0)


if __name__ == '__main__':
    unittest.main()
