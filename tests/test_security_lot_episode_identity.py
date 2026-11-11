"""Distinct, source-established lots survive legal grouping and saved counts."""
from copy import deepcopy
import hashlib
import json
from itertools import permutations
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import v336_legal_matrix as rules
import v333_dedup_ui as dedup
import v352_dashboard_truth as truth
import v353_episode_guard as episodes
import security_episode_identity as identity

REG = "32616410694"
P = {"reg": REG, "method": "Аукцион в электронной форме", "published": "2026-10-01"}
EIS = "https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid="


def originals(lot, *, offset=0):
    result = []
    for index, (name, text) in enumerate((
        ("Извещение.docx", "Начальная (максимальная) цена договора: 4 000 000 рублей."),
        ("Документация.docx", "Обеспечение заявки: 100 000 рублей.")), 1):
        result.append({"name": name, "text": text, "status": "checked",
            "meta": {"source": "official_eis_original", "lot_number": str(lot),
                "sha256": hashlib.sha256(text.encode()).hexdigest(),
                "source_url": EIS + f"{offset+index:032X}"}})
    return result


def confirmed(lot, *, offset=0):
    values, _ = rules.security_findings({**P, "lot_number": str(lot)}, originals(lot, offset=offset))
    if len(values) != 1:
        raise AssertionError(values)
    return values[0]


class SecurityLotEpisodeIdentity(unittest.TestCase):
    def test_distinct_source_lots_are_not_one_saved_episode(self):
        first, second = confirmed(1), confirmed(2, offset=2)
        self.assertFalse(episodes.same_episode(first, second))
        self.assertTrue(episodes.distinct_new(second, [first]))

    def test_distinct_source_lots_are_not_one_display_event(self):
        first, second = confirmed(1), confirmed(2, offset=2)
        self.assertFalse(truth._common_event(first, second))
        self.assertEqual(len(truth.collapse_legal_events([first, second])), 2)

    def test_exact_copies_of_one_lot_remain_one_episode(self):
        first = confirmed(1)
        self.assertTrue(episodes.same_episode(first, deepcopy(first)))
        self.assertEqual(len(truth.collapse_legal_events([first, deepcopy(first)])), 1)

    def test_raw_lot_flags_do_not_invent_independent_incidents(self):
        first = confirmed(1)
        first["evidence"] = first["evidence"].split("\nОбласть условия обеспечения заявки:", 1)[0]
        second = {**deepcopy(first), "lot_number": "2", "lot_id": "another",
            "security_scope": {"lots": ["2"]}}
        self.assertTrue(episodes.same_episode(first, second))
        self.assertEqual(len(truth.collapse_legal_events([first, second])), 1)

    def test_copies_of_one_explicit_lot_coalesce_across_eis_uids(self):
        first, copied = confirmed(1), confirmed(1, offset=10)
        self.assertIsNotNone(identity.finding_security_scope(first))
        self.assertTrue(episodes.same_episode(first, copied))
        self.assertTrue(dedup._same_finding(first, copied, "title"))
        self.assertEqual(len(dedup.collapse([first, copied])), 1)
        self.assertEqual(len(truth.collapse_legal_events([first, copied])), 1)

    def test_marker_is_bound_to_fresh_lot_source_hash_and_quote(self):
        first = confirmed(1)
        prefix, raw = first["evidence"].rsplit("\n"+identity.PREFIX, 1)
        proof = json.loads(raw[:-1])
        for field, value in (("lots", ["2"]), ("sha256", "b"*64),
                             ("quote", proof["quote"].replace("100", "200")),
                             ("source_url_sha256", "c"*64), ("span", [3, 9])):
            with self.subTest(field=field):
                altered = {**proof, field: value}
                candidate = {**deepcopy(first), "evidence": prefix+"\n"+identity.PREFIX+
                    json.dumps(altered, ensure_ascii=False, separators=(",", ":"))+"."}
                self.assertIsNone(identity.finding_security_scope(candidate))
                self.assertFalse(identity.distinct_security_lots(first, candidate))
                self.assertEqual(len(truth.collapse_legal_events([first, candidate])), 1)

    def test_missing_ambiguous_scope_cannot_emit_an_identity(self):
        first = confirmed(1)
        assessment = deepcopy(first["security_assessment"])
        for lots, ids in (([], []), (["1", "2"], []), ([], ["a", "b"])):
            with self.subTest(lots=lots, ids=ids):
                value = deepcopy(assessment)
                for scope in (value["source_scope"], value["price_basis"]["scope"]):
                    scope.update(lots=lots, lot_ids=ids)
                self.assertEqual(identity.security_scope_evidence(P, value), "")
        assessment["price_basis"]["scope"]["lots"] = ["2"]
        self.assertEqual(identity.security_scope_evidence(P, assessment), "")

    def test_plain_or_repeated_marker_does_not_create_another_episode(self):
        first = confirmed(1)
        plain = {**deepcopy(first), "evidence": identity.security_evidence_text(first)}
        forged = {**deepcopy(plain), "evidence": plain["evidence"]+"\n"+
            identity.PREFIX+'{"lots":["2"]}.'}
        repeated = {**deepcopy(first), "evidence": first["evidence"]+"\n"+
            first["evidence"].splitlines()[-1]}
        for item in (forged, repeated):
            self.assertIsNone(identity.finding_security_scope(item))
            self.assertEqual(len(truth.collapse_legal_events([plain, item])), 1)

    def test_unknown_lot_cannot_bridge_two_source_bound_lots(self):
        first, second = confirmed(1), confirmed(2, offset=2)
        unknown = deepcopy(first)
        unknown["evidence"] = identity.security_evidence_text(first)+" Дополнительные реквизиты."*150
        unknown.pop("security_assessment", None)
        for ordered in permutations((unknown, first, second)):
            with self.subTest(order=[item["source_url"] for item in ordered]):
                grouped = truth.collapse_legal_events(ordered)
                self.assertEqual(len(grouped), 2)
                self.assertTrue(all(identity.finding_security_scope(item) for item in grouped))
        self.assertTrue(episodes.distinct_new(second, [unknown, first]))
        self.assertFalse(episodes.distinct_new(first, [unknown, first]))

    def test_richer_unknown_copy_cannot_replace_the_source_bound_identity(self):
        first = confirmed(1)
        unknown = deepcopy(first)
        unknown["evidence"] = identity.security_evidence_text(first)+" Дополнительные реквизиты."*150
        unknown.pop("security_assessment", None)
        for ordered in ((unknown, first), (first, unknown)):
            grouped = dedup.collapse(ordered)
            self.assertEqual(len(grouped), 1)
            self.assertIsNotNone(identity.finding_security_scope(grouped[0]))

    def test_long_official_urls_preserve_both_lots_and_same_lot_copies(self):
        docs = originals(1)+originals(2, offset=2)
        for doc in docs:
            doc["meta"]["source_url"] += "&receipt_context="+"r"*2200
        findings, _ = rules.security_findings(P, docs)
        self.assertEqual(len(findings), 2)
        self.assertTrue(all(identity.finding_security_scope(item) for item in findings))
        self.assertEqual(len(truth.collapse_legal_events(findings)), 2)
        repeated, _ = rules.security_findings(P, docs+deepcopy(docs))
        self.assertEqual(len(repeated), 2)
        self.assertEqual(len(truth.collapse_legal_events(repeated)), 2)
        copied, _ = rules.security_findings(P, docs[:2]+deepcopy(docs[:2]))
        self.assertEqual(len(copied), 1)
        self.assertEqual(len(truth.collapse_legal_events(copied)), 1)


