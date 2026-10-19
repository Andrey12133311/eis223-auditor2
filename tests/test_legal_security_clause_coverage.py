"""Real legal parsers on synthetic verified originals; no external calls.

The legal thresholds are unchanged. These cases reproduce lost clauses and
also prevent recovering a value from a different legal subject, lot or quote.
"""
import hashlib
import sys
import unittest
from copy import deepcopy
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from v324_official_price import prices_in_text
from v336_legal_matrix import security_findings,verified_price,install

REG="32616233661"
EIS="https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid="
P={"reg":REG,"method":"Аукцион в электронной форме",
   "price_num":4000000,"published":"2026-10-01"}
PRICE="Начальная (максимальная) цена договора: 4 000 000 рублей."
BID="Обеспечение заявки устанавливается в размере 100 000 рублей."


def original(text,name="Документация.docx",*,trusted=True,uid=1,kind="other"):
    return {"name":name,"text":text,"status":"checked","type":kind,
            "meta":{"source":"official_eis_original" if trusted else "local_unverified",
                    "sha256":hashlib.sha256(text.encode()).hexdigest(),
                    "source_url":EIS+f"{uid:032X}"}}


def inspect(body,*,purchase=None,price=PRICE):
    return security_findings(purchase or P,
                            [original(price,"Извещение.docx"),original(body,uid=2)])


