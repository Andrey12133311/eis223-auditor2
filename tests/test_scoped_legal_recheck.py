"""Synthetic local-only regression coverage for explicit managed recheck scope."""
import contextlib
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
import scoped_legal_recheck as scoped

A="32616233661"
B="32616324151"
C="32616287360"
RUNNING="32616233001"
PENDING="32616233002"
NO_FILE="32616233003"
NO_PURCHASE="32616233004"
NO_QUEUE="32616233005"
UNKNOWN="32616233006"


class ScopedLegalRecheckTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="scoped-legal-recheck-")
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.path=self.root/"existing.sqlite"
        self.files={}
        with sqlite3.connect(self.path) as conn:
            conn.executescript("""
                CREATE TABLE purchases(reg_number TEXT PRIMARY KEY,documents_checked INTEGER,
                    violations INTEGER,checks_ok INTEGER,checks_attention INTEGER,title TEXT);
                CREATE TABLE docs(id INTEGER PRIMARY KEY,reg_number TEXT,local_path TEXT,status TEXT,
                    metadata_json TEXT,content BLOB);
                CREATE TABLE v332_audit_queue(campaign TEXT,reg TEXT,state TEXT,
                    documents_found INTEGER,documents_read INTEGER,documents_unreadable INTEGER,
                    verified_original_documents INTEGER,original_expected INTEGER,original_received INTEGER,
                    new_findings INTEGER,new_checks INTEGER,error TEXT,updated_at TEXT,audit_revision TEXT,
                    last_doc_id INTEGER,last_source_version TEXT,retry_count INTEGER,next_retry_at REAL,
                    last_result_token TEXT,last_counted_token TEXT,PRIMARY KEY(campaign,reg));
                CREATE TABLE findings(id INTEGER PRIMARY KEY,reg_number TEXT,body BLOB);
                CREATE TABLE checks(id INTEGER PRIMARY KEY,reg_number TEXT,body BLOB);
                CREATE TABLE manager_decisions(id INTEGER PRIMARY KEY,reg_number TEXT,body BLOB);
                CREATE TABLE finding_status_ledger(id INTEGER PRIMARY KEY,reg_number TEXT,body BLOB);
                CREATE TABLE v332_audit_run(campaign TEXT PRIMARY KEY,body BLOB);
            """)
            states={A:"completed",B:"failed",C:"no_readable_documents",RUNNING:"running",PENDING:"pending",
                    NO_FILE:"completed",NO_PURCHASE:"completed",NO_QUEUE:"completed",UNKNOWN:"cancelled"}
            for index,(reg,state) in enumerate(states.items(),1):
                if reg!=NO_PURCHASE:
                    conn.execute("INSERT INTO purchases VALUES(?,?,?,?,?,?)",(reg,11,5,3,2,"Original title"))
                if reg!=NO_QUEUE:
                    conn.execute("INSERT INTO v332_audit_queue VALUES("+",".join("?"*20)+")",
                        (scoped.CAMPAIGN,reg,state,13,11,2,9,12,10,5,6,"Retained error","old-at", "retained-rule",
                         123,"old-source",7,9999999999,"result-token","counted-token"))
                file=self.root/(reg+".pdf")
                if reg!=NO_FILE:file.write_bytes(b"synthetic original bytes\x00\xff")
                self.files[reg]=file
                conn.execute("INSERT INTO docs VALUES(?,?,?,?,?,?)",(index,reg,str(file),"checked","{\"original\":true}",b"preserved\x00"))
                for table in ("findings","checks","manager_decisions","finding_status_ledger"):
                    conn.execute("INSERT INTO "+table+" VALUES(?,?,?)",(index,reg,b"immutable\x00\xff"))
            conn.execute("INSERT INTO v332_audit_run VALUES(?,?)",(scoped.CAMPAIGN,b"baseline\x00"))
            conn.execute("INSERT INTO v332_audit_queue SELECT 'other-campaign',reg,state,documents_found,documents_read,"
                         "documents_unreadable,verified_original_documents,original_expected,original_received,"
                         "new_findings,new_checks,error,updated_at,audit_revision,last_doc_id,last_source_version,"
                         "retry_count,next_retry_at,last_result_token,last_counted_token FROM v332_audit_queue WHERE reg=?",(A,))

    def snapshot(self):
        with sqlite3.connect(self.path) as conn:
            tables=[row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            return {table:conn.execute('SELECT * FROM "'+table+'" ORDER BY rowid').fetchall() for table in tables}

    def queue(self,reg,campaign=scoped.CAMPAIGN):
        with sqlite3.connect(self.path) as conn:
            conn.row_factory=sqlite3.Row
            return dict(conn.execute("SELECT * FROM v332_audit_queue WHERE campaign=? AND reg=?",(campaign,reg)).fetchone())

    def test_default_preflight_is_read_only_and_reports_selected_candidates(self):
        before=self.path.read_bytes();snapshot=self.snapshot()
        files={reg:path.read_bytes() for reg,path in self.files.items() if path.exists()}
        with patch.object(Path,"read_bytes",side_effect=AssertionError("Do not read original bytes")), \
             patch("builtins.open",side_effect=AssertionError("Do not open original bytes")):
            report=scoped.recheck(self.path,[A,B])
        self.assertEqual(report["mode"],"dry-run")
        self.assertEqual((report["selected"],report["eligible"],report["queued"]),(2,2,0))
        self.assertEqual([item["state"] for item in report["items"]],["completed","failed"])
        self.assertEqual({item["action"] for item in report["items"]},{"would_queue"})
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(self.snapshot(),snapshot)
        self.assertEqual({reg:path.read_bytes() for reg,path in self.files.items() if path.exists()},files)

    def test_apply_updates_only_selected_state_and_delay_preserving_every_other_value(self):
        before=self.snapshot()
        original_bytes={reg:path.read_bytes() for reg,path in self.files.items() if path.exists()}
        original={reg:self.queue(reg) for reg in (A,B,C)}
        other_campaign=self.queue(A,"other-campaign")
        report=scoped.recheck(self.path,[A,B],apply=True)
        self.assertEqual(report["queued"],2)
        for reg in (A,B):
            self.assertEqual(self.queue(reg),{**original[reg],"state":"pending","next_retry_at":0})
        self.assertEqual(self.queue(C),original[C])
        self.assertEqual(self.queue(A,"other-campaign"),other_campaign)
        self.assertEqual({reg:path.read_bytes() for reg,path in self.files.items() if path.exists()},original_bytes)
        after=self.snapshot()
        for table in before:
            if table!="v332_audit_queue":self.assertEqual(after[table],before[table],table)
        repeated_before=self.path.read_bytes()
        repeated=scoped.recheck(self.path,[A,B],apply=True)
        self.assertEqual((repeated["queued"],repeated["skipped"]),(0,2))
        self.assertEqual(self.path.read_bytes(),repeated_before)

    def test_pending_running_missing_and_unsupported_rows_are_skipped(self):
        before=self.snapshot()
        report=scoped.recheck(self.path,[RUNNING,PENDING,NO_FILE,NO_PURCHASE,NO_QUEUE,UNKNOWN],apply=True)
        reasons={item["reg"]:item["reason"] for item in report["items"]}
        self.assertEqual(reasons,{RUNNING:"already_running",PENDING:"already_pending",NO_FILE:"no_local_original",
                                 NO_PURCHASE:"missing_purchase",NO_QUEUE:"missing_queue_row",UNKNOWN:"unsupported_queue_state"})
        self.assertEqual((report["queued"],report["skipped"]),(0,6))
        self.assertEqual(self.snapshot(),before)

    def test_all_three_terminal_states_are_supported(self):
        report=scoped.recheck(self.path,[A,B,C],apply=True)
        self.assertEqual(report["queued"],3)
        self.assertEqual({self.queue(reg)["state"] for reg in (A,B,C)},{"pending"})

    def test_malformed_or_oversize_scope_fails_before_database_open(self):
        bad=[[],None,"all",["*"],["all"],["32616233661 OR 1=1"],["3261623366"],["42616233661"],
             [A,A],[A,True],[A," "+B],[str(32616000000+i) for i in range(21)]]
        before=self.path.read_bytes()
        for regs in bad:
            with self.subTest(regs=regs),patch.object(scoped,"_connect") as connect,self.assertRaises(ValueError):
                scoped.recheck(self.path,regs,apply=True)
            connect.assert_not_called()
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(len(scoped.validate_regs([str(32616000000+i) for i in range(20)])),20)

    def test_missing_database_directory_or_schema_never_creates_or_migrates(self):
        nonexistent=self.root/"does-not-exist.sqlite"
        with self.assertRaises(ValueError):scoped.recheck(nonexistent,[A],apply=True)
        self.assertFalse(nonexistent.exists())
        with self.assertRaises(ValueError):scoped.recheck(self.root,[A])
        empty=self.root/"empty.sqlite"
        with sqlite3.connect(empty) as conn:conn.execute("CREATE TABLE unrelated(value TEXT)")
        before=empty.read_bytes()
        with self.assertRaises(ValueError):scoped.recheck(empty,[A],apply=True)
        self.assertEqual(empty.read_bytes(),before)

    def test_concurrent_worker_reservation_is_not_replaced(self):
        original_read=scoped._read_plan
        def read_then_reserve(*args):
            plan=original_read(*args)
            def reserve():
                with sqlite3.connect(self.path) as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    conn.execute("UPDATE v332_audit_queue SET state='running',updated_at='worker-claim',last_result_token='worker-token' "
                                 "WHERE campaign=? AND reg=?",(scoped.CAMPAIGN,A))
            with ThreadPoolExecutor(max_workers=1) as workers:workers.submit(reserve).result(timeout=5)
            return plan
        with patch.object(scoped,"_read_plan",side_effect=read_then_reserve):
            report=scoped.recheck(self.path,[A],apply=True)
        self.assertEqual((report["queued"],report["items"][0]["reason"]),(0,"already_running"))
        row=self.queue(A)
        self.assertEqual((row["state"],row["updated_at"],row["last_result_token"]),("running","worker-claim","worker-token"))
        self.assertEqual(row["next_retry_at"],9999999999)

    def test_concurrent_terminal_result_or_original_row_change_is_skipped(self):
        original_read=scoped._read_plan
        for sql,reason in (("UPDATE v332_audit_queue SET updated_at='new-result' WHERE reg=?","changed_since_preflight"),
                           ("UPDATE docs SET status='deleted' WHERE reg_number=?","original_changed_since_preflight")):
            def read_then_change(*args):
                plan=original_read(*args)
                with sqlite3.connect(self.path) as conn:conn.execute(sql,(A,))
                return plan
            with self.subTest(reason=reason),patch.object(scoped,"_read_plan",side_effect=read_then_change):
                report=scoped.recheck(self.path,[A],apply=True)
            self.assertEqual(report["queued"],0)
            self.assertEqual(report["items"][0]["reason"],reason)

    def test_original_removed_after_preflight_is_skipped(self):
        original_read=scoped._read_plan
        def read_then_remove(*args):
            plan=original_read(*args)
            self.files[A].unlink()
            return plan
        with patch.object(scoped,"_read_plan",side_effect=read_then_remove):
            report=scoped.recheck(self.path,[A],apply=True)
        self.assertEqual((report["queued"],report["items"][0]["reason"]),(0,"no_local_original"))
        self.assertEqual(self.queue(A)["state"],"completed")

    def test_database_trigger_cannot_modify_history_as_update_side_effect(self):
        with sqlite3.connect(self.path) as conn:
            conn.execute("""CREATE TRIGGER unexpected_history_write AFTER UPDATE OF state ON v332_audit_queue
                BEGIN INSERT INTO findings(reg_number,body) VALUES(NEW.reg,'unexpected'); END""")
        before=self.snapshot()
        with self.assertRaises(sqlite3.DatabaseError):scoped.recheck(self.path,[A,B],apply=True)
        self.assertEqual(self.snapshot(),before)

    def test_unusable_empty_outside_root_or_relative_files_do_not_qualify(self):
        for path in (str(self.root/"missing.pdf"),str(self.root),"relative.pdf"):
            with sqlite3.connect(self.path) as conn:conn.execute("UPDATE docs SET local_path=? WHERE reg_number=?",(path,A))
            self.assertEqual(scoped.recheck(self.path,[A])["items"][0]["reason"],"no_local_original")
        self.files[A].write_bytes(b"")
        with sqlite3.connect(self.path) as conn:conn.execute("UPDATE docs SET local_path=? WHERE reg_number=?",(str(self.files[A]),A))
        self.assertEqual(scoped.recheck(self.path,[A])["items"][0]["reason"],"no_local_original")

    def test_downloaded_original_without_text_qualifies_but_unverified_row_does_not(self):
        with sqlite3.connect(self.path) as conn:
            conn.execute("UPDATE docs SET status='downloaded_no_text' WHERE reg_number=?",(A,))
            conn.execute("UPDATE docs SET status='unverified' WHERE reg_number=?",(B,))
        report=scoped.recheck(self.path,[A,B])
        self.assertTrue(report["items"][0]["eligible"])
        self.assertEqual(report["items"][1]["reason"],"no_local_original")

    def test_cli_has_no_enumeration_and_defaults_to_preflight(self):
        before=self.path.read_bytes()
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(scoped.main(["--database",str(self.path),"--reg",A,"--reg",B]),0)
        self.assertEqual(json.loads(output.getvalue())["mode"],"dry-run")
        self.assertEqual(self.path.read_bytes(),before)
        for args in (["--database",str(self.path),"--all","--apply"],
                     ["--database",str(self.path),"--reg","*","--apply"],
                     ["--database",str(self.path),"--apply"]):
            with self.subTest(args=args),contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as exc:
                scoped.main(args)
            self.assertEqual(exc.exception.code,2)
        self.assertEqual(self.path.read_bytes(),before)

    def test_campaign_matches_existing_worker_without_revision_changes(self):
        import v332_all_procurements_audit as worker
        self.assertEqual(scoped.CAMPAIGN,worker.CAMPAIGN)
        revision=worker.RULE_REVISION
        scoped.recheck(self.path,[A],apply=True)
        self.assertEqual(worker.RULE_REVISION,revision)


if __name__=="__main__":unittest.main()
