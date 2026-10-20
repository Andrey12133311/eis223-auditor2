"""Expanded date labels survive receipt ingestion and both audit paths.

The synthetic DOCX/XML/ZIP bytes use the existing saved-original fixture.
No network, production procurement or legal threshold is part of these tests.
"""
import asyncio
import json
from pathlib import Path
import shutil
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_official_publication_evidence as saved
import v332_all_procurements_audit as general
import v342_thematic_queue as thematic
from v352_dashboard_truth import load_counts


class DateLabelPipelineParity(unittest.TestCase):
    # Reuse the real local receipt/ZIP/XML/DOCX fixture without inheriting its
    # test cases. Analysis, source verification and persistence are not mocked.
    setUp = saved.OfficialPublicationEvidenceTests.setUp
    connect = saved.OfficialPublicationEvidenceTests.connect
    make_xml = saved.OfficialPublicationEvidenceTests.make_xml
    write_archive = saved.OfficialPublicationEvidenceTests.write_archive
    write_original = saved.OfficialPublicationEvidenceTests.write_original
    collect = saved.OfficialPublicationEvidenceTests.collect

    def seed_collected(self):
        result = self.collect()
        purchase = result['p']
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO purchases(reg_number) VALUES(?)', (saved.REG,))
            db.execute('UPDATE purchases SET title=?,customer=?,method=?,published_at=?, '
                       'submission_end=?,card_fields_json=? WHERE reg_number=?',
                       (purchase['title'], 'ООО Тест', purchase['method'], purchase['published'],
                        purchase['submission_end'], '{}', saved.REG))
            db.execute('DELETE FROM docs WHERE reg_number=?', (saved.REG,))
            for doc in result['docs']:
                metadata = dict(doc['meta'])
                metadata.pop('official_publication_evidence', None)
                db.execute('INSERT INTO docs(reg_number,name,url,doc_type,published_at,local_path,'
                           'status,metadata_json,final_url) VALUES(?,?,?,?,?,?,?,?,?)',
                           (saved.REG, doc['name'], doc['url'], doc['type'], doc['published'],
                            doc['local'], doc['status'], json.dumps(metadata), doc['final_url']))
        return result

    def assert_both_paths(self, expected, categories=('protocols', 'dates'), *, seed=True):
        if seed:
            self.seed_collected()
        first = asyncio.run(general.inspect_one(self.ns, saved.REG))
        repeated = asyncio.run(general.inspect_one(self.ns, saved.REG))
        selected = asyncio.run(thematic.independent_review(self.ns, saved.REG, list(categories)))
        self.assertEqual((first[0], first[1]['new_findings']), ('completed', expected))
        self.assertEqual(repeated[1]['new_findings'], 0)
        self.assertEqual((selected[0], len(selected[1])), ('completed', expected))
        with self.connect() as db:
            found = [dict(row) for row in db.execute('SELECT * FROM findings')]
            self.assertEqual(load_counts(db, [saved.REG]), {saved.REG: expected})
            self.assertEqual(db.execute('SELECT violations FROM purchases').fetchone()[0], expected)
        self.assertEqual({row['code'] for row in found},
                         {row['code'] for row in selected[1]})
        return found

    def test_protocol_kind_after_subject_reaches_both_paths_once(self):
        self.write_original(
            'Протокол № 1. Дата подписания протокола рассмотрения заявок: 06.10.2026. '
            'Дата размещения протокола рассмотрения заявок в ЕИС: 13.10.2026. '
            'Комиссия рассмотрела заявки участников закупки.')
        found = self.assert_both_paths(1)
        self.assertEqual(found[0]['code'], '223-4-12-PROTOCOL-PUBLICATION-LATE')

    def test_eis_before_protocol_subject_reaches_both_paths(self):
        self.write_original(
            'Протокол № 1. Дата подписания итогового протокола: 06.10.2026. '
            'Дата размещения в ЕИС итогового протокола: 13.10.2026. '
            'Результаты закупки опубликованы в установленном порядке.')
        self.assert_both_paths(1)

    def test_expanded_signature_can_use_hash_verified_protocol_xml(self):
        self.write_archive(self.make_xml())
        self.write_original(
            'Протокол № 1. Дата подписания протокола рассмотрения заявок: 06.10.2026. '
            'Комиссия рассмотрела заявки участников закупки, все заявки допущены.')
        found = self.assert_both_paths(1)
        self.assertIn('SHA256 XML', found[0]['evidence'])

    def test_extended_request_and_eis_prefix_labels_reach_both_paths(self):
        shutil.rmtree(self.root / 'eis223_official_archive_inbox' / saved.REG)
        self.write_archive(self.make_xml('purchaseNotice', version=2,
            filename='Разъяснение на запрос № 1.docx'))
        self.write_original(
            'Номер запроса: 1. '
            'Дата поступления запроса о даче разъяснений положений документации: 06.10.2026. '
            'Дата размещения в ЕИС разъяснений положений документации: 13.10.2026. '
            'Предоставлен ответ на поступивший запрос участника закупки.')
        found = self.assert_both_paths(1)
        self.assertEqual(found[0]['code'], '223-3.2-3-CLARIFICATION-RESPONSE-LATE')

    def test_extended_request_split_across_verified_originals_reaches_both_paths(self):
        answer_uid = 'A' * 32
        shutil.rmtree(self.root / 'eis223_official_archive_inbox' / saved.REG)
        self.write_archive(self.make_xml('purchaseNotice', version=2,
            filename='Запрос разъяснений № 1.docx'))
        self.write_original(
            'Номер запроса: 1. '
            'Дата поступления запроса на предоставление разъяснений положений документации: '
            '06.10.2026. Запрос поступил от участника закупки.')
        self.write_archive(self.make_xml('purchaseNotice', version=3, uid=answer_uid,
            filename='Разъяснение на запрос № 1.docx'))
        self.write_original(
            'Номер запроса: 1. '
            'Дата размещения в ЕИС разъяснений положений документации: 13.10.2026. '
            'Предоставлен ответ на поступивший запрос участника закупки.', uid=answer_uid)
        found = self.assert_both_paths(1)
        self.assertEqual(found[0]['code'], '223-3.2-3-CLARIFICATION-CROSS-DOC-LATE')

    def test_conflicting_explicit_protocol_kinds_remain_unconfirmed(self):
        self.write_original(
            'Протокол № 1. Дата подписания протокола рассмотрения заявок: 06.10.2026. '
            'Дата размещения итогового протокола в ЕИС: 13.10.2026. '
            'Комиссия рассмотрела заявки участников закупки.')
        self.assert_both_paths(0)

    def test_tampered_original_cannot_promote_previously_extracted_dates(self):
        self.write_original(
            'Протокол № 1. Дата подписания протокола рассмотрения заявок: 06.10.2026. '
            'Дата размещения протокола рассмотрения заявок в ЕИС: 13.10.2026. '
            'Комиссия рассмотрела заявки участников закупки.')
        self.seed_collected()
        data = self.original_path.read_bytes()
        self.original_path.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
        first = asyncio.run(general.inspect_one(self.ns, saved.REG))
        selected = asyncio.run(thematic.independent_review(self.ns, saved.REG, ['protocols']))
        self.assertEqual(first[0], 'no_readable_documents')
        self.assertEqual(first[1].get('new_findings', 0), 0)
        self.assertEqual(selected[1], [])
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM findings').fetchone()[0], 0)
            self.assertEqual(load_counts(db, [saved.REG]), {saved.REG: 0})


if __name__ == '__main__':
    unittest.main()
