"""All supplied originals reach existing rules without relaxing evidence gates."""
from copy import deepcopy
import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from v372_auto_regulation_protocols import evaluate, MAX_TEXT, _text
from v352_dashboard_truth import collapse_legal_events


P = {"reg": "32616412317", "method": "Открытый конкурс", "published": "2026-10-01"}
URL = "https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid=" + "A" * 32


def original(name, text):
    return {"name": name, "status": "checked", "text": text, "url": URL,
            "meta": {"source": "official_eis_original", "source_url": URL,
                     "sha256": hashlib.sha256(text.encode()).hexdigest()}}


class DocumentCoverage(unittest.TestCase):
    def setUp(self):
        self.protocol = original("Протокол рассмотрения заявок.pdf",
            "Протокол рассмотрения заявок. Комиссия рассмотрела документы участников. "
            "По заявке № 12 комиссия приняла решение: отклонена заявка № 12, "
            "основания отклонения не указаны. Итоги рассмотрения зафиксированы.")
        self.others = [original(f"Приложение {i}.pdf", f"Технические характеристики {i}")
                       for i in range(75)]

    def test_protocol_before_or_after_75_irrelevant_originals(self):
        expected = evaluate(P, [self.protocol])
        self.assertEqual(len(expected[0]), 1)
        for docs in ([self.protocol] + self.others, self.others + [self.protocol]):
            before = deepcopy(docs)
            self.assertEqual(evaluate(P, docs), expected)
            self.assertEqual(docs, before)

    def test_late_unverified_protocol_stays_excluded(self):
        for change in ({"source": "unknown"}, {"sha256": "invalid"}, {"source_url": ""}):
            doc = deepcopy(self.protocol)
            doc["meta"].update(change)
            self.assertEqual(evaluate(P, self.others + [doc]), ([], []))
        self.assertEqual(evaluate({**P, "method": "Закупка у единственного поставщика"},
                                  self.others + [self.protocol]), ([], []))

    def test_existing_episode_deduplication_still_collapses_copies(self):
        found, checks = evaluate(P, [self.protocol] + self.others + [deepcopy(self.protocol)])
        self.assertEqual(len(collapse_legal_events(found)), 1)
        self.assertEqual(len(checks), len(evaluate(P, [self.protocol])[1]))

    def test_per_document_text_bound_is_unchanged(self):
        self.assertEqual(len(_text({"text": "x" * (MAX_TEXT + 1)})), MAX_TEXT)


if __name__ == "__main__":
    unittest.main()
