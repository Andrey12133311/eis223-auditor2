import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from v343_fast_cards import recovered_submission_start

def test_explicit_card_date_recovery():
    row={"submission_start":"","card_fields_json":json.dumps(
        {"submissionStart":"14.10.2026 (МСК+6)"},ensure_ascii=False)}
    assert recovered_submission_start(row)=="14.10.2026 (МСК+6)"

def test_no_inferred_start_date():
    row={"published_at":"14.10.2026","submission_end":"22.10.2026 09:00",
         "card_fields_json":json.dumps({"publicationDate":"14.10.2026"})}
    assert recovered_submission_start(row)==""

def test_saved_field_has_priority():
    assert recovered_submission_start({"submission_start":"2026-10-14",
        "card_fields_json":json.dumps({"submissionStart":"2026-10-15"})})=="2026-10-14"
