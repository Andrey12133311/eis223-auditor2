"""Synthetic source-chain regressions, never evidence about a real procurement.

The lotData field names follow EIS 223-FZ TFF 16.3 Appendix 2 pp. 21–22.
The XML subset is transport/hash verified, not advertised as full XSD validation.
"""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import sys
import unittest
from xml.etree import ElementTree as ET
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import v298_eis_only as incoming
import v324_official_price as text_prices
import v332_all_procurements_audit as general
import v336_legal_matrix as matrix
import v341_eis_facts as facts
import v342_thematic_queue as thematic
import test_bid_security_price_basis as price_fixture
from test_bid_security_price_basis import PURCHASE, BID, original
import test_general_audit_recovery as database_fixture
import test_receipt_original_parity as receipt_fixture

GENERIC = 'Начальная (максимальная) цена: 195 167,20 руб.'
EXPLICIT = 'Начальная (максимальная) цена договора: 195 167,20 руб.'
UNIT = 'Начальная сумма цен единиц товаров, работ, услуг: 195 167,20 руб.'


class PriceMeaningTests(unittest.TestCase):
    def test_generic_initial_price_heading_is_not_explicit_contract_nmcd(self):
        self.assertIsNone(matrix.verified_price(PURCHASE, [original(GENERIC + '\n' + BID)]))

    def test_equal_number_does_not_hide_unit_price_context(self):
        body = GENERIC + '\n' + UNIT + '\n' + BID
        self.assertEqual(matrix.security_findings(PURCHASE, [original(body)])[0], [])

    def test_saved_kind_survives_general_context_adapter(self):
        row = {'reg_number': PURCHASE['reg'], 'price_num': PURCHASE['price_num'],
               'card_fields_json': json.dumps({'summary': {'price': '195167.20',
                    'price_num': 195167.2, 'price_kind': 'total_unit_prices'}})}
        self.assertEqual(general.as_procurement(row).get('price_kind'), 'total_unit_prices')

    def test_soap_ignores_price_named_fields_outside_lot_context(self):
        xml = ET.fromstring('<purchaseNoticeData><attachments><document><initialSum>195167.20'
                            '</initialSum></document></attachments></purchaseNoticeData>')
        self.assertEqual(incoming.verified_xml_nmcd(xml), '')

    def test_soap_multiple_lots_are_ambiguous_even_at_equal_amount(self):
        lot = '<lot><lotData><initialSum>195167.20</initialSum></lotData></lot>'
        xml = ET.fromstring('<purchaseNoticeData><lots>' + lot * 2 + '</lots></purchaseNoticeData>')
        self.assertEqual(incoming.verified_xml_nmcd(xml), '')

    def test_soap_unit_qualifier_does_not_become_nmcd(self):
        xml = ET.fromstring('<purchaseNoticeData><lots><lot><lotData><initialSum>195167.20'
                            '</initialSum><initialSumInfo>' + UNIT + '</initialSumInfo>'
                            '</lotData></lot></lots></purchaseNoticeData>')
        self.assertEqual(incoming.verified_xml_nmcd(xml), '')

    def test_only_documented_scalar_soap_price_shapes_qualify(self):
        invalid = (
            '<initialSum><value>195167.20</value></initialSum>',
            '<initialSum><x:value xmlns:x="urn:foreign">195167.20</x:value></initialSum>',
            '<initialSum>195167.20<x:value xmlns:x="urn:foreign">195167.20</x:value></initialSum>',
            '<initialSumInfo><initialSum>195167.20</initialSum></initialSumInfo>',
            '<initialSum>195167.20</initialSum><initialSum>195167.20</initialSum>',
            '<initialSum>195167.20</initialSum><x:initialSum xmlns:x="urn:foreign">195167.20</x:initialSum>',
            '<currency><digitalCode>840</digitalCode></currency><initialSum>195167.20</initialSum>',
            '<currency><x:code xmlns:x="urn:foreign">643</x:code></currency><initialSum>195167.20</initialSum>',
        )
        for fields in invalid:
            with self.subTest(fields=fields):
                xml=ET.fromstring('<purchaseNoticeData><lots><lot><lotData>'+fields+'</lotData></lot></lots></purchaseNoticeData>')
                self.assertEqual(incoming.verified_xml_nmcd(xml), '')
        for currency in ('<letterCode>RUB</letterCode>', '<digitalCode>643</digitalCode>'):
            xml=ET.fromstring('<purchaseNoticeData><lots><lot><lotData><currency>'+currency+
                             '</currency><initialSum>195167.20</initialSum></lotData></lot></lots></purchaseNoticeData>')
            self.assertEqual(incoming.verified_xml_nmcd(xml), '195167.20')

    def test_foreign_namespace_lot_collisions_remain_unknown(self):
        good='<lot><lotData><initialSum>195167.20</initialSum></lotData></lot>'
        bodies=('<lots>'+good+'<x:lot xmlns:x="urn:foreign"/></lots>',
                '<lots>'+good+'</lots><x:lots xmlns:x="urn:foreign"/>',
                '<lots><lot><lotData><initialSum>195167.20</initialSum></lotData>'
                '<x:lotData xmlns:x="urn:foreign"/></lot></lots>')
        for body in bodies:
            self.assertEqual(incoming.verified_xml_nmcd(ET.fromstring('<purchaseNoticeData>'+body+'</purchaseNoticeData>')), '')

    def test_cancelled_or_ambiguous_lot_status_never_confirms_nmcd(self):
        for flag in ('<cancelled>true</cancelled>','<cancelled>invalid</cancelled>',
                     '<cancelled>false</cancelled><cancelled>true</cancelled>',
                     '<x:cancelled xmlns:x="urn:foreign">false</x:cancelled><cancelled>true</cancelled>'):
            xml=ET.fromstring('<purchaseNoticeData><lots><lot>'+flag+
                             '<lotData><initialSum>195167.20</initialSum></lotData></lot></lots></purchaseNoticeData>')
            self.assertEqual(incoming.verified_xml_nmcd(xml),'')

    def test_all_unit_prices_qualifier_remains_non_nmcd(self):
        for label in ('Начальная сумма цен единиц','Начальная сумма цен всех единиц'):
            xml=ET.fromstring('<purchaseNoticeData><lots><lot><lotData><initialSum>195167.20</initialSum>'
                             '<initialSumInfo>'+label+' товаров, работ, услуг</initialSumInfo></lotData></lot></lots></purchaseNoticeData>')
            self.assertEqual(incoming.verified_xml_price(xml)['price_kind'],'total_unit_prices')
            self.assertEqual(incoming.verified_xml_nmcd(xml),'')

    def test_generic_card_labels_cannot_be_claimed_as_nmcd(self):
        for label in ('Начальная (максимальная) цена','Начальная максимальная цена','Начальная цена лота'):
            with self.assertRaises(ValueError):
                facts.validate_prices([{'price':'195167.20','price_kind':'nmcd','price_label':label}])

    def test_saved_card_evidence_revalidates_price_label(self):
        for label, expected in (('Начальная (максимальная) цена',False), ('',False), ('НМЦД',True)):
            row={**PURCHASE,'reg_number':PURCHASE['reg'],'card_fields_json':json.dumps({
                'price_evidence':{'source':'official_eis_card','price_kind':'nmcd','price':'195167.20',
                    'price_label':label,'source_url':price_fixture.CARD}})}
            p=general.as_procurement(row)
            found=matrix.security_findings({**p,'method':PURCHASE['method'],'published':PURCHASE['published']},[original(BID)])[0]
            self.assertEqual(bool(found),expected)

    def test_typed_label_is_not_inferred_from_neighboring_nmcd_prose(self):
        body=GENERIC+' Условия расчета НМЦД см. в отдельном разделе.'
        self.assertEqual(text_prices.prices_in_text(body,typed=True)[0][3], 'unknown')
        self.assertIsNone(matrix.verified_price(PURCHASE,[original(body)]))

    def test_unknown_or_ceiling_originals_cannot_disappear_from_conflicts(self):
        for other in ('Начальная (максимальная) цена: 40 400 000 рублей.',
                      'Максимальное значение цены договора: 40 400 000 рублей.'):
            for docs in ([original(EXPLICIT+'\n'+other+'\n'+BID)],
                         [original(other+'\n'+EXPLICIT+'\n'+BID)],
                         [original(EXPLICIT+'\n'+BID),original(other)]):
                self.assertIsNone(matrix.verified_price(PURCHASE,docs))

    def test_missing_numeric_cache_keeps_generic_card_amount_conflict(self):
        p={**PURCHASE,'price_num':None,'verified_price_source':{
            'kind':'nmcd','price':'195167.20','source':price_fixture.CARD}}
        docs=[original('Начальная (максимальная) цена: 40 400 000 рублей.\n'+BID)]
        self.assertIsNone(matrix.verified_price(p,docs))
        self.assertEqual(matrix.security_findings(p,docs)[0],[])

    def test_unverified_saved_evidence_cannot_veto_explicit_original(self):
        for source in ('https://example.invalid/price',price_fixture.CARD.replace(PURCHASE['reg'],'32619000999')):
            card={'summary':{'price_kind':'nmcd'},'price_evidence':{'source':'official_eis_card',
                  'source_url':source,'price':'195167.20','price_kind':'nmcd','price_label':'Начальная (максимальная) цена'}}
            p=general.as_procurement({'reg_number':PURCHASE['reg'],'price_num':195167.2,'card_fields_json':json.dumps(card)})
            self.assertEqual(p.get('price_kind'),'nmcd')
            self.assertEqual(len(matrix.security_findings({**PURCHASE,**p,'method':PURCHASE['method'],
                'published':PURCHASE['published']},[original(EXPLICIT+'\n'+BID)])[0]),1)

    def test_review_diagnostic_is_bounded_and_untrusted_strings_are_ignored(self):
        import audit_funnel
        for kind,reason in (('total_unit_prices','price_basis_non_nmcd'),('unknown','verified_nmcd_missing')):
            found,checks=matrix.security_findings({**PURCHASE,'price_kind':kind},[original(BID)])
            self.assertEqual(found,[])
            self.assertEqual(checks[0]['status'],'check')
            self.assertEqual(audit_funnel._review_reason(checks[0]),reason)
        row={'field_path':'v336-bid-security-needs-verified-nmcd','price_basis_reason':'private arbitrary text'}
        self.assertEqual(audit_funnel._review_reason(row),'verified_nmcd_missing')

    def test_explicit_contract_price_keeps_positive(self):
        self.assertEqual(len(matrix.security_findings(PURCHASE, [original(EXPLICIT + '\n' + BID)])[0]), 1)


