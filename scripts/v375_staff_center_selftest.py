"""Build-safe smoke tests for owner-scoped employee center and role controls."""
from pydantic import ValidationError
from scripts.account_schema_v1 import SCHEMA
from scripts.v370_staff_ui import render_workbench
from scripts.v368_manager_ui import render_manager
from scripts.v375_staff_center import FindingDecision, SavedSearch, REG, HASH
from scripts.v375_task_autoload import queue_missing


def run():
    html = render_workbench()
    assert 'data-staff-tab="overview"' in html
    assert 'data-staff-tab="activity"' in html
    assert "Календарь" not in html
    for name in ("staff-global-search","staff-case-draft","staff-saved-searches",
                 "staff-notification-list","staff-task-list","staff-progress-list"):
        assert name in html, name
    assert "account_staff_finding_decisions" in SCHEMA
    assert "account_manager_task_eis_jobs" in SCHEMA
    assert "account_staff_drafts" in SCHEMA
    assert "account_staff_saved_searches" in SCHEMA
    valid = FindingDecision(reg_number="32615969056", decision="confirmed")
    assert valid.decision == "confirmed"
    for invalid in ("123","3261596905x"):
        try:
            FindingDecision(reg_number=invalid,decision="confirmed")
        except ValidationError:
            pass
        else:
            raise AssertionError("Invalid procurement number was accepted")
    try:
        FindingDecision(reg_number="32615969056", decision="fabricate")
    except ValidationError:
        pass
    else:
        raise AssertionError("Unsupported finding decision")
    assert REG.fullmatch("32615969056") and HASH.fullmatch("f"*32)
    assert SavedSearch(phrase="32615969056").phrase=="32615969056"
    manager = render_manager("admin")
    assert "mgr-admin-search" in manager
    assert "mgr-admin-status" in manager
    assert "admin" in manager
    assert callable(queue_missing)
    print("V375_STAFF_CENTER_SELFTEST_OK owner_schemas=True calendar_absent=True role_search=True")


if __name__=="__main__":
    run()
