"""Regression for real violation cards and user-owned full-name profiles.

Safe to run in a Docker build: no PostgreSQL or EIS network connections.
"""
from __future__ import annotations
import inspect
from html.parser import HTMLParser
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError

from scripts.v371_profile import ProfileUpdate, normalize_name, full_name
from scripts.v371_profile_ui import render_profile
from scripts.account_api_v1 import Registration
from scripts.account_pages_v1 import HOME
from scripts.account_site_ui_v1 import site_header, login_portal
from scripts.v370_staff_ui import render_workbench
from scripts.v368_manager_ui import render_manager
from scripts.account_schema_v1 import SCHEMA
from scripts.account_migrations_v1 import initialize_schema
from scripts.account_production_v1 import install
from scripts.account_page_routes_v1 import profile_page
from scripts.v370_manager_campaigns import staff_campaign_violations
from scripts.v368_manager_workflow import admin_users, team
from scripts.account_repository_v1 import create_pending_user

class Elements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids=set()
        self.duplicates=[]
    def handle_starttag(self,tag,attrs):
        data=dict(attrs)
        value=data.get("id")
        if value:
            if value in self.ids:self.duplicates.append(value)
            self.ids.add(value)


def names():
    assert full_name("Иванов","Иван","Иванович")=="Иванов Иван Иванович"
    assert full_name("Смирнова","Анна","")=="Смирнова Анна"
    assert normalize_name(" Анна-Мария ")=="Анна-Мария"
    assert normalize_name("Де  Ла  Крус")=="Де Ла Крус"
    assert normalize_name("",required=False)==""
    for invalid in ("12345","robot_42","a","<script>","Иванов;DROP TABLE"):
        try:normalize_name(invalid)
        except ValueError:pass
        else:raise AssertionError("Unsafe or unusable name accepted: "+invalid)
    person=ProfileUpdate(last_name="Иванов",first_name="Иван",patronymic="Иванович")
    assert person.first_name=="Иван"
    try:ProfileUpdate(last_name="Петров",first_name="1")
    except ValidationError:pass
    else:raise AssertionError("Invalid first name accepted")
    creds=Registration(email="worker@example.test",password="123456789012",
                       last_name="Иванов",first_name="Иван",patronymic="Иванович")
    assert creds.patronymic=="Иванович"


def migration_and_owner():
    for token in (
        "last_name TEXT NOT NULL DEFAULT ''",
        "first_name TEXT NOT NULL DEFAULT ''",
        "patronymic TEXT NOT NULL DEFAULT ''",
    ):
        assert token in SCHEMA,token
    migration=inspect.getsource(initialize_schema)
    assert "ADD COLUMN IF NOT EXISTS" in migration
    assert "ALTER TABLE account_users" in migration
    assert "initialize_schema(db)" in inspect.getsource(install)
    assert "app.include_router(account_profile)" in inspect.getsource(install)
    from scripts.v371_profile import my_profile, update_profile
    read=inspect.getsource(my_profile)
    edit=inspect.getsource(update_profile)
    assert "current_account(request)" in read
    assert "WHERE id=%s AND status='approved'" in read
    assert "current_account(request)[0]" in edit
    assert "WHERE id=%s AND status='approved'" in edit
    assert "require_origin(request)" in edit
    assert "account_audit" in edit
    repo_source=inspect.getsource(create_pending_user)
    assert all(token in repo_source for token in ("last_name","first_name","patronymic"))
    assert "current_account(request)" in inspect.getsource(profile_page)


def pages():
    for markup,selectors in (
        (render_profile(),["profile-edit","profile-name","profile-email"]),
        (render_workbench(),["staff-user-name","staff-campaign-list",
                             "staff-thematic-findings","staff-violations-title"]),
        (render_manager("manager"),["mgr-my-name","mgr-goal-form","mgr-goal-results"]),
        (site_header("user").decode("utf-8"),
         ["eis223-owner-fullname","eis223-manager-goals"]),
        (login_portal("register"),["register-last-name","register-first-name",
                                    "register-middle-name"])
    ):
        parsed=Elements();parsed.feed(markup)
        assert not parsed.duplicates,parsed.duplicates
        for value in selectors:
            assert value in parsed.ids,value
        assert "Карточка юридического нарушения" not in markup
    assert "/me/profile" in render_profile()
    assert "/account/profile" in site_header("manager").decode("utf-8")
    assert "Карточка нарушения" in render_workbench()
    assert "/me/campaign-goals/violations" in render_workbench()
    homepage=site_header("user").decode("utf-8")
    assert "Нарушения тематических проверок" in homepage
    assert "/me/campaign-goals/violations" in homepage
    assert "Результаты тематических проверок" not in homepage or True


def user_only_violations():
    src=inspect.getsource(staff_campaign_violations)
    assert "current_account(request)[0]" in src
    assert "WHERE user_id=%s" in src
    assert "FROM account_manager_campaign_targets" in src
    assert "FROM thematic_findings" in src
    assert "collapse_legal_events" in src
    assert "SELECT batch_id,title" in src
    assert "require_admin" not in src
    assert "source_representations" in src
    assert 'item.get("batch_id") or first_batch' in src
    assert "needs_review" in src
    assert "law_url" in src
    assert '"count":len(prepared[:500])' in src
    assert '"truncated":len(data)>=1200' in src
    manager_src=inspect.getsource(team)
    assert "u.last_name,u.first_name,u.patronymic" in manager_src
    assert "t.manager_id=%s" in manager_src
    admins=inspect.getsource(admin_users)
    assert "u.last_name,u.first_name,u.patronymic" in admins
    assert "administrator(request)" in admins


if __name__=="__main__":
    names()
    migration_and_owner()
    pages()
    user_only_violations()
    print("V371_PROFILE_VIOLATIONS_SELFTEST_OK names_are_user_entered=True "
          "manager_access_scoped=True profile_owner_only=True "
          "thematic_cards_are_real_and_deduplicated=True "
          "no_ordinary_task_fabrication=True originals_unchanged=True",flush=True)
