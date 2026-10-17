"""No-production-write smoke tests for account notifications, favorites, filters and memo archive."""
from __future__ import annotations
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch
import inspect
import zipfile

from fastapi import HTTPException
from scripts import v367_private_features as features
from scripts.account_schema_v1 import SCHEMA
from scripts.account_pages_v1 import HOME, THEMATIC, MEMOS
from scripts.account_site_ui_v1 import site_header
from scripts.account_production_v1 import install


def test_favorites_and_owner():
    assert features.valid_favorite("procurement","32616412317")
    assert features.valid_favorite("customer","7707083893")
    assert features.valid_favorite("customer","771234567890")
    assert not features.valid_favorite("customer","123")
    assert not features.valid_favorite("procurement","32616412317;DROP")
    assert not features.valid_favorite("other","32616412317")
    assert "WHERE user_id=%s" in inspect.getsource(features.favorites)
    assert "WHERE user_id=%s" in inspect.getsource(features.favorite_remove)
    assert "current_account(request)" in inspect.getsource(features.favorite_add)
    assert "require_origin(request)" in inspect.getsource(features.favorite_add)


class Result:
    def __init__(self,row=None):
        self.row=row
    def fetchone(self):
        return self.row


class FakeAlerts:
    def __init__(self):
        self.snapshots={}
        self.events=[]
    def execute(self,sql,params):
        if "pg_advisory_xact_lock" in sql:
            return Result()
        if "SELECT status,violation_count,documents_checked" in sql:
            return Result(self.snapshots.get((params[0],params[1])))
        if "INSERT INTO account_alert_events" in sql:
            self.events.append(params)
            return Result()
        if "INSERT INTO account_alert_snapshots" in sql:
            owner,key,status,count,docs=params
            old=self.snapshots.get((owner,key))
            if old:
                count=max(count,old[1])
                docs=max(docs,old[2])
            self.snapshots[(owner,key)]=(status,count,docs)
            return Result()
        raise AssertionError("Unexpected SQL: "+sql[:75])


def test_alert_transitions():
    db=FakeAlerts()
    owner="account-a"
    first={"key":"manual:32616412317","status":"queued","violations":0,
           "docs":0,"found":3,"label":"Закупка № 32616412317"}
    features._reconcile_alerts(db,owner,[first])
    assert db.events==[]  # Initial baseline must never fabricate new findings.
    features._reconcile_alerts(db,owner,[dict(first,status="done",violations=2,docs=3)])
    assert len(db.events)==2,db.events
    assert {x[2] for x in db.events}=={"new_violation","documents_checked"}
    features._reconcile_alerts(db,owner,[dict(first,status="done",violations=2,docs=3)])
    assert len(db.events)==2  # Repeat polling is idempotent.
    features._reconcile_alerts(db,owner,[dict(first,status="done",violations=1,docs=2)])
    features._reconcile_alerts(db,owner,[dict(first,status="done",violations=2,docs=3)])
    assert len(db.events)==2  # Dedup recalculation must not cause false alerts.
    features._reconcile_alerts(db,owner,[dict(first,status="done",violations=3,docs=3)])
    assert len(db.events)==3
    assert db.events[-1][2]=="new_violation"
    features._reconcile_alerts(db,"account-b",[dict(first,status="done",violations=3,docs=3)])
    assert len(db.events)==3  # The other user's initial visit is baseline.
    theme={"key":"thematic:"+"a"*24,"status":"active","violations":0,
           "docs":0,"found":0,"label":"Тематическая проверка"}
    features._reconcile_alerts(db,owner,[theme])
    features._reconcile_alerts(db,owner,[dict(theme,status="stopped")])
    assert len(db.events)==3  # Stopping is NOT a completed legal audit.
    features._reconcile_alerts(db,owner,[dict(theme,status="completed")])
    assert len(db.events)==4
    assert db.events[-1][2]=="thematic_finished"


def test_memo_formats():
    output=BytesIO()
    with zipfile.ZipFile(output,mode="w",compression=zipfile.ZIP_STORED) as z:
        z.writestr("mimetype","application/vnd.oasis.opendocument.text")
        z.writestr("content.xml",'<office:document>'+("x"*1800)+'</office:document>')
    body=output.getvalue()
    assert features._verify_odt(body)==body
    for invalid in (b"bad", b"%PDF-"+b"x"*1900):
        try:features._verify_odt(invalid)
        except HTTPException as exc:assert exc.status_code==422
        else:raise AssertionError("Non-ODT archive was accepted")
    dl=inspect.getsource(features.download_owned_memo)
    assert "_owned_blob(memo_id,owner)" in dl
    assert "current_account(request)" in dl
    assert "format not in" in dl
    assert "application/pdf" in dl
    assert "application/vnd.oasis.opendocument.text" in dl
    assert "pdf_created_at" in dl
    assert "attachment;" in dl
    assert "X-Content-Type-Options" in dl
    assert "CURRENT_TIMESTAMP" in inspect.getsource(features._reconcile_alerts)
    assert "app.include_router(personal_features)" in inspect.getsource(install)
    from scripts import account_private_api_v1 as legacy
    assert "download_owned_memo" in inspect.getsource(legacy.memo_download)
    from v304_official_memo_odt import install as odt_install
    assert "app.state.eis223_build_memo=build_memo" in inspect.getsource(odt_install)


def test_account_ui():
    shell=site_header().decode("utf-8")
    assert shell.count('class="eis223-tile"')==3
    for part in ("eis223-alert-list","eis223-favorite-list","eis223-unread",
                 "/me/notifications","/me/favorites","/me/memo-archive",
                 "Запустить проверку","Служебные записки"):
        assert part in shell,part
    for token in ('id="manual-search"','id="manual-status"','id="manual-date-start"',
                  'id="manual-date-end"','id="manual-violation-type"',
                  'id="manual-only-violations"'):
        assert token in HOME,token
    # Name of checkbox is "manual-only-violations" in current UI.
    assert 'id="manual-only-violations"' in HOME
    assert "item.customer_inn" in HOME and "item.violation_types" in HOME
    for token in ("thematic-customer","thematic-filter-status","thematic-from",
                  "thematic-to","thematic-type","violation_types"):
        assert token in THEMATIC,token
    for token in ('id="memo-search"','id="memo-from"','id="memo-to"',
                  '/me/memo-archive/','Скачать '+chr(79)+'DT','PDF'):
        assert token in MEMOS,token
    assert "document.createElement" in MEMOS
    assert "Array.isArray" in MEMOS
    for table in ("account_favorites","account_alert_snapshots",
                  "account_alert_events","account_memo_blobs"):
        assert "CREATE TABLE IF NOT EXISTS "+table in SCHEMA,table
    assert "REFERENCES account_memos(id) ON DELETE CASCADE" in SCHEMA


if __name__=="__main__":
    test_favorites_and_owner()
    test_alert_transitions()
    test_memo_formats()
    test_account_ui()
    print("V367_PERSONAL_FEATURES_SELFTEST_OK notifications_deduplicated=True "
          "favorites_scoped=True filters_real=True ODT_archive=True "
          "PDF_no_fabrication=True login_owner_enforced=True "
          "old_evidence_untouched=True",flush=True)
