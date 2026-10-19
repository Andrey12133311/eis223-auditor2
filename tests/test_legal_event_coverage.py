"""Complete numbered events must survive analysis, persistence and card counts.

The dates below exercise existing legal rules. No production procurements or
new legal thresholds are used, and copies of one event must still count once.
"""
import asyncio
import copy
import hashlib
import json
import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import v341_precise_legal as precise
import v348_cross_document_legal as cross
import v333_dedup_ui as dedup
from v332_all_procurements_audit import persist
from v352_dashboard_truth import collapse_legal_events, load_counts
from v353_episode_guard import same_episode

P = {"reg": "32616412317", "method": "Открытый конкурс",
     "published": "2026-10-01", "submission_end": "2026-10-30"}
SOURCE = "https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid="


def original(family, number, uid=1, published="13.10.2026", neutral_name=False):
    if family == "protocol":
        name = f"Итоговый протокол № {number}.pdf"
        body = (f"Номер протокола: {number}\n"
                "Дата подписания итогового протокола: 06.10.2026\n"
                f"Дата размещения итогового протокола в ЕИС: {published}")
    else:
        name = f"Разъяснение на запрос № {number}.pdf"
        body = (f"Номер запроса: {number}\n"
                "Дата поступления запроса: 06.10.2026\n"
                f"Дата публикации ответа в ЕИС: {published}")
    url = SOURCE + f"{uid:032X}"
    return {"name": "Оригинал ЕИС.pdf" if neutral_name else name,
            "text": body, "status": "checked", "url": url,
            "meta": {"source": "official_eis_original", "source_url": url,
                     "sha256": hashlib.sha256(body.encode()).hexdigest()}}


def identifier(family, item):
    reader = (cross.finding_protocol_number if family == "protocol" else
              cross.finding_clarification_number)
    return reader(item)


def installed(docs, initial=None, checks=None):
    ns = {"analyze": lambda p, d: list(initial or []),
          "build_checks": lambda p, detail, card, d: list(checks or [])}
    precise.install(ns)
    cross.install(ns)
    with patch.object(precise, "enrich", side_effect=lambda ns, p, *a, **kw: p), \
         patch.object(precise, "persist_facts", return_value=None):
        p = dict(P)
        found = dedup.collapse(ns["analyze"](p, docs))
        checked = ns["build_checks"](p, {}, {}, docs)
    return found, checked