class CurrentPriceChain(database_fixture.DatabaseFixture, unittest.IsolatedAsyncioTestCase):
    soap = receipt_fixture.SavedCardContext.soap
    stale_purchase = receipt_fixture.SavedCardContext.stale_purchase
    source = receipt_fixture.SavedCardContext.source

    def setUp(self):
        super().setUp()
        with self.connect() as con:
            for declaration in ("source TEXT", "url TEXT", "customer_inn TEXT", "price TEXT", "price_num REAL",
                                "submission_start TEXT", "deadline TEXT", "card_fields_json TEXT"):
                con.execute("ALTER TABLE purchases ADD COLUMN " + declaration)
        from v311_verified_customer import _initialize
        _initialize(self.ns)
        facts.initialize(self.ns)
        self.observed = []
        def analyze(p, docs):
            facts.enrich(self.ns, p, docs)
            self.observed.append(deepcopy(p))
            return matrix.security_findings(p, docs)[0]
        def checks(p, detail, card, docs):
            facts.enrich(self.ns, p, docs, card, detail)
            return matrix.security_findings(p, docs)[1]
        self.ns.update(analyze=analyze, build_checks=checks)

    def typed_soap(self, reg, content):
        path, receipt = self.soap(reg, published='2026-10-19T06:16:00+03:00', price='195167.20')
        with zipfile.ZipFile(path) as archive:
            name = archive.namelist()[0]
            raw = archive.read(name)
        raw = raw.replace(b'<initialSum>195167.20</initialSum>', content.encode())
        import eis223_soap_xml
        # Keep the prior valid revision as historical evidence; the typed
        # revision is explicitly newer, not selected by file/hash ordering.
        raw = raw.replace(b'<version>1</version>', b'<version>2</version>')
        record, _ = eis223_soap_xml.parse_document(raw, name)
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr(name, raw)
        data = stream.getvalue()
        digest = hashlib.sha256(data).hexdigest()
        (path.parent / (digest + '.zip')).write_bytes(data)
        (path.parent / (digest + '.json')).write_text(json.dumps({
            'reg_number': reg, 'source': 'official_eis_soap_archive',
            'archive_sha256': digest, 'notices': [record]}))

    def chain(self, number, *, content=None, body=GENERIC, saved_kind=None):
        reg = self.purchase(number)
        self.document(reg, body + '\n' + BID)
        with self.connect() as con:
            con.execute("UPDATE docs SET name='Документация.docx' WHERE id=(SELECT MAX(id) FROM docs)")
        card = {'summary': {'price': '195167.20', 'price_num': 195167.2}}
        if saved_kind:
            card['summary']['price_kind'] = saved_kind
        self.stale_purchase(reg, method='Открытый конкурс', published_at='2026-10-19',
                            price='195167.20', price_num=195167.2, card_fields_json=json.dumps(card))
        if content is not None:
            self.typed_soap(reg, content)
        return reg

    async def test_saved_non_nmcd_kind_reaches_both_engines_without_soap(self):
        reg = self.chain(50, saved_kind='total_unit_prices', body=EXPLICIT)
        source = await self.source(reg)
        facts.enrich(self.ns, source['p'], source['docs'], source['card'], source['detail'])
        self.assertEqual(source['p'].get('price_kind'), 'total_unit_prices')
        self.assertEqual(matrix.security_findings(source['p'], source['docs'])[0], [])
        self.assertEqual((await general.inspect_one(self.ns, reg))[1]['new_findings'], 0)
        self.assertEqual((await thematic.independent_review(self.ns, reg, ['security']))[1], [])

    async def test_current_unit_soap_reaches_both_engines_without_nmcd_promotion(self):
        reg = self.chain(51, content='<initialSum>195167.20</initialSum><initialSumInfo>' + UNIT + '</initialSumInfo>')
        source = await self.source(reg)
        facts.enrich(self.ns, source['p'], source['docs'], source['card'], source['detail'])
        self.assertNotEqual(source['p'].get('price_kind'), 'nmcd')
        self.assertEqual(matrix.security_findings(source['p'], source['docs'])[0], [])
        self.assertEqual((await general.inspect_one(self.ns, reg))[1]['new_findings'], 0)
        self.assertEqual((await thematic.independent_review(self.ns, reg, ['security']))[1], [])

    async def test_current_commodity_and_maximum_are_not_low_nmcd(self):
        reg = self.chain(52, content='<commodityPrice>195167.20</commodityPrice>'
                         '<maxContractPrice>40400000.00</maxContractPrice>')
        source = await self.source(reg)
        facts.enrich(self.ns, source['p'], source['docs'], source['card'], source['detail'])
        self.assertNotEqual(source['p'].get('price_kind'), 'nmcd')
        self.assertEqual(matrix.security_findings(source['p'], source['docs'])[0], [])
        self.assertEqual((await general.inspect_one(self.ns, reg))[1]['new_findings'], 0)
        self.assertEqual((await thematic.independent_review(self.ns, reg, ['security']))[1], [])

    async def test_newer_explicit_nmcd_replaces_saved_unit_or_ceiling_context(self):
        for number,kind in enumerate(('total_unit_prices','maximum_contract_value'),60):
            reg=self.chain(number,content='<initialSum>195167.20</initialSum>',body=EXPLICIT,saved_kind=kind)
            self.assertEqual((await general.inspect_one(self.ns,reg))[1]['new_findings'],1)
            self.assertEqual(len((await thematic.independent_review(self.ns,reg,['security']))[1]),1)
            self.assertTrue(all(p['price_kind']=='nmcd' for p in self.observed if p['reg']==reg))

    async def test_newer_unit_or_unknown_xml_overrides_older_explicit_nmcd_original(self):
        for number,content in enumerate(('<initialSum>195167.20</initialSum><initialSumInfo>'+UNIT+'</initialSumInfo>',
                                         '<price>195167.20</price>'),62):
            reg=self.chain(number,content=content,body=EXPLICIT,saved_kind='nmcd')
            self.assertEqual((await general.inspect_one(self.ns,reg))[1]['new_findings'],0)
            selected=await thematic.independent_review(self.ns,reg,['security'])
            self.assertEqual(selected[1],[])
            source=await self.source(reg)
            facts.enrich(self.ns,source['p'],source['docs'],source['card'],source['detail'])
            self.assertTrue(matrix.security_findings(source['p'],source['docs'])[1])

    async def test_generic_saved_card_alternative_cannot_disappear_from_conflict(self):
        reg=self.chain(64,body=BID)
        payload={'source_page':price_fixture.CARD.replace(PURCHASE['reg'],reg),'captured_at':'2026-10-19T08:00:00+03:00',
                 'fields':{},'prices':[{'price':'195167.20','price_kind':'nmcd','price_label':'НМЦД'},
                                       {'price':'40400000.00','price_kind':'nmcd','price_label':'Начальная (максимальная) цена'}]}
        with self.connect() as con:
            con.execute('INSERT INTO v341_card_facts VALUES(?,?,?,?)',(reg,payload['source_page'],payload['captured_at'],json.dumps(payload)))
        self.assertEqual((await general.inspect_one(self.ns,reg))[1]['new_findings'],0)
        self.assertEqual((await thematic.independent_review(self.ns,reg,['security']))[1],[])

    async def test_current_public_typed_price_overrides_saved_kind_in_both_engines(self):
        reg=self.chain(68,body=EXPLICIT,saved_kind='nmcd')
        path=self.folder/'eis223_public_browser_intake'/reg/'manifest.json'
        manifest=json.loads(path.read_text())
        manifest.update(price='195167.20',price_kind='total_unit_prices')
        path.write_text(json.dumps(manifest))
        self.assertEqual((await general.inspect_one(self.ns,reg))[1]['new_findings'],0)
        self.assertEqual((await thematic.independent_review(self.ns,reg,['security']))[1],[])
        self.assertTrue(all(p['price_kind']=='total_unit_prices' for p in self.observed))

    async def test_real_legacy_price_conflict_vetoes_old_proof_in_both_engines(self):
        import v338_card_prices
        v338_card_prices.initialize(self.ns)
        for number,with_snapshot in enumerate((False,True),70):
            reg=self.chain(number,body=BID)
            source=price_fixture.CARD.replace(PURCHASE['reg'],reg)
            first=v338_card_prices.save_card_price(self.ns,reg,'195167.20',195167.2,'nmcd','НМЦД',source)
            self.assertFalse(first.get('conflict'))
            if with_snapshot:
                payload={'source_page':source,'captured_at':'2026-10-18T08:00:00+03:00','fields':{},
                         'prices':facts.validate_prices([{'price':'195167.20','price_kind':'nmcd','price_label':'НМЦД'}])}
                with self.connect() as con:
                    con.execute('INSERT INTO v341_card_facts VALUES(?,?,?,?)',(reg,source,payload['captured_at'],json.dumps(payload)))
            second=v338_card_prices.save_card_price(self.ns,reg,'195167.20',195167.2,'total_unit_prices',
                                                    'Начальная сумма цен единиц',source)
            self.assertTrue(second['conflict'])
            self.assertEqual((await general.inspect_one(self.ns,reg))[1]['new_findings'],0)
            self.assertEqual((await thematic.independent_review(self.ns,reg,['security']))[1],[])
            self.assertTrue(all('price' in p['_official_context_conflicts'] for p in self.observed if p['reg']==reg))
            # The legacy first timestamp does not date its later alternative.
            self.typed_soap(reg,'<initialSum>195167.20</initialSum>')
            current=await self.source(reg)
            facts.enrich(self.ns,current['p'],current['docs'],current['card'],current['detail'])
            self.assertIn('price',current['p']['_official_context_conflicts'])
            self.assertEqual(matrix.security_findings(current['p'],current['docs'])[0],[])

    async def test_current_price_persistence_retains_kind_and_historical_state(self):
        reg=self.chain(65,content='<initialSum>195167.20</initialSum><initialSumInfo>'+UNIT+'</initialSumInfo>',
                       body=EXPLICIT,saved_kind='nmcd')
        with self.connect() as con:
            con.execute("INSERT INTO findings(reg_number,code,kind,title,evidence) VALUES(?,'HISTORICAL','violation','Preserved event','Preserved evidence')",(reg,))
            con.execute('CREATE TABLE account_manager_finding_reviews (reg_number TEXT,status TEXT,version INTEGER)')
            con.execute("INSERT INTO account_manager_finding_reviews VALUES(?,'approved',3)",(reg,))
            prior_findings=list(map(tuple,con.execute('SELECT * FROM findings')))
            prior_reviews=list(map(tuple,con.execute('SELECT * FROM account_manager_finding_reviews')))
        files={path:path.read_bytes() for path in self.folder.rglob('*') if path.suffix in ('.zip','.json','.docx')}
        source=await self.source(reg)
        facts.enrich(self.ns,source['p'],source['docs'],source['card'],source['detail'])
        facts.persist_facts(self.ns,source['p'])
        with self.connect() as con:
            row=dict(con.execute('SELECT * FROM purchases WHERE reg_number=?',(reg,)).fetchone())
        self.assertEqual(general.as_procurement(row)['price_kind'],'total_unit_prices')
        self.assertEqual((await general.inspect_one(self.ns,reg))[1]['new_findings'],0)
        self.assertEqual((await thematic.independent_review(self.ns,reg,['security']))[1],[])
        with self.connect() as con:
            self.assertEqual(list(map(tuple,con.execute('SELECT * FROM findings'))),prior_findings)
            self.assertEqual(list(map(tuple,con.execute('SELECT * FROM account_manager_finding_reviews'))),prior_reviews)
        self.assertEqual({path:path.read_bytes() for path in files},files)
        records=[json.loads(line.split(' ',1)[1]) for line in self.output.getvalue().splitlines() if line.startswith('AUDIT_FUNNEL ')]
        self.assertTrue(any(r['reasons'].get('price_basis_non_nmcd') for r in records))

    async def test_malformed_saved_summary_does_not_crash_collection(self):
        reg=self.chain(66,body=EXPLICIT)
        with self.connect() as con:
            con.execute('UPDATE purchases SET card_fields_json=? WHERE reg_number=?',(json.dumps({'summary':[]}),reg))
        source=await self.source(reg)
        self.assertFalse(source['source_incomplete'])

    async def test_tampered_original_cannot_establish_a_fresh_finding(self):
        reg=self.chain(67,content='<initialSum>195167.20</initialSum>',body=EXPLICIT)
        with self.connect() as con:
            path=Path(con.execute("SELECT local_path FROM docs WHERE name='Документация.docx' AND reg_number=?",(reg,)).fetchone()[0])
        path.write_bytes(path.read_bytes()+b'tampered')
        result=await general.inspect_one(self.ns,reg)
        self.assertEqual(result[1].get('new_findings',0),0)
        self.assertEqual((await thematic.independent_review(self.ns,reg,['security']))[1],[])

    async def test_verified_current_nmcd_keeps_both_engines_positive(self):
        reg = self.chain(53, content='<initialSum>195167.20</initialSum>', body=EXPLICIT)
        self.assertEqual((await general.inspect_one(self.ns, reg))[1]['new_findings'], 1)
        self.assertEqual(len((await thematic.independent_review(self.ns, reg, ['security']))[1]), 1)


if __name__ == '__main__':
    unittest.main()
