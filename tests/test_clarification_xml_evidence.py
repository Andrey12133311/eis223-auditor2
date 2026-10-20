"""Real synthetic immutable ZIP/XML/original receipts; no live procurement writes."""
import asyncio
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from xml.sax.saxutils import escape
import zipfile
import eis223_soap_xml as reader
import official_publication_evidence as proof
import v294_eis_bridge as bridge
import v341_precise_legal as legal
import v332_all_procurements_audit as general
import v342_thematic_queue as thematic
from v352_dashboard_truth import load_counts
import test_official_publication_evidence as fixtures
from test_official_publication_evidence import REG, UID, URL

TEXT = ('Разъяснение положений документации. Дата поступления запроса о разъяснении документации: 06.10.2026. '
        'Ответ заказчика: технические характеристики приведены в приложении. Дата подготовки ответа: 07.10.2026.')
CODE = '223-3.2-3-CLARIFICATION-RESPONSE-LATE'


class ClarificationXmlEvidence(unittest.TestCase):
    connect = fixtures.OfficialPublicationEvidenceTests.connect
    make_xml = fixtures.OfficialPublicationEvidenceTests.make_xml
    write_archive = fixtures.OfficialPublicationEvidenceTests.write_archive
    write_original = fixtures.OfficialPublicationEvidenceTests.write_original
    collect = fixtures.OfficialPublicationEvidenceTests.collect
    evaluate = fixtures.OfficialPublicationEvidenceTests.evaluate

    def setUp(self):
        fixtures.OfficialPublicationEvidenceTests.setUp(self)
        # These are temporary synthetic fixture archives only, not live data.
        for path in (self.root / 'eis223_official_archive_inbox' / REG).iterdir():
            path.unlink()
        self.write_original(TEXT)
        self.write_archive(self.make_xml('purchaseNotice', filename='Разъяснение.docx'))

    def explanation(self, publication='2026-10-13T10:00:00+03:00', request='2026-10-06T09:00:00+03:00',
                    decision='2026-10-07T10:00:00+03:00', status='P', uid=UID, guid='explanation-1',
                    extra='', attachments=True, parse=True):
        filename = f'explanation_{REG}_{guid}_{publication[:10] or "missing"}.xml'
        links = ('<attachments><document><guid>attachment-1</guid><fileName>Разъяснение.docx</fileName>'
                 f'<url>{escape(URL.replace(UID, uid))}</url></document></attachments>') if attachments else ''
        raw = (f'<explanation xmlns="{reader.PURCHASE_NS}"><body><item><explanationData>'
               f'<guid>{guid}</guid><purchaseRegNum>{REG}</purchaseRegNum><requestDate>{request}</requestDate>'
               f'<decisionDate>{decision}</decisionDate><publishDate>{publication}</publishDate><status>{status}</status>'
               '<creationDate>2026-10-06T12:00:00+03:00</creationDate>'
               '<modificationDate>2026-10-20T12:00:00+03:00</modificationDate>'
               '<description>Разъяснение № 1</description><requestSubjectInfo>Техническое задание</requestSubjectInfo>'
               + links + extra + '</explanationData></item></body></explanation>').encode()
        return (filename, raw, reader.parse_document(raw, filename)[0]) if parse else (filename, raw)

    def codes(self):
        findings, checks = self.evaluate()
        return [f for f in findings if f['code'] == CODE], checks

    def test_supported_clarification_uses_exact_fields_and_preserves_all_attachments(self):
        name, raw, record = self.explanation()
        self.assertEqual(record['xml_family'], 'clarification')
        self.assertEqual(record['publication'], '2026-10-13T10:00:00+03:00')
        self.assertEqual(record['request_received'], '2026-10-06T09:00:00+03:00')
        self.assertEqual(record['documents'][0]['url'], URL)
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr(name, raw)
        accepted = bridge.parse_notices(stream.getvalue())
        self.assertEqual(accepted[0]['total_attachment_count'], 1)
        self.assertEqual(accepted[0]['unresolved_attachment_count'], 0)

    def test_verified_xml_supplies_missing_publication_date_without_using_preparation_date(self):
        self.write_archive(self.explanation())
        finding, _ = self.codes()
        self.assertEqual(len(finding), 1, finding)
        self.assertIn('2026-10-13', finding[0]['evidence'])
        self.assertIn('SHA256 XML', finding[0]['evidence'])
        self.assertIn('2026-10-09', finding[0]['evidence'])

    def test_on_time_xml_or_late_request_never_become_a_violation(self):
        self.write_archive(self.explanation(publication='2026-10-09T23:59:00+03:00'))
        self.assertEqual(self.codes()[0], [])
        self.write_original(TEXT.replace('06.10.2026', '29.10.2026'))
        self.assertEqual(self.codes()[0], [])

    def test_status_draft_or_absent_is_not_published_evidence(self):
        for status in ('F', ''):
            with self.subTest(status=status):
                path, receipt = self.write_archive(self.explanation(status=status))
                self.assertEqual(self.codes()[0], [])
                path.unlink(); receipt.unlink()

    def test_date_disagreement_or_multiple_xml_events_remain_review(self):
        self.write_archive(self.explanation())
        self.write_archive(self.explanation(guid='different-clarification', publication='2026-10-14T10:00:00+03:00'))
        findings, checks = self.codes()
        self.assertEqual(findings, [])
        self.assertTrue(any(c['field_path'] == 'v341-clarification-xml-conflict' for c in checks))

    def test_xml_contradiction_vetoes_incompatible_textual_pair(self):
        self.write_original(TEXT + ' Дата размещения ответа в ЕИС: 14.10.2026.')
        self.write_archive(self.explanation())
        findings, checks = self.codes()
        self.assertEqual(findings, [])
        self.assertTrue(any(c['field_path'] == 'v341-clarification-xml-conflict' for c in checks))

    def test_other_uid_does_not_complete_this_originals_date_chain(self):
        self.write_archive(self.explanation(uid='A'*32))
        self.assertEqual(self.codes()[0], [])

    def test_receipt_metadata_is_not_a_substitute_for_hash_verified_xml(self):
        archive, receipt = self.write_archive(self.explanation(publication='2026-10-09T10:00:00+03:00'))
        saved = json.loads(receipt.read_text())
        saved['notices'][0]['publication'] = '2026-10-20T10:00:00+03:00'
        receipt.write_text(json.dumps(saved))
        self.assertEqual(self.codes()[0], [])
        archive.write_bytes(archive.read_bytes()+b'tampered')
        self.assertEqual(self.codes()[0], [])

    def test_original_hash_is_rechecked_not_trusted_from_metadata(self):
        self.write_archive(self.explanation())
        result = self.collect(); doc = result['docs'][0]
        verify = proof.clarification_publication_verifier(result['p'])
        self.assertEqual(verify(doc)['state'], 'confirmed')
        self.original_path.write_bytes(self.original_path.read_bytes()+b'tampered')
        fresh = proof.clarification_publication_verifier(result['p'])(doc)
        self.assertEqual(fresh['state'], 'unknown')

    def test_archive_member_cannot_borrow_parent_clarification_date(self):
        self.write_archive(self.explanation())
        result = self.collect(); doc = deepcopy(result['docs'][0])
        doc['meta']['source'] = 'official_eis_archive_member'
        self.assertEqual(proof.clarification_publication_verifier(result['p'])(doc)['state'], 'unknown')
        self.assertEqual(legal.evaluate(result['p'], [doc])[0], [])

    def test_duplicate_date_and_foreign_namespace_fields_are_rejected(self):
        for extra in ('<publishDate>2026-10-20</publishDate>',
                      '<x:publishDate xmlns:x="https://foreign.invalid/">2026-10-20</x:publishDate>',
                      '<purchaseRegNum>32619999999</purchaseRegNum>',
                      '<registrationNumber>32619999999</registrationNumber>'):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.explanation(extra=extra)

    def test_missing_required_field_stays_quarantined_not_verified(self):
        name, raw = self.explanation(publication='', parse=False)
        with self.assertRaises(reader.UnsupportedXmlType):
            reader.parse_document(raw, name)
        record = reader.quarantine_document(raw, name, REG)
        self.assertEqual(record['xml_family'], 'unsupported')
        self.assertEqual(record['documents'], [])

    def test_malformed_date_and_inconsistent_date_order_do_not_establish_violation(self):
        for kwargs in ({'publication': '2026-99-13'}, {'decision': '2026-10-20T10:00:00+03:00'}):
            with self.subTest(kwargs=kwargs):
                path, receipt = self.write_archive(self.explanation(**kwargs))
                self.assertEqual(self.codes()[0], [])
                path.unlink(); receipt.unlink()

    def test_noncompetitive_unknown_scope_and_missing_deadline_do_not_establish_violation(self):
        self.write_archive(self.explanation())
        result = self.collect()
        for change in ({'method': 'Закупка у единственного поставщика'}, {'submission_end': '', 'deadline': ''}):
            with self.subTest(change=change):
                p = {**result['p'], **change}
                if 'method' in change:
                    with patch.object(legal, 'competitive_rule_scope', return_value=False):
                        self.assertEqual(legal.evaluate(p, result['docs'])[0], [])
                else:
                    self.assertEqual(legal.evaluate(p, result['docs'])[0], [])

    def test_ambiguous_explicit_request_label_is_not_bypassed_by_xml(self):
        self.write_archive(self.explanation())
        result = self.collect(); doc = deepcopy(result['docs'][0])
        doc['name'] = 'Ответ на запрос № 1(2).docx'
        self.assertEqual(legal.evaluate(result['p'], [doc])[0], [])

    def test_general_and_thematic_persist_same_evidence_once_without_duplicate_credit(self):
        self.write_archive(self.explanation())
        result = self.collect(); purchase = result['p']
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO purchases(reg_number) VALUES(?)', (REG,))
            db.execute('UPDATE purchases SET title=?,customer=?,method=?,published_at=?,submission_end=?,card_fields_json=? WHERE reg_number=?',
                       (purchase['title'], 'ООО Тест', purchase['method'], purchase['published'], purchase['submission_end'], '{}', REG))
            for doc in result['docs']:
                db.execute('INSERT INTO docs(reg_number,name,url,doc_type,published_at,local_path,status,metadata_json,final_url) VALUES(?,?,?,?,?,?,?,?,?)',
                           (REG,doc['name'],doc['url'],doc['type'],doc['published'],doc['local'],doc['status'],json.dumps(doc['meta']),doc['final_url']))
        files = {path: path.read_bytes() for path in self.root.rglob('*') if path.suffix in ('.zip', '.docx')}
        first = asyncio.run(general.inspect_one(self.ns, REG))
        repeated = asyncio.run(general.inspect_one(self.ns, REG))
        selected = asyncio.run(thematic.independent_review(self.ns, REG, ['dates']))
        self.assertEqual(first[1]['new_findings'], 1, first)
        self.assertEqual(repeated[1]['new_findings'], 0)
        self.assertEqual(len(selected[1]), 1, selected)
        with self.connect() as db:
            self.assertEqual(load_counts(db, [REG]), {REG: 1})
        self.assertEqual({path: path.read_bytes() for path in files}, files)


if __name__ == '__main__':
    unittest.main()