class LegalEventCoverage(unittest.TestCase):
    def test_two_numbered_events_with_identical_dates_survive_all_analyzers(self):
        for family in ("protocol", "clarification"):
            with self.subTest(family=family):
                docs = [original(family, "1"), original(family, "2", 2)]
                self.assertEqual([len(precise.evaluate(P, [doc])[0]) for doc in docs], [1, 1])
                found, _ = installed(docs)
                self.assertEqual(len(found), 2)
                self.assertEqual({identifier(family, item) for item in found}, {"1", "2"})
                self.assertEqual(len(collapse_legal_events(found)), 2)

    def test_number_in_body_survives_neutral_filename_and_saved_evidence(self):
        for family in ("protocol", "clarification"):
            with self.subTest(family=family):
                docs = [original(family, "1", neutral_name=True),
                        original(family, "2", 2, neutral_name=True)]
                found, _ = installed(docs)
                self.assertEqual(len(found), 2)
                saved = [{k: item[k] for k in ("code", "title", "law", "evidence",
                                                "document", "source_url")} for item in found]
                self.assertEqual(len(dedup.collapse(saved)), 2)
                self.assertEqual(len(collapse_legal_events(saved)), 2)
                self.assertEqual({identifier(family, item) for item in saved}, {"1", "2"})

    def test_copy_with_another_filename_or_url_is_not_an_extra_event(self):
        for family in ("protocol", "clarification"):
            with self.subTest(family=family):
                one = original(family, "А–1 / 2026")
                duplicate = original(family, "а-1/2026", 2, neutral_name=True)
                found, _ = installed([one, duplicate])
                self.assertEqual(len(found), 1)
                self.assertEqual(identifier(family, found[0]), "а-1/2026")

    def test_same_number_different_dated_events_are_retained(self):
        for family in ("protocol", "clarification"):
            with self.subTest(family=family):
                docs = [original(family, "1"), original(family, "1", 2, "14.10.2026")]
                found, _ = installed(docs)
                self.assertEqual(len(found), 2)
                self.assertEqual(len(collapse_legal_events(found)), 2)

    def test_date_spelling_does_not_create_an_extra_copy(self):
        for family in ("protocol", "clarification"):
            with self.subTest(family=family):
                one = original(family, "1")
                duplicate = copy.deepcopy(one)
                duplicate["text"] = duplicate["text"].replace("06.10.2026", "2026-10-06")
                duplicate["text"] = duplicate["text"].replace("13.10.2026", "2026-10-13")
                duplicate["url"] = SOURCE + f"{2:032X}"
                duplicate["meta"]["source_url"] = duplicate["url"]
                duplicate["meta"]["sha256"] = hashlib.sha256(duplicate["text"].encode()).hexdigest()
                found, _ = installed([one, duplicate])
                self.assertEqual(len(found), 1)

    def test_conflicting_or_incomplete_numbers_do_not_create_new_identity(self):
        for family in ("protocol", "clarification"):
            label = "протокола" if family == "protocol" else "запроса"
            for number in ("1(А)", "1 А", "___"):
                with self.subTest(family=family, number=number):
                    doc = original(family, number)
                    self.assertEqual(cross.document_event_number(doc,family.upper()), "")
            doc = original(family, "1")
            doc["text"] += f"\nНомер {label}: 2"
            found, _ = installed([doc])
            self.assertEqual(identifier(family, found[0]), "")

    def test_unrelated_numbers_are_not_event_identifiers(self):
        for family in ("protocol", "clarification"):
            doc = original(family, "1", neutral_name=True)
            doc["text"] = "Закупка № 32616412317. Заявка № 12.\n" + doc["text"].split("\n", 1)[1]
            found, _ = installed([doc])
            self.assertEqual(identifier(family, found[0]), "")

    def test_existing_numbered_event_does_not_suppress_a_different_number(self):
        for family in ("protocol", "clarification"):
            first = original(family, "1")
            existing = precise.evaluate(P, [first])[0]
            found, _ = installed([first, original(family, "2", 2)], initial=existing)
            self.assertEqual(len(found), 2)

    def test_check_statuses_and_different_numbers_are_preserved(self):
        docs = [original("protocol", "1"), original("protocol", "2", 2)]
        rows = precise.evaluate(P, docs)[0]
        checks = [{**item, "item": item["title"], "status": "violation"} for item in rows]
        checks += [{**checks[0], "status": "check"}, dict(checks[1])]
        _, result = installed([], checks=checks)
        self.assertEqual(len(result), 3)
        self.assertEqual([item["status"] for item in result].count("violation"), 2)

    def test_unverified_and_timely_events_still_do_not_produce_findings(self):
        for family in ("protocol", "clarification"):
            doc = original(family, "1")
            doc["meta"]["source"] = "existing_local_unverified"
            self.assertEqual(precise.evaluate(P, [doc])[0], [])
            timely = original(family, "2", 2, "09.10.2026")
            self.assertEqual(precise.evaluate(P, [timely])[0], [])

    def test_submission_deadline_context_does_not_create_a_new_request(self):
        for number in ("1", "01.10.2026", ""):
            with self.subTest(number=number):
                doc = original("clarification", number)
                first = precise.evaluate(P, [doc])[0][0]
                changed = precise.evaluate({**P, "submission_end": "2026-10-31"}, [doc])[0][0]
                saved = copy.deepcopy([first, changed])
                self.assertIn("окончание подачи 2026-10-30", first["evidence"])
                self.assertIn("окончание подачи 2026-10-31", changed["evidence"])
                self.assertTrue(same_episode(first, changed))
                self.assertEqual(len(dedup.collapse([first, changed])), 1)
                self.assertEqual(len(collapse_legal_events([first, changed])), 1)
                self.assertEqual(precise._event_dates(first), precise._event_dates(changed))
                self.assertEqual([first, changed], saved)

    def test_context_projection_retains_dates_status_and_original_quote(self):
        first = precise.evaluate(P, [original("clarification", "1")])[0][0]
        changed_docs = [original("clarification", "1", 2, "14.10.2026"),
                        original("clarification", "1", 2)]
        changed_docs[1]["text"] = changed_docs[1]["text"].replace("06.10.2026", "07.10.2026")
        changed_docs[1]["meta"]["sha256"] = hashlib.sha256(changed_docs[1]["text"].encode()).hexdigest()
        for doc in changed_docs:
            with self.subTest(text=doc["text"]):
                other = precise.evaluate({**P, "submission_end": "2026-10-31"}, [doc])[0][0]
                self.assertFalse(same_episode(first, other))
                self.assertEqual(len(collapse_legal_events([first, other])), 2)
        review = {**first, "status": "check", "item": first["title"], "field_path": first["code"]}
        self.assertFalse(same_episode(first, review))
        self.assertEqual(len(dedup.collapse([first, review])), 2)
        self.assertEqual(len(collapse_legal_events([first, review])), 2)
        quoted = [{**first, "evidence": first["evidence"] +
                   f" Условие оригинала: окончание подачи {deadline}."}
                  for deadline in ("2026-11-01", "2026-11-02")]
        self.assertFalse(same_episode(*quoted))
        self.assertEqual(len(collapse_legal_events(quoted)), 2)

    def test_both_clarification_analyzers_keep_persisted_date_aliases(self):
        request, answer = original("clarification", "1"), original("clarification", "1", 2)
        request["text"] = request["text"].rsplit("\n", 1)[0]
        answer["text"] = "Номер запроса: 1\n" + answer["text"].rsplit("\n", 1)[1]
        for doc in (request, answer):
            doc["meta"]["sha256"] = hashlib.sha256(doc["text"].encode()).hexdigest()
        for analyze, docs in ((lambda p, d: precise.evaluate(p, d)[0], [original("clarification", "1")]),
                              (cross.cross_doc_clarifications, [request, answer])):
            first = analyze(P, docs)[0]
            changed = analyze({**P, "submission_end": "2026-10-31"}, docs)[0]
            with self.subTest(code=first["code"]):
                self.assertTrue(same_episode(first, changed))
                self.assertEqual(len(dedup.collapse([first, changed])), 1)
                self.assertEqual(len(collapse_legal_events([first, changed])), 1)
                tail = cross.event_evidence_text(changed["evidence"])
                alias_tail = re.sub(r"(20\d{2})-(\d{2})-(\d{2})",
                                    lambda m: f"{m[3]}.{m[2]}.{m[1]}", tail)
                alias = {**changed, "evidence": changed["evidence"][:-len(tail)] + alias_tail}
                self.assertTrue(same_episode(first, alias))
                self.assertEqual(len(collapse_legal_events([first, alias])), 1)

    def test_general_and_thematic_pipelines_preserve_numbered_events_and_repeat_counts(self):
        import v332_all_procurements_audit as general
        import v342_thematic_queue as thematic
        import v314_eis_metadata as metadata
        import v372_auto_regulation_protocols as protocols

        with tempfile.TemporaryDirectory(prefix="eis-numbered-pipeline-") as root:
            path = Path(root) / "audit.sqlite"
            def connect():
                db = sqlite3.connect(path)
                db.row_factory = sqlite3.Row
                return db
            with connect() as db:
                db.executescript("""
                    CREATE TABLE purchases(reg_number TEXT PRIMARY KEY, customer TEXT, title TEXT,
                        method TEXT, published_at TEXT, submission_end TEXT,
                        violations INTEGER DEFAULT 0, checks_ok INTEGER DEFAULT 0,
                        checks_attention INTEGER DEFAULT 0);
                    CREATE TABLE docs(id INTEGER PRIMARY KEY, reg_number TEXT, name TEXT, url TEXT,
                        doc_type TEXT, published_at TEXT, local_path TEXT, status TEXT,
                        metadata_json TEXT, final_url TEXT);
                    CREATE TABLE findings(id INTEGER PRIMARY KEY, reg_number TEXT, code TEXT, kind TEXT,
                        title TEXT, law TEXT, law_url TEXT, evidence TEXT, note TEXT, document TEXT,
                        source_url TEXT, location TEXT, match_text TEXT);
                    CREATE TABLE checks(id INTEGER PRIMARY KEY, reg_number TEXT, scope TEXT, item TEXT,
                        status TEXT, evidence TEXT, source TEXT, source_url TEXT, document TEXT,
                        field_path TEXT, note TEXT, law TEXT, law_url TEXT, location TEXT);
                """)
                db.execute("""INSERT INTO purchases(reg_number,customer,title,method,published_at,
                              submission_end) VALUES(?,?,?,?,?,?)""",
                           (P["reg"], "ООО Тест", "Поставка материалов", P["method"],
                            P["published"], P["submission_end"]))
            originals = [original(family, number, i + 1, neutral_name=True)
                         for i, (family, number) in enumerate((
                             ("protocol", "1"), ("protocol", "2"),
                             ("clarification", "1"), ("clarification", "2")))]
            source = {"docs": originals, "unreadable": 0, "original_expected": 4,
                      "original_received": 4, "source_incomplete": False,
                      "source_error": None, "p": {}, "card": {}, "detail": {}}
            ns = {"conn": connect, "analyze": lambda p, docs: [],
                  "build_checks": lambda p, detail, card, docs: []}
            precise.install(ns)
            cross.install(ns)
            protocols.install(ns)
            # Source collection has separate receipt/reading regressions. Here
            # the real audit entry points, legal wrappers and SQL stay active.
            with patch.object(general, "collect_saved_evidence", return_value=source), \
                 patch.object(metadata, "from_documents", return_value={}), \
                 patch.object(precise, "enrich", side_effect=lambda ns, p, *a, **kw: p), \
                 patch.object(precise, "persist_facts", return_value=None):
                first = asyncio.run(general.inspect_one(ns, P["reg"]))
                repeated = asyncio.run(general.inspect_one(ns, P["reg"]))
                themed = asyncio.run(thematic.independent_review(ns, P["reg"], ["protocols", "dates"]))
            self.assertEqual(first[0], "completed")
            self.assertEqual(first[1]["verified_original_documents"], 4)
            self.assertEqual(first[1]["new_findings"], 4)
            self.assertEqual((repeated[1]["new_findings"], repeated[1]["new_checks"]), (0, 0))
            self.assertEqual((themed[0], len(themed[1])), ("completed", 4))
            with connect() as db:
                saved = [dict(row) for row in db.execute("SELECT * FROM findings")]
                self.assertEqual(len(saved), 4)
                self.assertEqual(load_counts(db, [P["reg"]]), {P["reg"]: 4})
                self.assertEqual(db.execute("SELECT violations FROM purchases").fetchone()[0], 4)
            self.assertEqual({(item["code"], item["evidence"]) for item in saved},
                             {(item["code"], item["evidence"]) for item in themed[1]})

    def test_distinct_events_persist_once_each_and_reaudit_does_not_inflate_count(self):
        with tempfile.TemporaryDirectory(prefix="eis-local-event-coverage-") as root:
            path = Path(root) / "audit.sqlite"
            def connect():
                db = sqlite3.connect(path)
                db.row_factory = sqlite3.Row
                return db
            with connect() as db:
                db.executescript("""
                    CREATE TABLE purchases(reg_number TEXT PRIMARY KEY, violations INTEGER DEFAULT 0,
                        checks_ok INTEGER DEFAULT 0, checks_attention INTEGER DEFAULT 0);
                    CREATE TABLE findings(id INTEGER PRIMARY KEY, reg_number TEXT, code TEXT, kind TEXT,
                        title TEXT, law TEXT, law_url TEXT, evidence TEXT, note TEXT, document TEXT,
                        source_url TEXT, location TEXT, match_text TEXT);
                    CREATE TABLE checks(id INTEGER PRIMARY KEY, reg_number TEXT, scope TEXT, item TEXT,
                        status TEXT, evidence TEXT, source TEXT, source_url TEXT, document TEXT,
                        field_path TEXT, note TEXT, law TEXT, law_url TEXT, location TEXT);
                """)
                db.execute("INSERT INTO purchases(reg_number) VALUES (?)", (P["reg"],))
            ns = {"conn": connect}
            docs = [original(family, number, i + 1, neutral_name=True)
                    for i, (family, number) in enumerate((
                        ("protocol", "1"), ("protocol", "2"),
                        ("clarification", "1"), ("clarification", "2")))]
            found, _ = installed(docs)
            self.assertEqual(persist(ns, P["reg"], found, []), (4, 0))
            self.assertEqual(persist(ns, P["reg"], found, []), (0, 0))
            with connect() as db:
                self.assertEqual(load_counts(db, [P["reg"]]), {P["reg"]: 4})
                self.assertEqual(db.execute("SELECT violations FROM purchases").fetchone()[0], 4)


