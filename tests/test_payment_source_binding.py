"""A repeated payment quote needs a valid occurrence, including normalized text."""
import unittest

from test_payment_terms import LONG, P, doc
from vp_inspection_engine import qualify_outcomes


class PaymentSourceBindingTests(unittest.TestCase):
    def qualify(self, text, **extra):
        source = doc(text)
        item = {
            "code": "1352-28-SME-PAYMENT-OVER-SEVEN-WORKDAYS",
            "kind": "violation", "title": "Срок оплаты свыше семи рабочих дней",
            "law": "ПП РФ № 1352, п. 28", "evidence": LONG,
            "document": source["name"], "source_url": source["url"], **extra,
        }
        return qualify_outcomes(P, [source], [item])

    def test_repeated_normalized_quote_needs_an_occurrence(self):
        for second in (LONG, LONG.replace(" производится ", "\nпроизводится  "),
                       LONG.replace(" ", "\u00a0")):
            text = LONG + "\n" + second
            for position in ({}, {"source_position": "0"}, {"source_position": True},
                             {"source_position": -1}, {"source_position": 999999},
                             {"source_position": 5}):
                with self.subTest(second=second, position=position):
                    findings, checks = self.qualify(text, **position)
                    self.assertEqual(findings, [])
                    self.assertEqual(checks[0]["kind"], "review")
                    self.assertIn("payment_clause_unverified",
                                  checks[0]["methodology_reason_codes"])
                    self.assertEqual(checks[0]["evidence"], LONG)

    def test_valid_position_preserves_a_concrete_occurrence(self):
        text = LONG + "\n" + LONG.replace(" производится ", "\nпроизводится  ")
        for position in (0, len(LONG) + 1):
            with self.subTest(position=position):
                findings, checks = self.qualify(text, source_position=position)
                self.assertEqual(len(findings), 1)
                self.assertEqual(checks, [])
                self.assertEqual(findings[0]["payment_assessment"]["status"], "confirmed")

    def test_unique_legacy_quote_without_position_remains_assessable(self):
        findings, checks = self.qualify("4. Порядок оплаты\n" + LONG)
        self.assertEqual(len(findings), 1)
        self.assertEqual(checks, [])


if __name__ == "__main__":
    unittest.main()
