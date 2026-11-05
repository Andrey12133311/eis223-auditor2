"""Exact inspection topics share proof gates with thematic document review."""
import asyncio
from copy import deepcopy
from pathlib import Path
import json
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import UUID

import httpx
from fastapi import FastAPI

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
import v342_thematic_queue as theme
import v332_all_procurements_audit as general
from vp_inspection_criteria import CRITERIA
from v372_automatic_legal_selftest import official


class InspectionTopics(unittest.TestCase):
    def test_all_new_pickers_follow_the_nineteen_sheet_rows(self):
        self.assertEqual(list(theme.CATEGORIES),[row.key for row in CRITERIA])
        for index,row in enumerate(CRITERIA,1):
            label=theme.CATEGORIES[row.key]
            self.assertIn(row.title,label)
            self.assertIn(row.law,label)
            self.assertTrue(label.startswith(str(index)+". "))
        self.assertNotIn("docs",theme.CATEGORIES)
        self.assertEqual(theme.CATEGORY_LABELS["docs"],theme.LEGACY_CATEGORIES["docs"])
        self.assertEqual(theme.ordered_categories(["sme_payment","clarifications"]),
                         ["clarifications","sme_payment"])

    def test_staff_and_manager_can_select_all_rows_and_keep_legacy_templates(self):
        from scripts.v370_staff_workbench import Template,Preferences
        from scripts.v370_manager_campaigns import CampaignCreate
        keys=list(theme.CATEGORIES)
        self.assertEqual(Template(name="Все критерии",categories=keys).categories,keys)
        self.assertEqual(Preferences(pinned_legal_categories=keys).pinned_legal_categories,keys)
        self.assertEqual(Template(name="Сохранённая проверка",categories=["dates"]).categories,["dates"])
        assignment=CampaignCreate(assignee_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
            title="Проверка по листу",categories=keys,legal_forms=["ООО"],target_violations=1)
        self.assertEqual(assignment.categories,keys)

    def test_exact_rows_do_not_share_every_finding_from_a_broad_group(self):
        clarification={"code":"223-3.2-3-CLARIFICATION-RESPONSE-LATE",
            "title":"Ответ на запрос разъяснений размещён с нарушением срока",
            "law":"223-ФЗ, ст. 3.2 ч. 3"}
        self.assertEqual(theme.match_legal_category(clarification,["clarifications"]),"clarifications")
        for other in ("notice_deadlines","amendments","post_deadline_extension","notice_content"):
            self.assertIsNone(theme.match_legal_category(clarification,[other]))
        # Source wording can mention all these topics without changing the rule.
        generic={"code":"unclassified-rule","title":"Проверка документов",
            "law":"223-ФЗ, ст. 4 ч. 8",
            "evidence":"Состав извещения, товарный знак, срок оплаты, МСП"}
        self.assertIsNone(theme.match_legal_category(generic,list(theme.CATEGORIES)))
        self.assertEqual(theme.match_legal_category(clarification,["dates"]),"dates")


class ThematicProofAndSheet(unittest.IsolatedAsyncioTestCase):
    async def test_start_accepts_nineteen_selected_rows_and_saves_sheet_order(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"thematic.sqlite"
            ns={"conn":lambda:sqlite3.connect(path),"app":FastAPI()}
            theme.install(ns)
            keys=list(theme.CATEGORIES)
            with patch.object(theme.ownership,"enabled",return_value=False):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ns["app"]),
                                             base_url="http://localhost") as client:
                    response=await client.post("/api/thematic/start",json={
                        "categories":list(reversed(keys)),"legal_forms":["ООО"]})
            self.assertEqual(response.status_code,200,response.text)
            with sqlite3.connect(path) as c:
                selected=json.loads(c.execute("SELECT categories FROM thematic_batches WHERE id=?",
                    (response.json()["id"],)).fetchone()[0])
            self.assertEqual(selected,keys)

    async def review(self,categories,*,unverified=False,save=None,finding_changes=None):
        doc=official("Разъяснение.pdf","Дата поступления запроса: 01.10.2026. Ответ размещён 07.10.2026.")
        if unverified:doc["meta"]["source"]="existing_local_unverified"
        purchase={"reg_number":"32616412317","customer":"ООО Пример","method":"Открытый конкурс"}
        finding={"code":"223-3.2-3-CLARIFICATION-RESPONSE-LATE","kind":"violation",
            "title":"Ответ на разъяснение размещён с нарушением срока",
            "law":"223-ФЗ, ст. 3.2 ч. 3","evidence":doc["text"],
            "document":doc["name"],"source_url":doc["meta"]["source_url"]}
        finding.update(finding_changes or {})
        source={"docs":[doc],"unreadable":0,"source_incomplete":False,
            "p":{},"card":{},"detail":{},"original_received":True,"source_error":None}
        ns={"analyze":lambda p,d:[deepcopy(finding)],"build_checks":lambda *args:[]}
        if save is not None:ns["_vp_save_snapshot"]=save
        with patch.object(general,"existing",return_value=(purchase,[])), \
             patch.object(general,"collect_saved_evidence",return_value=source):
            return await theme.independent_review(ns,purchase["reg_number"],categories)

    async def test_selected_exact_criterion_keeps_confirmed_source(self):
        state,findings=await self.review(["clarifications"])
        self.assertEqual(state,"completed")
        self.assertEqual([item["category"] for item in findings],["clarifications"])
        self.assertEqual(findings[0]["document"],"Разъяснение.pdf")
        self.assertEqual(await self.review(["notice_deadlines"]),("completed",[]))

    async def test_unverified_original_cannot_establish_a_canonical_finding(self):
        state,findings=await self.review(["clarifications"],unverified=True)
        self.assertNotEqual(state,"completed")
        self.assertEqual(findings,[])

    async def test_name_and_url_alone_do_not_establish_exact_source_evidence(self):
        self.assertEqual(await self.review(["clarifications"],finding_changes={
            "evidence":"Сгенерированное утверждение о просрочке без цитаты оригинала"}),
            ("completed",[]))

    async def test_unconfirmed_sme_status_does_not_establish_sme_violation(self):
        self.assertEqual(await self.review(["sme_payment"],finding_changes={
            "code":"v337-sme_payment","law":"ПП РФ № 1352, п. 28"}),
            ("completed",[]))

    async def test_legacy_after_deadline_signal_does_not_become_exact_row_violation(self):
        self.assertEqual(await self.review(["post_deadline_extension"],finding_changes={
            "code":"223-4-11-CHANGE-AFTER-BID-DEADLINE","law":"223-ФЗ, ст. 4 ч. 11"}),
            ("completed",[]))

    async def test_sheet_receives_all_outcomes_even_when_campaign_does_not_select_them(self):
        calls=[]
        def save(p,docs,findings,checks,detail,card):
            calls.append((p,docs,findings,checks,detail,card))
        self.assertEqual(await self.review(["notice_deadlines"],save=save),("completed",[]))
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0][2][0]["code"],"223-3.2-3-CLARIFICATION-RESPONSE-LATE")

    async def test_sheet_save_failure_retries_instead_of_claiming_completion(self):
        def unavailable(*args):raise OSError("snapshot could not be saved")
        with self.assertRaisesRegex(OSError,"snapshot"):
            await self.review(["clarifications"],save=unavailable)


if __name__=="__main__":
    unittest.main()
