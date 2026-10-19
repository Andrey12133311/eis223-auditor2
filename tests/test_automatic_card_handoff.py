"""Card updates use the durable semantic scheduler, never a parallel analyzer."""
import ast
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import v338_card_prices as cards
from v332_all_procurements_audit import CAMPAIGN
from v381_audit_scheduling import audit_signals


class AutomaticCardHandoff(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        path=Path(temp.name)/"fixture.sqlite3"
        self.ns={"conn":lambda:sqlite3.connect(path)}
        cards.initialize(self.ns)
        db=self.ns["conn"]()
        db.executescript("""
          CREATE TABLE purchases(reg_number TEXT PRIMARY KEY,price TEXT,price_num REAL);
          CREATE TABLE v332_audit_queue(campaign TEXT,reg TEXT,state TEXT,updated_at TEXT);
          CREATE TABLE findings(id INTEGER PRIMARY KEY,reg TEXT,evidence TEXT);
          CREATE TABLE manager_decisions(id INTEGER PRIMARY KEY,decision TEXT);
          INSERT INTO findings VALUES(1,'32616233661','retained evidence');
          INSERT INTO manager_decisions VALUES(1,'approved');
        """);db.commit();db.close()

    def insert(self,reg="32616233661",state="pending",campaign=CAMPAIGN,queue_state="completed"):
        db=self.ns["conn"]()
        db.execute("INSERT INTO v338_price_rechecks(reg,state,changed_at) VALUES(?,?,?)",(reg,state,"2026-10-19T12:00:00+00:00"))
        if campaign is not None:
            db.execute("INSERT INTO v332_audit_queue VALUES(?,?,?,?)",(campaign,reg,queue_state,"2026-10-18"))
        db.commit();db.close()

    def rows(self,table):
        db=self.ns["conn"]()
        try:return db.execute("SELECT * FROM "+table+" ORDER BY 1").fetchall()
        finally:db.close()

    def test_campaign_need_not_be_finished_and_handoff_is_not_completion(self):
        self.insert(queue_state="running")
        signals=audit_signals(self.ns);before=signals.version("general")
        queue=self.rows("v332_audit_queue")
        with patch("v332_all_procurements_audit.inspect_one",side_effect=AssertionError("parallel analyzer")):
            self.assertEqual(cards.handoff_card_rechecks(self.ns),1)
        self.assertGreater(signals.version("general"),before)
        self.assertEqual(self.rows("v338_price_rechecks")[0][1],"automatic")
        self.assertIsNone(self.rows("v338_price_rechecks")[0][3])
        self.assertEqual(self.rows("v332_audit_queue"),queue)
        summary=cards.price_summary(self.ns)
        self.assertEqual(summary["price_triggered_legal_rechecks_completed"],0)
        self.assertEqual(summary["price_triggered_legal_rechecks_automatically_monitored"],1)

    def test_replay_and_restart_do_not_create_another_analysis(self):
        self.insert()
        self.assertEqual(cards.handoff_card_rechecks(self.ns),1)
        self.assertEqual(cards.handoff_card_rechecks({"conn":self.ns["conn"]}),0)
        self.assertEqual(len(self.rows("v338_price_rechecks")),1)

    def test_only_existing_current_general_cohort_is_handed_off(self):
        self.insert("32616233661")
        self.insert("32616233662",campaign="old-campaign")
        self.insert("32616233663",campaign=None)
        self.assertEqual(cards.handoff_card_rechecks(self.ns),1)
        self.assertEqual([r[1] for r in self.rows("v338_price_rechecks")],["automatic","pending","pending"])
        self.assertEqual(len(self.rows("v332_audit_queue")),2)

    def test_bounded_dispatch_eventually_advances_late_rows(self):
        for n in range(70):self.insert(str(32616233661+n))
        self.assertEqual(cards.handoff_card_rechecks(self.ns,10000),32)
        self.assertEqual(cards.handoff_card_rechecks(self.ns,10000),32)
        self.assertEqual(cards.handoff_card_rechecks(self.ns,10000),6)
        self.assertEqual(cards.handoff_card_rechecks(self.ns,10000),0)

    def test_new_card_change_is_observed_again_without_resetting_audit(self):
        self.insert();cards.handoff_card_rechecks(self.ns)
        db=self.ns["conn"]();db.execute("UPDATE v338_price_rechecks SET state='pending',changed_at='later'");db.commit();db.close()
        self.assertEqual(cards.handoff_card_rechecks(self.ns),1)
        self.assertEqual(self.rows("v332_audit_queue")[0][2],"completed")

    def test_no_findings_history_or_manager_mutation(self):
        self.insert();findings=self.rows("findings");decisions=self.rows("manager_decisions")
        cards.handoff_card_rechecks(self.ns)
        self.assertEqual(self.rows("findings"),findings)
        self.assertEqual(self.rows("manager_decisions"),decisions)

    def test_missing_main_queue_defers_marker_safely(self):
        self.insert(campaign=None)
        db=self.ns["conn"]();db.execute("DROP TABLE v332_audit_queue");db.commit();db.close()
        self.assertEqual(cards.handoff_card_rechecks(self.ns),0)
        self.assertEqual(self.rows("v338_price_rechecks")[0][1],"pending")

    def test_worker_never_calls_inspect_or_global_progress(self):
        tree=ast.parse(Path(cards.__file__).read_text())
        worker=next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name=="recheck_after_card_price")
        names={n.id for n in ast.walk(worker) if isinstance(n,ast.Name)}
        self.assertIn("handoff_card_rechecks",names)
        self.assertNotIn("inspect_one",names)
        self.assertNotIn("progress",names)

if __name__=="__main__":unittest.main()