class ReceiptDeadlineOccurrence(unittest.IsolatedAsyncioTestCase):
    async def test_semantic_deadline_recheck_persists_one_event_in_both_audits(self):
        # Reuse the real receipt fixture without inheriting/rerunning its suite.
        from tests.test_receipt_original_parity import ReceiptOriginalParity, docx_bytes, LATE_REPLY_B
        import v332_all_procurements_audit as general
        import v342_thematic_queue as thematic
        import v345_thematic_accelerator as fast
        import os

        previous_stdout, previous_health = sys.stdout, thematic.storage_health
        previous_tick, previous_state = thematic.tick, dict(thematic.WORKER_STATE)
        previous_requests = fast._request_missing_originals
        previous_data_dir = os.environ.get("EIS223_DATA_DIR")
        def assert_restored():
            self.assertIs(sys.stdout, previous_stdout)
            self.assertIs(thematic.storage_health, previous_health)
            self.assertIs(thematic.tick, previous_tick)
            self.assertEqual(thematic.WORKER_STATE, previous_state)
            self.assertIs(fast._request_missing_originals, previous_requests)
            self.assertEqual(os.environ.get("EIS223_DATA_DIR"), previous_data_dir)
        self.addCleanup(assert_restored)
        fixture = ReceiptOriginalParity()
        # The borrowed case has no asyncio runner. Register its cleanup with
        # this running case so failures are visible and every patch is closed.
        fixture.addCleanup = self.addCleanup
        fixture.setUp()
        reg = fixture.receipt_purchase()
        general.initialize(fixture.ns)
        self.assertEqual(general.claim_next(fixture.ns), reg)
        first = await fixture.finish_general(reg)
        self.assertEqual(first["new_findings"], 1)
        self.assertEqual(await fast.faster_tick(fixture.ns), 1)
        self.assertEqual(len(fixture.themed(reg)[1]), 1)
        with fixture.connect() as db:
            original_evidence = db.execute("SELECT evidence FROM findings WHERE reg_number=?", (reg,)).fetchone()[0]
        self.assertIn("окончание подачи 2026-10-30", original_evidence)

        manifest = fixture.folder / "eis223_public_browser_intake" / reg / "manifest.json"
        data = json.loads(manifest.read_text())
        data["submission_end"] = "2026-10-31"
        manifest.write_text(json.dumps(data))
        self.assertEqual(general.refresh_changed(fixture.ns), 1)
        self.assertEqual(general.claim_next(fixture.ns), reg)
        repeated = await fixture.finish_general(reg)
        self.assertEqual(repeated["new_findings"], 0)
        self.assertEqual(await fast.faster_tick(fixture.ns), 1)
        item, themed = fixture.themed(reg)
        self.assertEqual((item["status"], len(themed)), ("completed", 1))
        self.assertEqual(themed[0]["evidence"], original_evidence)
        current_state, current = await thematic.independent_review(fixture.ns, reg, ["dates"])
        self.assertEqual((current_state, len(current)), ("completed", 1))
        self.assertIn("окончание подачи 2026-10-31", current[0]["evidence"])
        with fixture.connect() as db:
            saved = db.execute("SELECT evidence FROM findings WHERE reg_number=?", (reg,)).fetchall()
            self.assertEqual([row[0] for row in saved], [original_evidence])
            self.assertEqual(load_counts(db, [reg]), {reg: 1})
            self.assertEqual(db.execute("SELECT violations FROM purchases WHERE reg_number=?", (reg,)).fetchone()[0], 1)
        self.assertEqual(general.progress(fixture.ns)["new_evidence_based_findings"], 1)

        # A genuinely later request/response in another authenticated original
        # remains a second occurrence through the same real persistence path.
        fixture.put_original(reg, docx_bytes(LATE_REPLY_B), "Поздний ответ.docx")
        self.assertEqual(general.refresh_changed(fixture.ns), 1)
        self.assertEqual(general.claim_next(fixture.ns), reg)
        later = await fixture.finish_general(reg)
        self.assertEqual(later["new_findings"], 1)
        self.assertEqual(await fast.faster_tick(fixture.ns), 1)
        self.assertEqual(len(fixture.themed(reg)[1]), 2)
        with fixture.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM findings WHERE reg_number=?", (reg,)).fetchone()[0], 2)
            self.assertEqual(load_counts(db, [reg]), {reg: 2})


if __name__ == "__main__":
    unittest.main()
