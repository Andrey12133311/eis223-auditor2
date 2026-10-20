"""Explicit TFF223 16.3 pp.78-80 fields, not fabricated legal event dates."""
import unittest
from eis223_soap_xml import parse_document, UnsupportedXmlType, PURCHASE_NS
from v294_eis_bridge import parse_notices
from test_explanation_quarantine_identity import archive, document, REG

FIELDS='''<guid>03253434-0000-0000-0000-000000000001</guid><purchaseRegNum>'''+REG+'''</purchaseRegNum>
<requestDate>2026-10-01T08:00:00+03:00</requestDate><creationDate>2026-10-02T09:00:00+03:00</creationDate>
<decisionDate>2026-10-05T11:00:00+03:00</decisionDate><publishDate>2026-10-09T12:00:00+03:00</publishDate><status>P</status>
<attachments><document><fileName>Разъяснение.pdf</fileName><url>https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA</url></document></attachments>'''

class ExplanationSupported(unittest.TestCase):
    def parse(self, fields=FIELDS):
        name,raw=document('explanation',fields)
        return parse_document(raw,name)[0]

    def test_documented_identity_dates_and_attachment_are_retained_separately(self):
        row=self.parse()
        self.assertEqual(row['xml_family'],'explanation');self.assertEqual(row['reg_number'],REG)
        self.assertTrue(row['request_received'].startswith('2026-10-01'))
        self.assertTrue(row['created'].startswith('2026-10-02'))
        self.assertTrue(row['decision'].startswith('2026-10-05'))
        self.assertTrue(row['publication'].startswith('2026-10-09'))
        self.assertEqual(row['documents_referenced'],1)
        self.assertEqual(row['documents'][0]['published'],row['publication'])
        self.assertNotIn('confirmed_violations',row)

    def test_explanation_only_archive_exposes_actual_file_reference(self):
        rows=parse_notices(archive(document('explanation',FIELDS)))
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['total_attachment_count'],1)
        self.assertEqual(rows[0]['unsupported_attachment_count'],0)

    def test_draft_never_becomes_supported_published_record(self):
        with self.assertRaises(UnsupportedXmlType): self.parse(FIELDS.replace('<status>P</status>','<status>F</status>'))

    def test_incomplete_dates_keep_existing_quarantine_behavior(self):
        for field in ('requestDate','creationDate','decisionDate','publishDate'):
            import re
            with self.subTest(field=field), self.assertRaises(UnsupportedXmlType):
                self.parse(re.sub('<'+field+'>.*?</'+field+'>','',FIELDS))

    def test_invalid_timestamp_is_not_replaced_by_creation_or_download_date(self):
        with self.assertRaises(UnsupportedXmlType):self.parse(FIELDS.replace('2026-10-09T12:00:00+03:00','2026-02-30T12:00:00+03:00'))

    def test_duplicate_and_foreign_namespace_dates_are_rejected(self):
        for value in (FIELDS+'<requestDate>2026-10-03T12:00:00</requestDate>',
                      FIELDS.replace('<requestDate>','<requestDate xmlns="https://foreign.invalid">')):
            with self.subTest(value=value), self.assertRaises(ValueError):self.parse(value)

    def test_foreign_registry_and_download_host_remain_rejected(self):
        for value in (FIELDS.replace(REG,'32619999999'),FIELDS.replace('https://zakupki.gov.ru','https://foreign.invalid')):
            with self.subTest(value=value), self.assertRaises(ValueError):self.parse(value)

    def test_supported_notice_is_not_replaced_by_explanation_in_mixed_archive(self):
        rows=parse_notices(archive(document('purchaseNotice',f'<registrationNumber>{REG}</registrationNumber>'),document('explanation',FIELDS)))
        self.assertEqual([r['xml_family'] for r in rows],['notice','explanation'])

if __name__=='__main__': unittest.main()
