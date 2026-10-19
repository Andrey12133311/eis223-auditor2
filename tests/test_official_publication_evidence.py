"""Real local receipt/ZIP/XML/DOCX ingress; synthetic facts, no remote state."""
import asyncio
from copy import deepcopy
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

from docx import Document

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import eis223_soap_xml as xml_reader
import official_publication_evidence as proof
import v295_eis_originals as originals
import v298_eis_only as offline
import v341_eis_facts as facts
import v341_precise_legal as legal
import v348_cross_document_legal as cross
import v372_auto_regulation_protocols as protocols
import v332_all_procurements_audit as general
import v342_thematic_queue as thematic
from v352_dashboard_truth import load_counts

REG = '32616233661'
UID = '0123456789ABCDEF0123456789ABCDEF'
URL = 'https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid=' + UID
SIGNATURE = ('Протокол № 1. Дата подписания протокола: 06.10.2026. '
             'Комиссия рассмотрела заявки участников закупки. Заявки допущены.')


class OfficialPublicationEvidenceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='eis-publication-proof-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        env = patch.dict(os.environ, {'EIS223_DATA_DIR': str(self.root)})
        env.start(); self.addCleanup(env.stop)
        self.database = self.root / 'test.sqlite'
        with self.connect() as db:
            db.execute('CREATE TABLE purchases(reg_number TEXT PRIMARY KEY, title TEXT, '
                       'customer TEXT, method TEXT, price TEXT, price_num REAL, published_at TEXT, '
                       'submission_start TEXT, submission_end TEXT, deadline TEXT, card_fields_json TEXT, '
                       'violations INTEGER DEFAULT 0, checks_ok INTEGER DEFAULT 0, checks_attention INTEGER DEFAULT 0)')
            db.executescript('''
                CREATE TABLE docs(id INTEGER PRIMARY KEY,reg_number TEXT,name TEXT,url TEXT,
                    doc_type TEXT,published_at TEXT,local_path TEXT,status TEXT,metadata_json TEXT,final_url TEXT);
                CREATE TABLE findings(id INTEGER PRIMARY KEY,reg_number TEXT,code TEXT,kind TEXT,
                    title TEXT,law TEXT,law_url TEXT,evidence TEXT,note TEXT,document TEXT,
                    source_url TEXT,location TEXT,match_text TEXT);
                CREATE TABLE checks(id INTEGER PRIMARY KEY,reg_number TEXT,scope TEXT,item TEXT,
                    status TEXT,evidence TEXT,source TEXT,source_url TEXT,document TEXT,
                    field_path TEXT,note TEXT,law TEXT,law_url TEXT,location TEXT);
            ''')
        self.captured = []
        self.ns = {'conn': self.connect, 'extract_text': lambda path: '',
                   'doc_type': lambda name: 'protocol' if 'протокол' in name.casefold() else 'other',
                   'analyze': lambda p, docs: [], 'build_checks': lambda p, detail, card, docs: [],
                   'save': lambda *args: self.captured.append(args)}
        facts.initialize(self.ns)
        legal.install(self.ns)
        cross.install(self.ns)
        protocols.install(self.ns)
        self.write_original()
        self.write_archive(self.make_xml('purchaseNotice'))

    def connect(self):
        db = sqlite3.connect(self.database); db.row_factory = sqlite3.Row
        return db

    def make_xml(self, kind='purchaseProtocol', publication='2026-10-13T12:00:00+03:00',
                 version=1, uid=UID, reg=REG, guid='protocol-1', modification='',
                 filename='Протокол.docx'):
        identity = (f'<registrationNumber>{reg}</registrationNumber>' if kind in xml_reader.NOTICE_TYPES
                    else f'<registrationNumber>{reg}-01</registrationNumber><purchaseInfo>'
                         f'<purchaseNoticeNumber>{reg}</purchaseNoticeNumber></purchaseInfo>')
        attachment = ('<attachments><document><guid>file-1</guid><fileName>' + filename +
                      '</fileName><url>' + escape(URL.replace(UID, uid)) + '</url></document></attachments>')
        if kind in xml_reader.NOTICE_TYPES:
            extra = ('<name>Тест закупки</name><purchaseCodeName>Открытый конкурс</purchaseCodeName>'
                     '<applSubmisionStartDate>2026-10-01</applSubmisionStartDate>'
                     '<submissionCloseDateTime>2026-10-30</submissionCloseDateTime>')
        else:
            extra = ''
        name = f'{kind}_{reg}_{version}_{publication[:10] or "undated"}.xml'
        raw = (f'<{kind} xmlns="{xml_reader.PURCHASE_NS}"><{kind}Data>{identity}'
               f'<guid>{guid}</guid><version>{version}</version><publicationDateTime>{publication}</publicationDateTime>'
               f'<modificationDate>{modification}</modificationDate>' + extra + attachment +
               f'</{kind}Data></{kind}>').encode()
        record, _ = xml_reader.parse_document(raw, name)
        return name, raw, record

    def write_archive(self, *records, folder_reg=REG):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            for name, raw, _ in records:
                archive.writestr(name, raw)
        data = stream.getvalue(); digest = hashlib.sha256(data).hexdigest()
        folder = self.root / 'eis223_official_archive_inbox' / folder_reg
        folder.mkdir(parents=True, exist_ok=True)
        archive = folder / (digest + '.zip'); receipt = folder / (digest + '.json')
        archive.write_bytes(data)
        receipt.write_text(json.dumps({'reg_number': folder_reg, 'source': 'official_eis_soap_archive',
            'archive_sha256': digest, 'notices': [entry[2] for entry in records]}))
        return archive, receipt

    def write_original(self, text=SIGNATURE, uid=UID):
        document = Document(); document.add_paragraph(text)
        stream = io.BytesIO(); document.save(stream)
        data = stream.getvalue(); digest = hashlib.sha256(data).hexdigest()
        folder = self.root / 'eis223_official_originals' / REG / uid
        folder.mkdir(parents=True, exist_ok=True)
        self.original_path = folder / (digest + '.docx')
        self.original_path.write_bytes(data)
        self.original_receipt = folder / 'receipt.json'
        self.original_receipt.write_text(json.dumps({'reg_number': REG, 'uid': uid,
            'extension': '.docx', 'sha256': digest, 'bytes': len(data)}))

    def collect(self):
        return asyncio.run(offline.offline_audit(self.ns, REG, collect_only=True))

    def evaluate(self):
        result = self.collect()
        return legal.evaluate(result['p'], result['docs'])

    def assert_persisted_count(self, expected):
        # Seed only actual collected originals, then run the real stored-document
        # reader, general re-audit, thematic review, persistence and UI counter.
        # Deliberately omit the saved annotation: it is never a trust shortcut.
        result = self.collect(); purchase = result['p']
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO purchases(reg_number) VALUES(?)', (REG,))
            db.execute('UPDATE purchases SET title=?,customer=?,method=?,published_at=?, '
                       'submission_end=?,card_fields_json=? WHERE reg_number=?',
                       (purchase['title'], 'ООО Тест', purchase['method'], purchase['published'],
                        purchase['submission_end'], '{}', REG))
            db.execute('DELETE FROM docs WHERE reg_number=?', (REG,))
            for doc in result['docs']:
                metadata = dict(doc['meta']); metadata.pop('official_publication_evidence', None)
                db.execute('INSERT INTO docs(reg_number,name,url,doc_type,published_at,local_path,'
                           'status,metadata_json,final_url) VALUES(?,?,?,?,?,?,?,?,?)',
                           (REG,doc['name'],doc['url'],doc['type'],doc['published'],doc['local'],
                            doc['status'],json.dumps(metadata),doc['final_url']))
        first = asyncio.run(general.inspect_one(self.ns, REG))
        repeated = asyncio.run(general.inspect_one(self.ns, REG))
        selected = asyncio.run(thematic.independent_review(self.ns, REG, ['protocols']))
        self.assertEqual((first[0], first[1]['new_findings']), ('completed', expected))
        self.assertEqual(repeated[1]['new_findings'], 0)
        self.assertEqual((selected[0], len(selected[1])), ('completed', expected))
        with self.connect() as db:
            self.assertEqual(load_counts(db, [REG]), {REG: expected})

    def test_real_ingress_publication_proof_and_installed_analyzer(self):
        self.write_archive(self.make_xml())
        result = self.collect(); doc = result['docs'][0]
        self.assertEqual(doc['published'], '')
        self.assertEqual(sorted(originals.allowed_documents(REG)[UID]), ['ext', 'name', 'uid', 'url'])
        evidence = proof.verified_protocol_publication(result['p'], doc)
        self.assertEqual(evidence['state'], 'confirmed')
        self.assertEqual(evidence['publication_date'], '2026-10-13')
        self.assertEqual(evidence['attachment_uid'], UID)
        self.assertEqual(len(evidence['archive_sha256']), 64)
        self.assertEqual(len(evidence['xml_sha256']), 64)
        # The decisive fact is in verified XML, never fabricated in DOCX text.
        self.assertNotIn('13.10.2026', doc['text'])
        audited = asyncio.run(offline.offline_audit(self.ns, REG))
        self.assertEqual(audited['findings'], 1)
        self.assertEqual(audited['confirmed_violations'], 1)
        self.assertIn('XML', self.captured[-1][2][0]['evidence'])
        self.assert_persisted_count(1)

    def test_notice_publication_alone_cannot_establish_protocol_timing(self):
        result = self.collect()
        self.assertEqual(proof.verified_protocol_publication(result['p'], result['docs'][0])['state'], 'unknown')
        self.assertFalse(self.evaluate()[0])
        self.assert_persisted_count(0)

    def test_modification_date_is_not_publication(self):
        self.write_archive(self.make_xml(publication='', modification='2026-10-13'))
        result = self.collect(); ev = proof.verified_protocol_publication(result['p'], result['docs'][0])
        self.assertEqual(ev['state'], 'unknown')
        self.assertEqual(ev['reason'], 'publication_not_explicit')
        self.assertFalse(self.evaluate()[0])

    def test_protocol_revisions_with_conflicting_dates_or_versions_remain_conflict(self):
        self.write_archive(self.make_xml())
        for version, publication in [(2, '2026-10-14'), (3, '2026-10-13')]:
            with self.subTest(version=version):
                self.write_archive(self.make_xml(version=version, publication=publication))
                result = self.collect(); ev = proof.verified_protocol_publication(result['p'], result['docs'][0])
                self.assertEqual(ev['state'], 'conflict')
                self.assertFalse(self.evaluate()[0])
                self.assert_persisted_count(0)

    def test_wrong_attachment_uid_not_joined(self):
        self.write_archive(self.make_xml(uid='A' * 32))
        result = self.collect()
        self.assertEqual(proof.verified_protocol_publication(result['p'], result['docs'][0])['state'], 'unknown')
        self.assertFalse(self.evaluate()[0])
        self.assert_persisted_count(0)

    def test_wrong_procurement_xml_not_joined(self):
        self.write_archive(self.make_xml(reg='32616233662'))
        self.assertFalse(self.evaluate()[0])

    def test_modified_archive_and_xml_hash_fail_closed(self):
        archive, receipt = self.write_archive(self.make_xml())
        original = archive.read_bytes()
        archive.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
        self.assertFalse(self.evaluate()[0])
        archive.write_bytes(original)
        value = json.loads(receipt.read_text())
        value['notices'][0]['xml_sha256'] = '0' * 64
        receipt.write_text(json.dumps(value))
        self.assertFalse(self.evaluate()[0])

    def test_receipt_only_publication_cannot_override_xml(self):
        _, receipt = self.write_archive(self.make_xml(publication='2026-10-07'))
        value = json.loads(receipt.read_text())
        value['notices'][0]['publication'] = '2026-10-13'
        value['notices'][0]['documents'][0]['published'] = '2026-10-13'
        receipt.write_text(json.dumps(value))
        result = self.collect()
        self.assertEqual(proof.verified_protocol_publication(result['p'], result['docs'][0])['publication_date'], '2026-10-07')
        self.assertFalse(self.evaluate()[0])

    def test_untrusted_metadata_and_browser_manifest_cannot_supply_xml_date(self):
        result = self.collect(); doc = result['docs'][0]
        doc['meta']['official_publication_evidence'] = {'state': 'confirmed', 'publication_date': '2026-10-13'}
        folder = self.root / 'eis223_public_browser_intake' / REG; folder.mkdir(parents=True)
        (folder / 'manifest.json').write_text(json.dumps({'reg_number': REG, 'source': 'public_eis_223_browser',
            'documents': [{'url': URL, 'name': 'Протокол.docx', 'published': '2026-10-13'}]}))
        self.assertEqual(proof.verified_protocol_publication(result['p'], doc)['state'], 'unknown')
        self.assertFalse(legal.evaluate(result['p'], [doc])[0])

    def test_original_receipt_hash_path_and_bytes_revalidated(self):
        self.write_archive(self.make_xml())
        result = self.collect(); doc = result['docs'][0]
        for change in [('sha256', '0' * 64), ('uid', 'A' * 32), ('source', 'unverified')]:
            with self.subTest(change=change):
                changed = deepcopy(doc); changed['meta'][change[0]] = change[1]
                self.assertEqual(proof.verified_protocol_publication(result['p'], changed)['state'], 'unknown')
        changed = deepcopy(doc); changed['local'] = str(self.root / 'missing.docx')
        self.assertEqual(proof.verified_protocol_publication(result['p'], changed)['state'], 'unknown')
        data = self.original_path.read_bytes()
        self.original_path.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
        self.assertEqual(proof.verified_protocol_publication(result['p'], doc)['state'], 'unknown')

    def test_parent_archive_uid_never_authenticates_member_event(self):
        self.write_archive(self.make_xml())
        result = self.collect(); doc = result['docs'][0]
        doc['meta'].update(source='official_eis_archive_member', archive_member='other/Протокол № 2.docx',
                           archive_sha256=doc['meta']['sha256'])
        proof.annotate_protocol_publications(REG, [doc])
        ev = doc['meta']['official_publication_evidence']
        self.assertEqual(ev['state'], 'unknown')
        self.assertNotIn('publication_date', ev)
        self.assertEqual(proof.verified_protocol_publication(result['p'], doc)['state'], 'unknown')
        self.assertFalse(legal.evaluate(result['p'], [doc])[0])

    def test_printed_publication_contradiction_and_multiple_protocols_not_promoted(self):
        self.write_archive(self.make_xml())
        for text in [SIGNATURE + ' Дата размещения протокола в ЕИС: 07.10.2026.',
                     SIGNATURE + ' Протокол № 2. Дата подписания протокола: 07.10.2026.']:
            with self.subTest(text=text):
                self.write_original(text)
                self.assertFalse(self.evaluate()[0])

    def test_printed_late_but_verified_xml_timely_is_review_in_every_path(self):
        self.write_archive(self.make_xml(publication='2026-10-07'))
        self.write_original(SIGNATURE + ' Дата размещения протокола в ЕИС: 13.10.2026.')
        found, reviews = self.evaluate()
        self.assertFalse(found)
        self.assertTrue(any(item.get('status') == 'check' for item in reviews))
        audited = asyncio.run(offline.offline_audit(self.ns, REG))
        self.assertEqual(audited['confirmed_violations'], 0)
        self.assertGreater(audited['checks'], 0)
        self.assert_persisted_count(0)
        with self.connect() as db:
            self.assertGreater(db.execute("SELECT COUNT(*) FROM checks WHERE status='check'").fetchone()[0], 0)

    def test_printed_late_and_conflicting_xml_versions_remain_review(self):
        self.write_archive(self.make_xml())
        self.write_archive(self.make_xml(version=2))
        self.write_original(SIGNATURE + ' Дата размещения протокола в ЕИС: 13.10.2026.')
        found, reviews = self.evaluate()
        self.assertFalse(found)
        self.assertTrue(any(item.get('status') == 'check' for item in reviews))
        audited = asyncio.run(offline.offline_audit(self.ns, REG))
        self.assertEqual(audited['confirmed_violations'], 0)
        self.assertGreater(audited['checks'], 0)
        self.assert_persisted_count(0)

    def test_split_originals_cannot_reinsert_publication_contradicted_by_xml(self):
        publication_uid = 'A' * 32
        self.write_original(
            'Протокол № 1. Дата подписания итогового протокола: 06.10.2026. '
            'Комиссия рассмотрела заявки участников закупки.', uid=UID)
        self.write_original(
            'Протокол № 1. Дата размещения итогового протокола в ЕИС: 13.10.2026. '
            'Публикация результатов закупки.', uid=publication_uid)
        for uid, filename in [(UID, 'Итоговый протокол № 1.docx'),
                              (publication_uid, 'Публикация итогового протокола № 1.docx')]:
            self.write_archive(self.make_xml(uid=uid, filename=filename,
                                             publication='2026-10-09'))

        result = self.collect()
        self.assertEqual(len(result['docs']), 2)
        self.assertEqual({doc['meta']['uid'] for doc in result['docs']},
                         {UID, publication_uid})
        for doc in result['docs']:
            verified = proof.verified_protocol_publication(result['p'], doc)
            self.assertEqual(verified['state'], 'confirmed')
            self.assertEqual(verified['publication_date'], '2026-10-09')
        found, reviews = legal.evaluate(result['p'], result['docs'])
        self.assertFalse(found)
        self.assertTrue(any(item.get('status') == 'check' for item in reviews))
        self.assertFalse(cross.cross_doc_protocols(result['p'], result['docs']))
        self.assertFalse(self.ns['analyze'](result['p'], result['docs']))
        audited = asyncio.run(offline.offline_audit(self.ns, REG))
        self.assertEqual(audited['confirmed_violations'], 0)
        self.assertGreater(audited['checks'], 0)
        self.assert_persisted_count(0)
        with self.connect() as db:
            self.assertGreater(db.execute(
                "SELECT COUNT(*) FROM checks WHERE status='check'").fetchone()[0], 0)

    def test_matching_printed_and_xml_publication_is_one_proven_event(self):
        self.write_archive(self.make_xml())
        self.write_original(SIGNATURE + ' Дата размещения протокола в ЕИС: 13.10.2026.')
        self.assertEqual(len(self.evaluate()[0]), 1)
        audited = asyncio.run(offline.offline_audit(self.ns, REG))
        self.assertEqual(audited['confirmed_violations'], 1)
        self.assert_persisted_count(1)

    def test_absent_protocol_xml_preserves_existing_text_only_finding(self):
        self.write_original(SIGNATURE + ' Дата размещения протокола в ЕИС: 13.10.2026.')
        self.assertEqual(len(self.evaluate()[0]), 1)
        audited = asyncio.run(offline.offline_audit(self.ns, REG))
        self.assertEqual(audited['confirmed_violations'], 1)
        self.assert_persisted_count(1)

    def test_printed_timely_but_verified_xml_late_is_review_in_every_path(self):
        self.write_archive(self.make_xml())
        self.write_original(SIGNATURE + ' Дата размещения протокола в ЕИС: 07.10.2026.')
        found, reviews = self.evaluate()
        self.assertFalse(found)
        self.assertTrue(any(item.get('status') == 'check' for item in reviews))
        audited = asyncio.run(offline.offline_audit(self.ns, REG))
        self.assertEqual(audited['confirmed_violations'], 0)
        self.assertGreater(audited['checks'], 0)
        self.assert_persisted_count(0)

    def test_timely_protocol_and_repeat_evaluation_are_stable(self):
        self.write_archive(self.make_xml(publication='2026-10-09'))
        self.assertFalse(self.evaluate()[0])
        self.assertFalse(self.evaluate()[0])
        self.assert_persisted_count(0)

    def test_xml_publication_without_explicit_signature_never_establishes_violation(self):
        self.write_archive(self.make_xml())
        self.write_original('Протокол № 1. Документ создан 06.10.2026. Комиссия рассмотрела заявки. '
                            'Решение принято единогласно, все заявки допущены.')
        with patch.object(proof, '_protocol_index', wraps=proof._protocol_index) as index:
            self.assertFalse(self.evaluate()[0])
            self.assertEqual(index.call_count, 0)
        self.assert_persisted_count(0)

    def test_two_originals_share_one_fresh_evaluation_xml_snapshot(self):
        self.write_archive(self.make_xml())
        self.write_original(SIGNATURE.replace('№ 1.', '№ 2.'), uid='A' * 32)
        self.write_archive(self.make_xml(uid='A' * 32, guid='protocol-2', filename='Протокол № 2.docx'))
        result = self.collect()
        self.assertEqual(len(result['docs']), 2)
        with patch.object(proof, '_protocol_index', wraps=proof._protocol_index) as index, \
                patch.object(proof.hashlib, 'file_digest', wraps=proof.hashlib.file_digest) as hashed:
            resolver = proof.protocol_publication_verifier(result['p'])
            self.assertEqual(index.call_count, 0)
            for doc in result['docs'] * 2:
                self.assertEqual(resolver(doc)['state'], 'confirmed')
            self.assertEqual(index.call_count, 1)
            self.assertEqual(hashed.call_count, 2)
            fresh = proof.protocol_publication_verifier(result['p'])
            self.assertEqual(fresh(result['docs'][0])['state'], 'confirmed')
            self.assertEqual(index.call_count, 2)
            self.assertEqual(hashed.call_count, 3)


if __name__ == '__main__':
    unittest.main()
