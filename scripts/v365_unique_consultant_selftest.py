"""Non-destructive regression for unique violations and pinpoint ConsultantPlus links.

Uses only ephemeral SQLite fixtures. Never modifies production rows, counters,
original EIS downloads, prior legal decisions or evidence.
"""
import inspect
import sqlite3
import tempfile
from pathlib import Path

from v365_consultant_refs import (
    ARTICLES, LAW_1352, LAW_223, consultant_url, enrich, references, legal_identity,
)
from v352_dashboard_truth import collapse_legal_events, load_counts
from v343_fast_cards import detail_block, render_thematic_panel, legal_links
from v342_thematic_queue import init, thematic_history_records

PAYMENT=("Расчет производится в течение 15 рабочих дней с даты подписания "
         "заказчиком документа о приемке товара по договору. "+("Условия оплаты договора. "*8))
OTHER=PAYMENT.replace("15 рабочих","25 рабочих")
CLARIFICATION=("Запрос разъяснений поступил 06.10.2026. Разъяснение размещено 13.10.2026. "
               +("Сведения об опубликованном разъяснении подтверждены ЕИС. "*4))


def test_norms():
    r=references({"law":"223-ФЗ, ч. 11 ст. 4"})
    assert len(r)==1,r
    assert r[0]["article"]=="4" and r[0]["part"]=="11"
    assert r[0]["url"].startswith(LAW_223+ARTICLES["4"])
    assert "#:~:text=" in r[0]["url"],r
    r=references({"law":"223-ФЗ, ст. 3.2 ч. 3"})
    assert r and "ч. 3" in r[0]["label"],r
    assert "#:~:text=" in r[0]["url"]
    refs=references({"law":"223-ФЗ, ст. 3 ч. 1 п. 2; ч. 6"})
    assert len(refs)==2,refs
    assert refs[0]["label"]=="223-ФЗ · ст. 3 · ч. 1 · п. 2"
    assert refs[1]["label"]=="223-ФЗ · ст. 3 · ч. 6"
    part=references({"law":"223-ФЗ, ст. 4 ч. 9 п. 8.3"})
    assert len(part)==1 and "п. 8.3" in part[0]["label"],part
    a=references({"law":"Положение № 1352, п. 28"})
    assert len(a)==1 and a[0]["url"].startswith(LAW_1352)
    assert "#:~:text=" in a[0]["url"]
    if references({"law":"Не установлено"}):
        raise AssertionError("Must not invent a legal basis")
    assert not references({"law":"223-ФЗ, ст. 999"})
    assert not consultant_url("https://evil.example/document/cons_doc_LAW_116964/")
    assert consultant_url(r[0]["url"])
    assert legal_identity("ПП РФ № 1352 п. 28")==legal_identity("Положение № 1352, п. 28")
    enriched=enrich({"law":"223-ФЗ, ст. 4 ч. 11","law_url":LAW_223,"evidence":"ЕИС"})
    assert enriched["law"]=="223-ФЗ, ст. 4 ч. 11"
    assert enriched["consultant_url"]==enriched["law_url"]
    assert len(enriched["consultant_references"])==1
    assert legal_identity("223-ФЗ, ст. 4 ч. 11")!=legal_identity("223-ФЗ, ст. 4 ч. 12")


def test_merge():
    source="https://zakupki.gov.ru/223/filestore/public/1.0/download/test"
    raw=[
      {"title":"Нарушен срок оплаты МСП","law":"ПП РФ № 1352 п. 28",
       "evidence":PAYMENT,"document":"Договор.pdf","source_url":source},
      {"title":"Срок оплаты МСП превышен","law":"Положение № 1352, п. 28",
       "evidence":PAYMENT,"document":"Документация.docx","source_url":source+"/2"},
      {"title":"Нарушен срок оплаты МСП","law":"ПП РФ № 1352 п. 28",
       "evidence":"Файл/источник: Договор.pdf; фрагмент: "+PAYMENT,
       "document":"Договор.pdf","source_url":source},
    ]
    unique=collapse_legal_events(raw)
    assert len(unique)==1,unique
    assert unique[0]["raw_count"]==3,unique
    assert len(unique[0]["supporting_sources"])==2,unique
    assert len(collapse_legal_events(raw+[dict(raw[0],evidence=OTHER)]))==2
    assert len(collapse_legal_events(raw+[dict(raw[0],law="223-ФЗ, ст. 4 ч. 10")]))==2
    a={"title":"Нарушение срока разъяснений","law":"223-ФЗ, ст. 3.2 ч. 3",
       "evidence":CLARIFICATION}
    b=dict(a,evidence=CLARIFICATION.replace("06.10.2026","2026-10-06")
           .replace("13.10.2026","2026-10-13"))
    assert len(collapse_legal_events([a,b]))==1
    third=dict(a,evidence=CLARIFICATION.replace("13.10.2026","14.10.2026"))
    assert len(collapse_legal_events([a,third]))==2


