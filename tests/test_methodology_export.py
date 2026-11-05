"""The report explains unresolved premises without filling user comments."""
from io import BytesIO
import unittest
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import vp_inspection_export as export
from test_vp_inspection_export import snapshot


class MethodologyExport(unittest.TestCase):
    def test_failed_norm_check_is_explained_with_its_original_evidence(self):
        value = snapshot()
        row = value["rows"][0]
        row.update(has_violation="", review_required=True,
                   required_evidence="Документ о приёмке и применимые исключения",
                   methodology_reasons=["Указанная норма не соответствует проверяемому критерию"],
                   unresolved=[{"status": "unknown", "law": "ГК РФ, ст. 450",
                                "legal_basis_verified": False,
                                "evidence": "Сохранённая спорная формулировка",
                                "document": "Проект договора.pdf"}])
        for extension in ("odt", "docx"):
            with self.subTest(extension=extension):
                with ZipFile(BytesIO(export.render(value, extension))) as archive:
                    root = ET.fromstring(archive.read("content.xml" if extension == "odt" else "word/document.xml"))
                text = " ".join(root.itertext())
                self.assertIn("Для завершения проверки: Документ о приёмке", text)
                self.assertIn("Норма, требующая подтверждения: ГК РФ", text)
                self.assertIn("Сохранённая спорная формулировка", text)
                self.assertIn("не соответствует проверяемому критерию", text)
                self.assertNotIn("MUST NEVER LEAK", text)

    def test_clause_violation_is_not_reported_as_a_rejected_bid(self):
        value = snapshot()
        value["rows"][0]["results"] = [{"status": "violation",
            "decision_kind": "document_requirement", "title": "Недопустимое условие документации",
            "law": "223-ФЗ, ст. 3.4 ч. 19.2", "evidence": "Точная цитата из документа"}]
        text = " ".join(export._appendix(value))
        self.assertIn("Проверено условие документа", text)
        self.assertIn("Фактическое отклонение заявки этим выводом не устанавливается", text)


if __name__ == "__main__":
    unittest.main()