class SecurityClauseCoverageTests(unittest.TestCase):
    def test_contract_negation_does_not_hide_bid_requirement(self):
        for separator in (" ","\n","; ",", "):
            for parts in ((BID,"Обеспечение исполнения договора не требуется."),
                          ("Обеспечение исполнения договора не требуется.",BID)):
                with self.subTest(separator=separator,parts=parts):
                    findings,_=inspect(separator.join(parts))
                    self.assertEqual(len(findings),1)
                    self.assertEqual(findings[0]["code"],"223-3.2-27-BID-SECURITY-LOW-NMCD")
                    self.assertNotIn("исполнения договора",findings[0]["match_text"])

    def test_same_sentence_and_table_subject_boundaries(self):
        for body in (
            "Обеспечение исполнения договора не требуется, обеспечение заявки составляет 100 000 рублей.",
            "Обеспечение заявки составляет 100 000 рублей, обеспечение исполнения договора не требуется.",
            "| Размер обеспечения заявки | 100 000 рублей |\n| Обеспечение исполнения договора | Не требуется |",
        ):
            with self.subTest(body=body):self.assertEqual(len(inspect(body)[0]),1)

    def test_wrapped_and_written_bid_amounts_are_not_dropped(self):
        for body in (
            "Размер обеспечения заявки\n100 000 рублей.",
            "Размер обеспечения заявки на участие в закупке\n100\u202f000,00 руб.",
            "Обеспечение заявки: 100 000 (сто тысяч) рублей.",
            "Размер обеспечения заявки: 3 (три) % от НМЦД.",
            "Обеспечение заявки предусмотрено в размере 1 процента от НМЦД.",
        ):
            with self.subTest(body=body):self.assertEqual(len(inspect(body)[0]),1)

    def test_conflicting_or_unsupported_written_values_are_not_guessed(self):
        for body in (
            "Обеспечение заявки: 3 (пять) % от НМЦД.",
            "Обеспечение заявки: 100 000 (двести тысяч) рублей.",
            "Обеспечение заявки: 6 (один два три) % от НМЦД.",
            "Обеспечение заявки: 3,5 (три целых пять десятых) % от НМЦД.",
        ):
            with self.subTest(body=body):self.assertEqual(inspect(body)[0],[])

    def test_amount_is_never_borrowed_from_contract_security(self):
        for body in (
            "Обеспечение заявки\nОбеспечение исполнения договора 100 000 рублей.",
            "Обеспечение заявки не требуется. Обеспечение исполнения договора 100 000 рублей.",
            "Размер обеспечения заявки | Не установлено |\nРазмер обеспечения исполнения договора | 100 000 рублей |",
            "Обеспечение заявки: см. пункт 4. Обеспечение исполнения договора: 100 000 рублей.",
            "Обеспечение заявки и обеспечение исполнения договора: 100 000 рублей.",
        ):
            with self.subTest(body=body):self.assertEqual(inspect(body)[0],[])

    def test_negative_and_hypothetical_bid_clauses_remain_non_findings(self):
        for body in (
            "Обеспечение заявки в размере 100 000 рублей не требуется.",
            "Не требуется предоставлять обеспечение заявки в размере 100 000 рублей.",
            "Запрещается требовать обеспечение заявки в размере 100 000 рублей.",
            "Если предусмотрено обеспечение заявки в размере 100 000 рублей, участник перечисляет средства.",
            "Например, обеспечение заявки составляет 100 000 рублей.",
            "Обеспечение заявки: -100 000 рублей.",
            "Обеспечение заявки: 100 000 рублей предоставлять не требуется.",
            "Обеспечение заявки в размере 100 000 руб. не требуется.",
            "Обеспечение заявки: 3 % от НМЦД, если НМЦД превышает 5 000 000 рублей.",
            "Обеспечение заявки: 3 % при проведении конкурентной процедуры.",
        ):
            with self.subTest(body=body):self.assertEqual(inspect(body)[0],[])

    def test_shared_price_parser_recovers_normal_monetary_fields(self):
        for price in (
            "Начальная (максимальная) цена договора составляет 4 000 000 рублей.",
            "Начальная (максимальная) цена договора (НМЦД): 4 000 000 рублей.",
            "Начальная (максимальная) цена договора: 4 000 000,00 (четыре миллиона) рублей.",
            "НМЦД\n4 000 000 рублей",
            "| НМЦД | 4\u202f000\u202f000,00 руб. |",
            "НМЦД: 4 млн. рублей",
        ):
            with self.subTest(price=price):
                self.assertIsNotNone(verified_price(P,[original(price)]))
                self.assertEqual(len(inspect(BID,price=price)[0]),1)

    def test_nmcd_digits_and_written_amount_must_agree(self):
        for prefix,digits in (("","4 000 000"),("","4 000 000,00"),
                             ("составляет "*28,"4 000 000")):
            with self.subTest(digits=digits,prefix=prefix):
                # The long accepted prefix truncates the display excerpt;
                # consistency still uses the full original monetary clause.
                consistent="НМЦД: "+prefix+digits+" (четыре миллиона) рублей."
                conflicting="НМЦД: "+prefix+digits+" (восемь миллионов) рублей."
                self.assertEqual(len(inspect(BID,price=consistent)[0]),1)
                findings,checks=inspect(BID,price=conflicting)
                self.assertEqual(findings,[])
                self.assertTrue(checks)
                # A matching card or a second copy of the numeric amount
                # cannot silently settle this conflict in the original.
                card={**P,"verified_price_source":{"kind":"nmcd","price":"4000000.00",
                    "source":f"https://zakupki.gov.ru/223/purchase/public/purchase/info/common-info.html?regNumber={REG}"}}
                self.assertIsNone(verified_price(card,[original(conflicting)]))
                self.assertIsNone(verified_price(P,[original(PRICE),original(conflicting,uid=2)]))

    def test_complete_currency_tokens(self):
        for token in ("руб.","руб", "рубль", "рубля", "рублей", "RUB", "Rub", "₽"):
            with self.subTest(token=token):
                values=prices_in_text("НМЦД: 4 000 000 "+token)
                self.assertTrue(values)
                self.assertEqual(values[0][1],4000000)
        for token in ("рубашек","рублика","RUBRIC","млн. рубашек"):
            with self.subTest(token=token):
                self.assertEqual(prices_in_text("НМЦД: 4 000 000 "+token),[])

    def test_price_thresholds_and_other_numbers_do_not_prove_nmcd(self):
        for price in (
            "Начальная (максимальная) цена договора не превышает 4 000 000 рублей.",
            "В случае, если НМЦД не превышает 4 000 000 рублей, обеспечение не устанавливается.",
            "НМЦД: 4 процентов",
            "НМЦД: 4 рабочих дня",
            "НМЦД: -4000000 руб.",
            "НМЦД\nСрок поставки: 4 дня. Цена договора: 4 000 000 руб.",
            "Цена победителя: 4 000 000 руб.",
            "Начальная цена единицы товара: 4 000 000 руб.",
            "Обеспечение исполнения договора: 5% от НМЦД — 4 000 000 руб.",
            "Максимальное значение цены договора: 4 000 000 руб.",
        ):
            with self.subTest(price=price):
                self.assertIsNone(verified_price(P,[original(price)]))
                self.assertEqual(inspect(BID,price=price)[0],[])

    def test_conflicting_prices_are_not_resolved_by_matching_db_value(self):
        for docs in (
            [original(PRICE),original("НМЦД: 8 000 000 рублей.",uid=2)],
            [original(PRICE+"\nНМЦД: 8 000 000 рублей.")],
            [original("Лот № 1. "+PRICE+"\nЛот № 2. "+PRICE)],
        ):
            with self.subTest(docs=docs):
                self.assertIsNone(verified_price(P,docs))
                self.assertEqual(security_findings(P,docs+[original(BID,uid=3)])[0],[])

    def test_ambiguous_original_is_not_overridden_by_card(self):
        card={**P,"verified_price_source":{"kind":"nmcd","price":"4000000.00",
                    "source":f"https://zakupki.gov.ru/223/purchase/public/purchase/info/common-info.html?regNumber={REG}"}}
        self.assertEqual(len(security_findings(card,[original(BID)])[0]),1)
        self.assertIsNone(verified_price(card,[original("НМЦД: 8 000 000 рублей.")]))
        for change in ({"kind":"subject_cost"},{"source":"https://example.org/price"},
                       {"source":card["verified_price_source"]["source"].replace(REG,"32616000001")}):
            bad={**card,"verified_price_source":{**card["verified_price_source"],**change}}
            with self.subTest(change=change):
                self.assertEqual(security_findings(bad,[original(BID)])[0],[])

    def test_unverified_or_unrelated_documents_do_not_supply_price(self):
        for doc in (
            original(PRICE,trusted=False),
            original(PRICE,"Итоговый протокол.pdf"),
            original(PRICE,kind="contract"),
            original(PRICE,"Положение о закупке.docx"),
            original(PRICE,"Образец извещения.docx"),
        ):
            with self.subTest(doc=doc):self.assertIsNone(verified_price(P,[doc]))
        self.assertEqual(security_findings(P,[original(PRICE),original(BID,trusted=False)])[0],[])
        for name in ("Положение о закупке.docx","Образец извещения.docx"):
            with self.subTest(name=name):
                self.assertEqual(security_findings(P,[original(PRICE),original(BID,name,uid=2)])[0],[])

    def test_existing_general_and_sme_thresholds_stay_unchanged(self):
        p={**P,"price_num":8000000}
        price="НМЦД: 8 000 000 рублей."
        for amount,expected in (("5",0),("5,01",1),("6 (шесть)",1)):
            with self.subTest(amount=amount):
                self.assertEqual(len(inspect("Обеспечение заявки: "+amount+" % от НМЦД.",purchase=p,price=price)[0]),expected)
        self.assertEqual(inspect("Обеспечение заявки: 3 % от НМЦД.",purchase=p,price=price)[0],[])
        sme={**p,"fact_evidence":{"sme_only":{"value":True,"source":"official_eis_card",
             "evidence":"Участниками закупки могут быть только субъекты малого и среднего предпринимательства: Да"}}}
        findings,_=inspect("Обеспечение заявки: 3 (три) % от НМЦД.",purchase=sme,price=price)
        self.assertEqual(findings[0]["code"],"223-SME-BID-SECURITY-OVER-TWO-PERCENT")
        self.assertEqual(inspect(BID,purchase={**P,"method":"Единственный поставщик"})[0],[])
        self.assertEqual(inspect(BID,purchase={**P,"published":"2018-01-01"})[0],[])

    def test_repeated_originals_do_not_multiply_findings_or_mutate_evidence(self):
        docs=[original(PRICE),original(BID,uid=2),original(BID,"Копия.docx",uid=3)]
        before=deepcopy(docs)
        ns={"analyze":lambda p,d:[],"build_checks":lambda p,detail,card,d:[]}
        install(ns)
        first=ns["analyze"](P,docs)
        second=ns["analyze"](P,docs)
        self.assertEqual(len(first),1)
        self.assertEqual(first,second)
        self.assertEqual(docs,before)
        self.assertTrue(first[0]["source_url"].startswith(EIS))


if __name__=="__main__":unittest.main()