def test_visible_links():
    item={"title":"Просроченная публикация изменений","law":"223-ФЗ ст. 4 ч. 11",
          "evidence":PAYMENT,"document":"Изменение.pdf","source_url":"https://zakupki.gov.ru/file"}
    link=legal_links(item,item["law"],LAW_223)
    assert "consultant.ru/document/cons_doc_LAW_116964/" in link
    assert "ст. 4" in link and "ч. 11" in link and "КонсультантПлюс" in link
    html=detail_block(item,"legal",item["title"],item["law"],item["evidence"],
                      item["document"],item["source_url"],LAW_223)
    assert "href=" in html and "consultant.ru" in html
    assert "zakupki.gov.ru" in html
    assert "&lt;script&gt;" in detail_block(
        {**item,"law":"<script>alert(1)</script>"},"legal",
        item["title"],"<script>alert(1)</script>",item["evidence"],
        item["document"],item["source_url"],LAW_223)
    assert "<script>alert(1)</script>" not in html


def test_thematic_and_main_counts():
    with tempfile.TemporaryDirectory(prefix="eis365-") as temp:
        db_path=Path(temp)/"fixture.sqlite"
        def conn():
            c=sqlite3.connect(db_path,timeout=2)
            c.row_factory=sqlite3.Row
            return c
        ns={"conn":conn}
        init(ns)
        batch="a"*24
        with conn() as db:
            db.execute("INSERT INTO thematic_batches(id,categories,created_at,status) VALUES(?,?,?,?)",
                       (batch,'["contract"]',"2026-10-17T12:00:00+00:00","active"))
            for name,doc in (("Нарушен срок оплаты МСП","Договор.pdf"),
                             ("Срок оплаты МСП превышен","Документация.docx"),
                             ("Нарушение срока оплаты МСП","Копия договора.pdf")):
                db.execute(
                    "INSERT INTO thematic_findings(batch_id,reg,category,title,law,evidence,document) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (batch,"32616411111","contract",name,"ПП РФ № 1352 п. 28",PAYMENT,doc))
            db.executescript("""
            CREATE TABLE findings(reg_number TEXT,title TEXT,law TEXT,evidence TEXT,
               document TEXT,source_url TEXT,code TEXT,kind TEXT);
            CREATE TABLE checks(reg_number TEXT,item TEXT,law TEXT,evidence TEXT,
               document TEXT,source_url TEXT,status TEXT);""")
            db.execute("INSERT INTO findings VALUES(?,?,?,?,?,?,?,?)",
                       ("32616411111","Нарушен срок оплаты МСП","ПП РФ № 1352 п. 28",
                        PAYMENT,"Договор.pdf","https://zakupki.gov.ru/file","1352-28","violation"))
            db.execute("INSERT INTO checks VALUES(?,?,?,?,?,?,?)",
                       ("32616411111","Срок оплаты МСП превышен","Положение № 1352 п. 28",
                        PAYMENT,"Документация.pdf","https://zakupki.gov.ru/file2","violation"))
        history=thematic_history_records(ns,[batch])
        assert len(history["batches"])==1
        assert history["batches"][0]["violations"]==1,history
        assert thematic_history_records(ns,[])=={"batches":[]}
        with conn() as db:
            assert load_counts(db,["32616411111"])=={"32616411111":1}
            assert db.execute("SELECT COUNT(*) FROM thematic_findings").fetchone()[0]==3
            assert db.execute("SELECT COUNT(*) FROM findings").fetchone()[0]==1
            assert db.execute("SELECT COUNT(*) FROM checks").fetchone()[0]==1


def test_legacy_api():
    import v333_dedup_ui
    source=inspect.getsource(v333_dedup_ui.install)
    assert "collapse_legal_events(confirmed)" in source
    assert "enrich(x)" in source


if __name__=="__main__":
    test_norms()
    test_merge()
    test_visible_links()
    test_thematic_and_main_counts()
    test_legacy_api()
    print("V365_UNIQUE_CONSULTANT_SELFTEST_OK duplicate_three_to_one=True "
          "distinct_episodes_kept=True EIS_originals_unchanged=True "
          "consultant_precise_statute=True memorial_links=True "
          "real_thematic_counts=True main_counts_agree=True", flush=True)
