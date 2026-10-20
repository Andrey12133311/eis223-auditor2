"""Distinct numbered sections in one original survive both audit workflows.

All files, receipts, transport XML and dates are synthetic and local. The
installed legal wrappers and persistence run without source/analyzer mocks.
"""
import asyncio
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_official_publication_evidence as saved
import test_date_label_pipeline_parity as pipeline
import v332_all_procurements_audit as general
import v342_thematic_queue as thematic
import v348_cross_document_legal as events
import v345_thematic_accelerator as fast
import v352_dashboard_truth as truth
import full_recheck_request as requested
from v352_dashboard_truth import load_counts


FILENAME = 'Сводная информация о протоколах.docx'


def section(number, *, signed='06.10.2026', published='13.10.2026'):
    return (f'Номер протокола: {number}\n'
            f'Дата подписания протокола рассмотрения заявок: {signed}\n'
            f'Дата размещения протокола рассмотрения заявок в ЕИС: {published}\n')


class LocalEventPipelineParity(unittest.TestCase):
    connect = saved.OfficialPublicationEvidenceTests.connect
    make_xml = saved.OfficialPublicationEvidenceTests.make_xml
    write_archive = saved.OfficialPublicationEvidenceTests.write_archive
    write_original = saved.OfficialPublicationEvidenceTests.write_original
    collect = saved.OfficialPublicationEvidenceTests.collect
    seed_collected = pipeline.DateLabelPipelineParity.seed_collected
    assert_both_paths = pipeline.DateLabelPipelineParity.assert_both_paths

    def setUp(self):
        saved.OfficialPublicationEvidenceTests.setUp(self)
        # Avoid unrelated seeded names for the same attachment UID. Every
        # test starts with one consistent generic role for the compilation.
        shutil.rmtree(self.root / 'eis223_official_archive_inbox' / saved.REG)
        self.write_archive(self.make_xml('purchaseNotice', filename=FILENAME))

    def assert_stored_events(self, expected_numbers):
        with self.connect() as db:
            rows = [dict(row) for row in db.execute('SELECT * FROM findings')]
            self.assertEqual(len(rows), len(expected_numbers))
            self.assertEqual(load_counts(db, [saved.REG]), {saved.REG: len(expected_numbers)})
            self.assertEqual(db.execute('SELECT violations FROM purchases').fetchone()[0],
                             len(expected_numbers))
        self.assertEqual({events.finding_protocol_number(row) for row in rows}, expected_numbers)
        self.assertEqual({row['code'] for row in rows},
                         {'223-4-12-PROTOCOL-PUBLICATION-LATE'})
        return rows

    def historical_compilation(self, *, checklist=False):
        self.write_original(section('1') + '\n' + section('2'))
        collected=self.seed_collected()
        found=self.ns['analyze'](collected['p'],collected['docs'])
        self.assertEqual(len(found),2)
        legacy={**found[0],'evidence':events.event_identity_evidence(
            events.event_evidence_text(found[0]['evidence']),'PROTOCOL','',collected['docs'][0])}
        self.assertEqual(events.finding_protocol_number(legacy),'')
        if checklist:
            general.persist(self.ns,saved.REG,[],[{**legacy,'status':'violation',
                'scope':'223-ФЗ','field_path':legacy['code'],'item':legacy['title']}])
        else:
            general.persist(self.ns,saved.REG,[legacy],[])
        table='checks' if checklist else 'findings'
        with self.connect() as db:
            old=dict(db.execute('SELECT * FROM '+table).fetchone())
            db.execute('CREATE TABLE historical_decisions(source_table TEXT,source_id INTEGER,decision TEXT)')
            db.execute('INSERT INTO historical_decisions VALUES(?,?,?)',(table,old['id'],'approved historical decision'))
        return found,legacy,table,old

    def assert_historical_retained(self, table, old, expected=2):
        with self.connect() as db:
            self.assertEqual(dict(db.execute('SELECT * FROM '+table+' WHERE id=?',(old['id'],)).fetchone()),old)
            self.assertEqual(tuple(db.execute('SELECT * FROM historical_decisions').fetchone()),
                             (table,old['id'],'approved historical decision'))
            self.assertEqual(load_counts(db,[saved.REG]),{saved.REG:expected})
            self.assertEqual(db.execute('SELECT violations FROM purchases').fetchone()[0],expected)

    def requested_attempt(self):
        with self.connect() as db:
            db.execute('CREATE TABLE v332_audit_queue(campaign TEXT,reg TEXT,state TEXT,updated_at TEXT,'
                       'new_findings INTEGER DEFAULT 0,new_checks INTEGER DEFAULT 0)')
            general.ensure_source_column(db)
            db.execute('INSERT INTO v332_audit_queue(campaign,reg,state) VALUES(?,?,?)',
                       (general.CAMPAIGN,saved.REG,'pending'))
            db.execute('CREATE TABLE v319_manual_inputs(reg TEXT PRIMARY KEY)')
        with self.connect() as db:
            requested.create_request(db,'local-refinement',lambda:[saved.REG],expected_count=1)
            db.execute('BEGIN IMMEDIATE')
            claim=requested.claim_member(db,'local-refinement',saved.REG)
            self.assertIsNotNone(claim)
            requested.set_review_watermark(db,claim,source_version='verified-source',engine_signature='verified-engine')
        reg=general.AuditReservation(saved.REG,'')
        reg.full_recheck_only=True
        reg.full_recheck_claim=claim
        reg.full_recheck_receipt={'source_version':'verified-source','engine_signature':'verified-engine',
            'full_verified_read':True,'metrics':{'documents_found':1,'documents_read':1,
                'documents_unreadable':0,'verified_original_documents':1,'original_expected':1,'original_received':1}}
        return reg

    def test_historical_generic_is_retained_with_one_unique_credit_and_raw_diagnostic_two(self):
        found,legacy,table,old=self.historical_compilation()
        reg=self.requested_attempt()
        output=io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(general.persist(self.ns,reg,found,[],progress_token='refined'),(1,0))
        records=[json.loads(line.split(' ',1)[1]) for line in output.getvalue().splitlines()
                 if line.startswith('AUDIT_FUNNEL ')]
        self.assertEqual(records[-1]['counts']['persisted_confirmed'],2)
        self.assert_historical_retained(table,old)
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM findings').fetchone()[0],3)
            self.assertEqual(db.execute('SELECT new_findings FROM v332_audit_queue').fetchone()[0],1)
            self.assertEqual(tuple(db.execute('SELECT new_findings,total_new_findings FROM full_recheck_members').fetchone()),(1,1))
            metrics=json.loads(db.execute('SELECT metrics_json FROM full_recheck_receipts').fetchone()[0])
            self.assertEqual(metrics['new_findings'],1)
            db.execute('BEGIN IMMEDIATE')
            requested.finish_member(db,reg.full_recheck_claim,result_token='refined')
        self.assertEqual(general.persist(self.ns,saved.REG,found,[],progress_token='replay'),(0,0))
        self.assert_historical_retained(table,old)
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT new_findings FROM v332_audit_queue').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT total_new_findings FROM full_recheck_members').fetchone()[0],1)

    def test_historical_generic_checklist_refines_without_deleting_the_reviewed_row(self):
        found,legacy,table,old=self.historical_compilation(checklist=True)
        self.assertEqual(general.persist(self.ns,saved.REG,found,[]),(1,0))
        self.assert_historical_retained(table,old)
        self.assertEqual(general.persist(self.ns,saved.REG,found,[]),(0,0))
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM findings').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM checks').fetchone()[0],1)

    def test_later_review_and_unrelated_violation_keep_refined_unique_counter(self):
        found,legacy,table,old=self.historical_compilation()
        self.assertEqual(general.persist(self.ns,saved.REG,found,[]),(1,0))
        review={'scope':'Review','item':'Independent review','status':'check','field_path':'later-review',
                'evidence':'An unrelated review item'}
        self.assertEqual(general.persist(self.ns,saved.REG,[],[review]),(0,1))
        self.assert_historical_retained(table,old)
        other={'kind':'violation','code':'independent-rule','title':'Independent violation',
               'law':'Other provision','evidence':'Separate independently established factual episode'}
        self.assertEqual(general.persist(self.ns,saved.REG,[other],[]),(1,0))
        self.assert_historical_retained(table,old,expected=3)

    def test_refinement_rollback_preserves_history_queue_and_requested_receipt(self):
        found,legacy,table,old=self.historical_compilation()
        reg=self.requested_attempt()
        with self.connect() as db:
            db.execute("CREATE TRIGGER fail_refinement BEFORE UPDATE OF violations ON purchases "
                       "BEGIN SELECT RAISE(ABORT,'forced refinement rollback'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError,'forced refinement rollback'):
            general.persist(self.ns,reg,found,[],progress_token='retry-refinement')
        self.assert_historical_retained(table,old,expected=1)
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM findings').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT new_findings FROM v332_audit_queue').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM full_recheck_receipts').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT total_new_findings FROM full_recheck_members').fetchone()[0],0)
            db.execute('DROP TRIGGER fail_refinement')
        self.assertEqual(general.persist(self.ns,reg,found,[],progress_token='retry-refinement'),(1,0))
        self.assert_historical_retained(table,old)

    def test_refinement_collapse_allows_unrelated_writer_before_reserving_sqlite(self):
        found,legacy,table,old=self.historical_compilation()
        with self.connect() as db:
            db.execute('CREATE TABLE unrelated_intake(value INTEGER)')
        actual=truth.collapse_legal_events
        def collapse(rows):
            with self.connect() as db:
                db.execute('INSERT INTO unrelated_intake VALUES(1)')
            return actual(rows)
        with patch.object(truth,'collapse_legal_events',side_effect=collapse):
            self.assertEqual(general.persist(self.ns,saved.REG,found,[]),(1,0))
        self.assert_historical_retained(table,old)
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM unrelated_intake').fetchone()[0],3)

    def test_thematic_saved_generic_retains_history_and_adds_local_proof_rows_once(self):
        found,legacy,table,old=self.historical_compilation()
        state,tick=dict(thematic.WORKER_STATE),thematic.tick
        def restore():
            thematic.WORKER_STATE.clear();thematic.WORKER_STATE.update(state);thematic.tick=tick
        self.addCleanup(restore)
        thematic.init(self.ns)
        fast.install(self.ns)
        batch='local-protocol-refinement'
        with self.connect() as db:
            db.execute('INSERT INTO thematic_batches(id,categories,created_at,status,legal_forms) '
                       'VALUES(?,?,?,\'active\',?)',(batch,'["protocols"]','fixture','["ООО"]'))
            db.execute('INSERT INTO thematic_items(batch_id,reg,updated_at) VALUES(?,?,?)',
                       (batch,saved.REG,'fixture'))
        item={'batch':batch,'reg':saved.REG,'doc_id':1,'ready':1,'categories':['protocols']}
        legacy={**legacy,'category':'protocols'}
        candidates=[{**row,'category':'protocols'} for row in found]
        self.assertEqual(fast._persist(self.ns,[(item,'completed',[legacy],'old')]),1)
        with self.connect() as db:
            previous=dict(db.execute('SELECT rowid,* FROM thematic_findings').fetchone())
        output=io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(fast._persist(self.ns,[(item,'completed',candidates,'refined')]),1)
        self.assertIn('new_unique_findings=1',output.getvalue())
        records=[json.loads(line.split(' ',1)[1]) for line in output.getvalue().splitlines()
                 if line.startswith('AUDIT_FUNNEL ')]
        self.assertEqual(records[-1]['counts']['persisted_confirmed'],2)
        self.assertEqual(fast._persist(self.ns,[(item,'completed',candidates,'replay')]),1)
        with self.connect() as db:
            rows=[dict(row) for row in db.execute('SELECT rowid,* FROM thematic_findings')]
            self.assertEqual(len(rows),3)
            self.assertEqual(rows[0],previous)
            self.assertEqual(len(truth.collapse_legal_events(rows)),2)

    def test_two_same_day_numbered_sections_survive_receipt_storage_and_thematic(self):
        self.write_original(section('1') + '\n' + section('2'))
        collected = self.seed_collected()
        self.assertEqual(len(collected['docs']), 1)
        original = collected['docs'][0]
        self.assertEqual(original['meta']['sha256'],
                         hashlib.sha256(Path(original['local']).read_bytes()).hexdigest())
        found = self.assert_both_paths(2, seed=False)
        self.assertEqual({events.finding_protocol_number(row) for row in found}, {'1', '2'})
        stored = self.assert_stored_events({'1', '2'})
        # Independent legal sections do not alter physical original identity.
        self.assertEqual({events.finding_original_identity(row) for row in stored}, {(saved.UID, '')})
        self.assertEqual({events.finding_binary_identity(row) for row in stored},
                         {original['meta']['sha256']})

    def test_same_binary_alias_and_repeat_do_not_double_two_sections(self):
        self.write_original(section('1') + '\n' + section('2'))
        self.assert_both_paths(2)
        original_bytes = self.original_path.read_bytes()
        digest = hashlib.sha256(original_bytes).hexdigest()
        alias_uid = 'A' * 32
        alias = self.root / 'eis223_official_originals' / saved.REG / alias_uid
        alias.mkdir(parents=True)
        (alias / (digest + '.docx')).write_bytes(original_bytes)
        (alias / 'receipt.json').write_text(json.dumps({
            'reg_number': saved.REG, 'uid': alias_uid, 'extension': '.docx',
            'sha256': digest, 'bytes': len(original_bytes)}))
        self.write_archive(self.make_xml('purchaseNotice', version=2, uid=alias_uid,
                                        filename='Копия сводной информации о протоколах.docx'))
        replay = asyncio.run(general.inspect_one(self.ns, saved.REG))
        selected = asyncio.run(thematic.independent_review(self.ns, saved.REG, ['protocols']))
        self.assertEqual((replay[0], replay[1]['new_findings']), ('completed', 0))
        self.assertEqual((selected[0], len(selected[1])), ('completed', 2))
        self.assert_stored_events({'1', '2'})

    def test_repeated_identical_section_is_one_event(self):
        self.write_original(section('1') + '\n' + section('1'))
        self.assert_both_paths(1)
        self.assert_stored_events({'1'})

    def test_crossed_numbered_sections_cannot_supply_each_others_dates(self):
        self.write_original(
            'Номер протокола: 1\n'
            'Дата подписания протокола рассмотрения заявок: 06.10.2026\n'
            'Номер протокола: 2\n'
            'Дата размещения протокола рассмотрения заявок в ЕИС: 13.10.2026\n'
            'Документы относятся к разным решениям комиссии.')
        self.assert_both_paths(0)

    def test_unsupported_numbers_do_not_create_two_independent_episodes(self):
        self.write_original(section('1(А)') + '\n' + section('1(Б)'))
        self.assert_both_paths(1)
        self.assert_stored_events({''})

    def test_sample_compilation_is_not_promoted_to_actual_protocol_events(self):
        shutil.rmtree(self.root / 'eis223_official_archive_inbox' / saved.REG)
        self.write_archive(self.make_xml('purchaseNotice', filename='Образец протоколов.docx'))
        self.write_original(section('1') + '\n' + section('2'))
        self.assert_both_paths(0)

    def test_missing_original_receipt_cannot_reuse_numbered_cached_text(self):
        self.write_original(section('1') + '\n' + section('2'))
        self.seed_collected()
        self.original_receipt.unlink()
        result = asyncio.run(general.inspect_one(self.ns, saved.REG))
        selected = asyncio.run(thematic.independent_review(self.ns, saved.REG, ['protocols']))
        self.assertEqual(result[1].get('new_findings', 0), 0)
        self.assertEqual(selected[1], [])
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM findings').fetchone()[0], 0)
            self.assertEqual(load_counts(db, [saved.REG]), {saved.REG: 0})

    def test_tampered_binary_cannot_reuse_valid_numbered_cached_text(self):
        self.write_original(section('1') + '\n' + section('2'))
        self.seed_collected()
        binary = self.original_path.read_bytes()
        self.original_path.write_bytes(binary[:-1] + bytes([binary[-1] ^ 1]))
        result = asyncio.run(general.inspect_one(self.ns, saved.REG))
        selected = asyncio.run(thematic.independent_review(self.ns, saved.REG, ['protocols']))
        self.assertEqual(result[1].get('new_findings', 0), 0)
        self.assertEqual(selected[1], [])
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM findings').fetchone()[0], 0)
            self.assertEqual(load_counts(db, [saved.REG]), {saved.REG: 0})


if __name__ == '__main__':
    unittest.main()
