"""A conflicting verified contract payment clause must not become a confirmed finding."""
import hashlib
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
import v341_precise_legal as legal

URL="https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid="+"1"*32

def original(name,text):
    return {"name":name,"text":text,"status":"checked","url":URL,
            "meta":{"source":"official_eis_original","source_url":URL,
                    "sha256":hashlib.sha256(text.encode()).hexdigest()}}

class ConflictingPaymentContracts(unittest.TestCase):
    def test_conflict_requires_review_not_confirmed_violation(self):
        p={"reg":"32616410694","method":"Открытый конкурс только для субъектов малого и среднего предпринимательства",
           "submission_end":"2026-10-30"}
        docs=[original("Проект договора.docx",
             "Оплата производится в течение 30 рабочих дней с даты подписания акта приемки."),
              original("Заключенный договор.docx",
             "Оплата производится в течение 7 рабочих дней с даты подписания акта приемки.")]
        findings,checks=legal.evaluate(p,docs)
        self.assertFalse(any(x["code"]=="1352-28-SME-PAYMENT-OVER-SEVEN-WORKDAYS" for x in findings))
        self.assertTrue(any(x["code"]=="v341-sme-payment-contract-version-conflict" for x in checks))