class SecurityLotSavedProjection(unittest.TestCase):
    """Real SQLite readers and journals preserve source scope without new trust."""
    def setUp(self):
        from test_payment_finding_reassessment import PaymentAssessmentStorage
        PaymentAssessmentStorage.setUp(self)
        with self.connection() as db:
            db.execute("UPDATE thematic_batches SET categories='[\"bid_security\"]'")

    from test_payment_finding_reassessment import PaymentAssessmentStorage as _Fixture
    connect = _Fixture.connect
    connection = _Fixture.connection
    event = _Fixture.event
    record = _Fixture.record
    retained = _Fixture.retained

    def save_sources(self, findings):
        from test_payment_finding_reassessment import BATCH
        with self.connection() as db:
            for item in findings:
                db.execute("INSERT INTO findings(reg_number,code,kind,title,law,evidence,document,source_url) "
                    "VALUES(?,?,?,?,?,?,?,?)", (REG, *(item[key] for key in
                    ("code", "kind", "title", "law", "evidence", "document", "source_url"))))
                db.execute("INSERT INTO thematic_findings(batch_id,reg,category,title,law,evidence,code,document,source_url) "
                    "VALUES(?,?,?,?,?,?,?,?,?)", (BATCH, REG, "bid_security", *(item[key] for key in
                    ("title", "law", "evidence", "code", "document", "source_url"))))

    def test_saved_distinct_lots_retain_two_current_episodes_and_counters(self):
        from test_payment_finding_reassessment import BATCH
        from v384_thematic_findings import read_thematic_items
        findings = [confirmed(1), confirmed(2, offset=2)]
        self.save_sources(findings)
        before = self.retained()
        self.record(findings)
        self.record(findings, "thematic")
        with self.connection() as db:
            self.assertEqual(truth.load_counts(db, [REG])[REG], 2)
            current = [item for item in read_thematic_items(db, BATCH, REG)
                       if item.get("effective_status") == "confirmed"]
            self.assertEqual(len(current), 2)
        self.assertEqual(self.retained(), before)

    def test_saved_copies_of_one_lot_display_one_current_episode(self):
        from test_payment_finding_reassessment import BATCH
        from v384_thematic_findings import read_thematic_items
        findings = [confirmed(1), confirmed(1, offset=10)]
        self.save_sources(findings)
        self.record(findings)
        self.record(findings, "thematic")
        with self.connection() as db:
            self.assertEqual(truth.load_counts(db, [REG])[REG], 1)
            current = [item for item in read_thematic_items(db, BATCH, REG)
                       if item.get("effective_status") == "confirmed"]
            self.assertEqual(len(current), 1)
            self.assertEqual(current[0]["source_representations"], 2)

    def test_historical_marker_without_fresh_journal_does_not_confirm(self):
        from test_payment_finding_reassessment import BATCH
        from v384_thematic_findings import read_thematic_items
        self.save_sources([confirmed(1), confirmed(2, offset=2)])
        before = self.retained()
        with self.connection() as db:
            self.assertEqual(truth.load_counts(db, [REG]).get(REG, 0), 0)
            self.assertTrue(all(item.get("effective_status") == "requires_review"
                for item in read_thematic_items(db, BATCH, REG)))
        self.assertEqual(self.retained(), before)


if __name__ == "__main__":
    unittest.main()
