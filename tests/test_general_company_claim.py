"""Queue admission and claims must use the same existing company scope."""
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import v332_all_procurements_audit as audit
from v342_thematic_queue import business_company


class GeneralCompanyClaim(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "claims.db"
        self.verified = []
        self.ns = {"conn": self.connect, "_verified_set235": lambda: self.verified}
        c = self.connect()
        c.executescript('''
            CREATE TABLE purchases(reg_number TEXT PRIMARY KEY,customer TEXT,last_seen TEXT);
            CREATE TABLE docs(id INTEGER PRIMARY KEY,reg_number TEXT,status TEXT,local_path TEXT);
            CREATE TABLE v319_manual_inputs(reg TEXT PRIMARY KEY);
            CREATE TABLE v332_audit_queue(campaign TEXT,reg TEXT,state TEXT DEFAULT 'pending',
                updated_at TEXT,last_doc_id INTEGER DEFAULT 0,PRIMARY KEY(campaign,reg));
        ''')
        c.commit()
        c.close()

    def connect(self):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c

    def add(self, index, name, *, verified=True, manual=False, ready=True):
        reg = str(32616412000 + index)
        c = self.connect()
        c.execute("INSERT INTO purchases VALUES(?,?,?)", (reg, name, "2026-10-19"))
        if ready:
            c.execute("INSERT INTO docs VALUES(?,?,?,?)", (index, reg, "checked", "/data/original.pdf"))
        if manual:
            c.execute("INSERT INTO v319_manual_inputs VALUES(?)", (reg,))
        c.commit()
        c.close()
        if verified:
            self.verified.append(reg)
        return reg

    def test_all_admitted_company_forms_can_be_claimed(self):
        names = ["общество с ограниченной ответственностью Тест", "«ООО Тест»",
                 '"АО Тест"', "Закрытое акционерное общество Тест",
                 "Открытое акционерное общество Тест", "публичное акционерное общество Тест",
                 "  ооо Тест", "ООО\tТест", "АО Тест", "ПАО Тест", "ОАО Тест", "ЗАО Тест"]
        regs = [self.add(i, name) for i, name in enumerate(names, 1)]
        self.assertTrue(all(business_company(name) for name in names))
        self.assertCountEqual(audit.dashboard_regs(self.ns), regs)
        self.assertEqual(audit.add_new_verified(self.ns), len(regs))
        self.assertCountEqual([audit.claim_next(self.ns) for _ in regs], regs)
        self.assertIsNone(audit.claim_next(self.ns))

    def test_noncompanies_manual_unverified_and_missing_originals_stay_excluded(self):
        accepted = self.add(1, "ООО Готовый")
        self.add(2, "ГБУ Учреждение")
        self.add(3, "ООО Ручная", manual=True)
        self.add(4, "ООО Неподтвержденная", verified=False)
        waiting = self.add(5, "ООО Ожидание", ready=False)
        self.add(6, "ИП Иванов")
        self.add(7, None)
        audit.add_new_verified(self.ns)
        self.assertEqual(audit.claim_next(self.ns), accepted)
        self.assertIsNone(audit.claim_next(self.ns))
        c = self.connect()
        try:
            self.assertCountEqual([r[0] for r in c.execute("SELECT reg FROM v332_audit_queue")],
                                  [accepted, waiting])
        finally:
            c.close()

    def test_corrected_noncompany_is_not_claimed(self):
        reg = self.add(1, "ООО Тест")
        audit.add_new_verified(self.ns)
        c = self.connect()
        c.execute("UPDATE purchases SET customer='ГБУ Учреждение' WHERE reg_number=?", (reg,))
        c.commit()
        c.close()
        self.assertIsNone(audit.claim_next(self.ns))


if __name__ == "__main__":
    unittest.main()
