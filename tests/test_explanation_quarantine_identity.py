"""Documented explanation identity is not an unrestricted XML fallback."""
import io
import zipfile
import unittest
import eis223_soap_xml as reader
from v294_eis_bridge import parse_notices

REG = "32616475650"
OTHER = "32616478351"
NS = reader.PURCHASE_NS


def document(kind, identity):
    name = f"{kind}_{REG}_fixture.xml"
    raw = (f'<p:{kind} xmlns:p="{NS}"><body><item><{kind}Data>'
           f'{identity}</{kind}Data></item></body></p:{kind}>').encode()
    return name, raw


def archive(*items):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as zipped:
        for name, raw in items:
            zipped.writestr(name, raw)
    return stream.getvalue()


class ExplanationIdentity(unittest.TestCase):
    def check(self, identity, kind="explanation"):
        name, raw = document(kind, identity)
        return reader.quarantine_document(raw, name, REG)

    def test_official_direct_purchase_reg_num_is_preserved_but_not_analyzed(self):
        item = self.check(f"<purchaseRegNum>{REG}</purchaseRegNum>")
        self.assertEqual(item["reg_number"], REG)
        self.assertEqual(item["xml_family"], "unsupported")
        self.assertEqual(item["documents"], [])
        self.assertNotIn("publication", item)
        name, raw = document("explanation", f"<purchaseRegNum>{REG}</purchaseRegNum>")
        with self.assertRaises(reader.UnsupportedXmlType):
            reader.parse_document(raw, name)

    def test_supported_sibling_no_longer_lost_to_documented_identity_shape(self):
        rows = parse_notices(archive(
            document("purchaseNotice", f"<registrationNumber>{REG}</registrationNumber>"),
            document("explanation", f"<purchaseRegNum>{REG}</purchaseRegNum>")))
        self.assertEqual([r["xml_family"] for r in rows], ["notice", "unsupported"])
        self.assertEqual([r["reg_number"] for r in rows], [REG, REG])

    def test_missing_foreign_duplicate_hidden_and_foreign_namespace_still_rejected(self):
        bad = ("", f"<purchaseRegNum>{OTHER}</purchaseRegNum>",
               f"<purchaseRegNum>{REG}</purchaseRegNum>" * 2,
               f"<extension><purchaseRegNum>{REG}</purchaseRegNum></extension>",
               f'<purchaseRegNum xmlns="https://foreign.invalid/">{REG}</purchaseRegNum>',
               f"<registrationNumber>{REG}</registrationNumber>",
               f"<purchaseRegNum>{REG}</purchaseRegNum><registrationNumber>{OTHER}</registrationNumber>",
               f"<purchaseRegNum>{REG}</purchaseRegNum><purchaseInfo><purchaseNoticeNumber>{OTHER}</purchaseNoticeNumber></purchaseInfo>")
        for identity in bad:
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                self.check(identity)

    def test_field_does_not_authorize_other_unknown_types(self):
        with self.assertRaises(ValueError):
            self.check(f"<purchaseRegNum>{REG}</purchaseRegNum>", "arbitraryPurchaseType")

    def test_matching_optional_identity_is_consistent(self):
        self.check(f"<purchaseRegNum>{REG}</purchaseRegNum><registrationNumber>{REG}</registrationNumber>"
                   f"<purchaseInfo><purchaseNoticeNumber>{REG}</purchaseNoticeNumber></purchaseInfo>")


if __name__ == "__main__":
    unittest.main()
