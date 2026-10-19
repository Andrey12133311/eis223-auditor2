"""Synthetic end-to-end legal outcomes; these are not production finding counts.

Original receipts and bytes are real temporary fixtures. Card changes go through
existing validation/persistence and findings through the actual legal analyzer.
"""
import os
import unittest
from unittest.mock import patch

from test_general_audit_recovery import DatabaseFixture
import v332_all_procurements_audit as audit
import v338_card_prices as prices
import v341_eis_facts as facts
from v352_dashboard_truth import load_counts


class AutomaticLegalOutcomes(DatabaseFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        with self.connect() as db:
            db.execute("ALTER TABLE purchases ADD COLUMN price TEXT")
            db.execute("ALTER TABLE purchases ADD COLUMN price_num REAL")
            db.execute("ALTER TABLE purchases ADD COLUMN card_fields_json TEXT DEFAULT '{}'")
            db.execute("CREATE TABLE manager_decisions(id INTEGER PRIMARY KEY,reg TEXT,decision TEXT)")
        prices.initialize(self.ns)
        facts.initialize(self.ns)

    def save_method(self, reg, method):
        value=facts.validate_snapshot({
            "reg_number":reg,
            "source_page":"https://zakupki.gov.ru/223/purchase/public/purchase/info/common-info.html?regNumber="+reg,
            "fields":{"method":method},"field_evidence":{"method":"Способ закупки: "+method},"prices":[]})
        facts.save_snapshot(self.ns,value)

    async def run_claimed(self, reg):
        self.assertEqual(audit.claim_next(self.ns),reg)
        snapshot=await audit.file_stage(self.ns,"source",audit.review_watermark,self.ns,reg)
        state,detail=await audit.inspect_one(self.ns,reg)
        self.assertEqual(state,"completed")
        self.assertFalse(detail.pop("source_incomplete",False))
        audit.mark(self.ns,reg,state,{**detail,**snapshot})
        return detail

    def displayed(self, reg):
        with self.connect() as db:return load_counts(db,[reg])[reg]

    async def test_authoritative_method_correction_automatically_finds_one_real_episode(self):
        reg=self.purchase()
        self.save_method(reg,"Закупка у единственного поставщика")
        with self.connect() as db:db.execute("INSERT INTO manager_decisions VALUES(1,?,'approved')",(reg,))
        audit.initialize(self.ns)
        initial=await self.run_claimed(reg)
        self.assertEqual(initial["new_findings"],0)
        self.assertEqual((self.findings(reg),self.displayed(reg)),(0,0))
        self.assertEqual(audit.refresh_changed(self.ns),0)

        # Same original bytes, now the current official card establishes that
        # the competitive clarification rule applies. No threshold is changed.
        self.save_method(reg,"Открытый конкурс")
        self.assertEqual(audit.refresh_changed(self.ns),1)
        changed=await self.run_claimed(reg)
        self.assertEqual(changed["new_findings"],1)
        self.assertEqual((self.findings(reg),self.displayed(reg)),(1,1))
        with self.connect() as db:
            found=dict(db.execute("SELECT * FROM findings WHERE reg_number=? AND kind='violation'",(reg,)).fetchone())
            self.assertTrue(found["law"])
            self.assertIn("zakupki.gov.ru",found["source_url"])
            self.assertIn("2026",found["evidence"])
            self.assertEqual(db.execute("SELECT decision FROM manager_decisions WHERE id=1").fetchone()[0],"approved")

        self.save_method(reg,"Открытый конкурс")
        for original in self.contents:os.utime(original,None)
        for _ in range(3):self.assertEqual(audit.refresh_changed(self.ns),0)
        # A genuine later rule version can rerun, but not duplicate the episode.
        with patch.object(audit,"engine_signature",return_value="synthetic-new-rule-version"):
            self.assertEqual(audit.refresh_changed(self.ns),1)
            repeated=await self.run_claimed(reg)
            self.assertEqual(repeated["new_findings"],0)
            self.assertEqual(audit.refresh_changed(self.ns),0)
        self.assertEqual((self.findings(reg),self.displayed(reg)),(1,1))
        self.assertEqual(audit.progress(self.ns)["new_evidence_based_findings"],1)

    async def test_noncompetitive_source_corrections_do_not_inflate_findings(self):
        reg=self.purchase()
        self.save_method(reg,"Закупка у единственного поставщика")
        audit.initialize(self.ns)
        await self.run_claimed(reg)
        self.save_method(reg,"Закупка у единственного поставщика")
        self.assertEqual(audit.refresh_changed(self.ns),0)
        with patch.object(audit,"engine_signature",return_value="synthetic-rules-revision"):
            self.assertEqual(audit.refresh_changed(self.ns),1)
            result=await self.run_claimed(reg)
            self.assertEqual(result["new_findings"],0)
        self.assertEqual((self.findings(reg),self.displayed(reg)),(0,0))

    async def test_missing_required_dates_remain_review_after_automatic_rule_change(self):
        reg=self.purchase()
        # Replace the original fixture with another verified original lacking
        # the reply publication date. Keep receipt matching exact bytes.
        with self.connect() as db:db.execute("DELETE FROM docs WHERE reg_number=?",(reg,))
        self.originals[reg]=[]
        self.document(reg,"Дата поступления запроса разъяснения: 01.10.2026. Ответ подготовлен заказчиком.")
        self.save_method(reg,"Открытый конкурс")
        audit.initialize(self.ns)
        result=await self.run_claimed(reg)
        self.assertEqual(result["new_findings"],0)
        with patch.object(audit,"engine_signature",return_value="synthetic-rules-revision"):
            self.assertEqual(audit.refresh_changed(self.ns),1)
            result=await self.run_claimed(reg)
            self.assertEqual(result["new_findings"],0)
        self.assertEqual((self.findings(reg),self.displayed(reg)),(0,0))
        with self.connect() as db:
            self.assertGreater(db.execute("SELECT COUNT(*) FROM checks WHERE reg_number=?",(reg,)).fetchone()[0],0)

if __name__=="__main__":unittest.main()
