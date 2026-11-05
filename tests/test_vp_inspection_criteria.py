import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from vp_inspection_criteria import CRITERIA, inspection_rows

def test_exact_template_rows_and_blank_comments():
    assert len(CRITERIA) == 19
    assert len({x.key for x in CRITERIA}) == 19
    rows = inspection_rows({})
    assert len(rows) == 19
    assert all(not x["has_violation"] and not x["no_violation"] and not x["comment"] for x in rows)
    assert all(x["review_required"] for x in rows)

def test_only_documented_outcomes_are_marked():
    rows = inspection_rows({
        "bid_security": {"status":"violation", "evidence":"exact excerpt",
                         "document":"EIS original", "legal_basis_verified":True,
                         "administrative_basis":"requires independent KoAP verification"},
        "notice_content": {"status":"compliant", "evidence":"exact excerpt",
                           "document":"EIS notice", "legal_basis_verified":True},
        "payment_223": {"status":"violation", "evidence":"unsupported"}
    })
    by_key={x["key"]:x for x in rows}
    assert by_key["bid_security"]["has_violation"] == "✓"
    assert by_key["notice_content"]["no_violation"] == "✓"
    assert not by_key["payment_223"]["has_violation"]
    assert all(x["comment"] == "" for x in rows)

def test_sme_unknown_is_not_no_violation():
    item={"sme_payment":{"status":"violation","evidence":"exact excerpt",
                         "document":"EIS contract","legal_basis_verified":True}}
    assert not next(x for x in inspection_rows(item) if x["key"]=="sme_payment")["has_violation"]
    assert next(x for x in inspection_rows(item,sme_status=True) if x["key"]=="sme_payment")["has_violation"]=="✓"
