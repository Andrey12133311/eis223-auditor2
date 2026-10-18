"""Offline card regressions; all records are synthetic, never production data.

Optional real Chromium checks (with its sandbox enabled):
EIS_CARD_BROWSER_TESTS=1 python -m unittest discover -s tests -p test_v343_card_presentation.py
"""
import copy
import os
import sqlite3
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import v343_fast_cards as card

REG = "32615969056"
SOURCE = "https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid=" + "A" * 32
OTHER_SOURCE = SOURCE.replace("A" * 32, "B" * 32)
FINDING = {
    "code": "223-4-12-PROTOCOL-CROSS-DOC-LATE", "kind": "violation",
    "title": "Протокол № 1 размещён позже крайнего дня публикации",
    "law": "223-ФЗ, ст. 4 ч. 12", "law_url": "",
    "evidence": "Протокол № 1 подписан 2026-10-06; крайний день 2026-10-09; размещён 2026-10-13.",
    "document": "Итоговый протокол № 1.pdf", "source_url": SOURCE,
    "match_text": "Дата размещения итогового протокола № 1 в ЕИС: 13.10.2026",
    "location": "Оригинал ЕИС, позиция 140",
    "note": "В двух оригиналах совпадают номер и вид протокола.",
}


class Node:
    def __init__(self, tag="root", attrs=(), parent=None):
        self.tag, self.attrs, self.parent = tag, dict(attrs), parent
        self.children = []
        self.text = ""

    def find(self, tag=None, **attrs):
        found = []
        if (tag is None or self.tag == tag) and all(self.attrs.get(k) == v for k, v in attrs.items()):
            found.append(self)
        for child in self.children:
            found.extend(child.find(tag, **attrs))
        return found

    def content(self):
        return self.text + "".join(child.content() for child in self.children)


class DOM(HTMLParser):
    VOID = {"meta", "br", "hr", "link", "input", "img"}

    def __init__(self, markup):
        super().__init__(convert_charrefs=True)
        self.root = self.current = Node()
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.current)
        self.current.children.append(node)
        if tag not in self.VOID:
            self.current = node

    def handle_endtag(self, tag):
        node = self.current
        while node.parent:
            if node.tag == tag:
                self.current = node.parent
                return
            node = node.parent

    def handle_data(self, data):
        self.current.text += data


def data(findings=None, checks=None):
    findings = [copy.deepcopy(FINDING)] if findings is None else findings
    checks = [] if checks is None else checks
    return ({"title": "Синтетическая закупка", "url": SOURCE,
             "documents_found": 1, "documents_checked": 1}, findings, checks,
            [{"name": FINDING["document"], "url": SOURCE, "status": "checked"}],
            len(findings), sum(x.get("status") == "violation" for x in checks), 1)


class CardPresentationTests(unittest.TestCase):
    def test_both_result_sections_are_native_collapsed_accordions(self):
        dom = DOM(card.render(REG, data())).root
        groups = dom.find("details")
        self.assertEqual([x.attrs["id"] for x in groups], ["audit-violations", "audit-review"])
        self.assertEqual([x.children[0].find("h2")[0].content() for x in groups],
                         ["Нарушения", "Требует проверки"])
        for group in groups:
            self.assertNotIn("open", group.attrs)
            summary = group.children[0]
            self.assertEqual(summary.tag, "summary")
            # Native summary must remain in the keyboard focus order and have
            # no nested interactive controls hijacking Enter or Space.
            self.assertNotIn("tabindex", summary.attrs)
            self.assertFalse(summary.find("a") or summary.find("button"))
            self.assertNotIn("onclick", summary.attrs)

    def test_redundant_buttons_removed_but_return_and_actions_remain(self):
        dom = DOM(card.render(REG, data())).root
        hrefs = [x.attrs.get("href") for x in dom.find("a")]
        self.assertNotIn("/manual-purchases", hrefs)
        self.assertNotIn("/thematic", hrefs)
        self.assertFalse(dom.find("nav"))
        back = dom.find("p", **{"class": "card-back"})[0]
        self.assertEqual(back.find("a")[0].attrs["href"], "/")
        self.assertEqual(back.content(), "← На главную")
        self.assertIn("/api/v304/memo/" + REG + ".odt", hrefs)
        action = dom.find("a", href="/api/v304/memo/" + REG + ".odt")[0]
        self.assertEqual(action.parent.attrs.get("class"), "card-actions")
        self.assertIn(SOURCE, hrefs)

    def test_main_navigation_still_exposes_manual_and_thematic_sections(self):
        from account_site_ui_v1 import site_header
        markup = site_header().decode()
        self.assertIn("/account/home", markup)
        self.assertTrue("/account/thematic" in markup or "/thematic" in markup)

    def test_mobile_has_wrapping_and_large_touch_targets(self):
        self.assertIn('name="viewport" content="width=device-width,initial-scale=1"', card.render(REG, data()))
        self.assertIn("@media(max-width:660px)", card.STYLE)
        self.assertIn("min-height:56px", card.STYLE)
        self.assertIn("overflow-wrap:anywhere", card.STYLE)
        self.assertIn(".result-group>summary h2{font-size:19px}", card.STYLE)
        self.assertIn(":focus-visible", card.STYLE)

    def test_original_facts_fragments_dates_and_explanations_are_preserved(self):
        dom = DOM(card.render(REG, data())).root
        finding = dom.find("article", **{"class": "legal"})[0]
        content = finding.content()
        for field in ("evidence", "match_text", "note", "document", "location"):
            self.assertIn(FINDING[field], content)
        links = finding.find("a")
        self.assertTrue(any(x.attrs["href"].startswith("https://www.consultant.ru/document/") for x in links))
        self.assertTrue(any(x.attrs["href"] == SOURCE and x.content() == FINDING["document"] for x in links))

    def test_eis_urls_in_evidence_are_inline_links_with_wording_unchanged(self):
        evidence = "Запрос (" + SOURCE + "); ответ (" + OTHER_SOURCE + "). <не HTML>"
        rendered = card.linked_evidence(evidence)
        dom = DOM("<p>" + rendered + "</p>").root
        self.assertEqual([x.attrs["href"] for x in dom.find("a")], [SOURCE, OTHER_SOURCE])
        self.assertIn("&lt;не HTML&gt;", rendered)
        self.assertIn("); ответ (", rendered)
        self.assertIn("). &lt;не HTML&gt;", rendered)

    def test_incomplete_rows_are_review_only_without_mutating_records(self):
        for changes in ({"evidence": ""}, {"evidence": "Нет данных"},
                        {"evidence": "Документы не получены"},
                        {"evidence": FINDING["title"]},
                        {"evidence": "Протокол опубликован с нарушением срока"},
                        {"law": "", "code": "UNKNOWN"}):
            with self.subTest(changes):
                original = data([{**FINDING, **changes}])
                before = copy.deepcopy(original)
                markup = card.render(REG, original)
                dom = DOM(markup).root
                self.assertFalse(dom.find("article", **{"class": "legal"}))
                self.assertEqual(len(dom.find("article", **{"class": "review"})), 1)
                self.assertIn("Почему требует проверки", markup)
                self.assertIn("Статусы и счётчики в базе не изменены", markup)
                self.assertIn("Сохранённых записей с отметкой «нарушение»: 1", markup)
                self.assertIn("Число подтверждённых нарушений в этой карточке: 0", markup)
                self.assertIn("/api/v304/memo/" + REG + ".odt", markup)
                self.assertIn("выгрузка ODT использует сохранённые результаты", markup)
                self.assertIn("Скачать ODT сохранённых результатов · требуется сверка", markup)
                self.assertEqual(original, before)

    def test_missing_optional_source_link_does_not_reclassify_evidenced_finding(self):
        for url in ("", "javascript:alert(1)"):
            with self.subTest(url):
                markup = card.render(REG, data([{**FINDING, "source_url": url}]))
                self.assertEqual(len(DOM(markup).root.find("article", **{"class": "legal"})), 1)
                self.assertIn("Проверяемая ссылка на оригинал ЕИС в сохранённой записи отсутствует", markup)
                self.assertNotIn("data-evidence-gap-notice", markup)

    def test_review_limit_explains_count_and_displayed_rows(self):
        checks = [{"item": "Проверить отдельный документ " + str(i), "status": "check",
                   "evidence": "Документ " + str(i)} for i in range(55)]
        markup = card.render(REG, data([], checks))
        dom = DOM(markup).root
        self.assertEqual(len(dom.find("article", **{"class": "review"})), 50)
        self.assertIn("Показаны первые 50 из 55 вопросов для проверки", markup)

    def test_missing_originals_do_not_create_any_finding(self):
        markup = card.render(REG, (None, [], [], [], 0, 0, 0))
        self.assertFalse(DOM(markup).root.find("article"))
        self.assertIn("Подтверждённые нарушения: 0", markup)
        self.assertIn("Оригиналы документов пока не сохранены", markup)

    def test_existing_review_never_promoted_even_with_complete_fields(self):
        check = {**FINDING, "item": FINDING["title"], "status": "check"}
        markup = card.render(REG, data([], [check]))
        self.assertFalse(DOM(markup).root.find("article", **{"class": "legal"}))
        self.assertEqual(len(DOM(markup).root.find("article", **{"class": "review"})), 1)

    def test_duplicate_sources_remain_accessible_once_per_episode(self):
        rows = [FINDING, {**FINDING, "source_url": OTHER_SOURCE, "document": "Публикация.pdf"}]
        markup = card.render(REG, data(rows))
        dom = DOM(markup).root
        self.assertEqual(len(dom.find("article", **{"class": "legal"})), 1)
        self.assertIn("Подтверждённые нарушения: 1", markup)
        self.assertIn("Исходных подтверждённых записей: 2", markup)
        for source in (SOURCE, OTHER_SOURCE):
            self.assertTrue(dom.find("a", href=source))

    def test_untrusted_source_urls_are_not_links(self):
        for url in ("javascript:alert(1)", "data:text/html,evil", "//zakupki.gov.ru/file",
                    "https://zakupki.gov.ru.evil.test/file", "https://evilzakupki.gov.ru/file",
                    "https://zakupki.gov.ru@evil.test/file", "https://evil.test@zakupki.gov.ru/file",
                    "https://zakupki.gov.ru:9999/file", "https://zakupki.gov.ru\\@evil.test/file",
                    "https://zakupki.gov.ru/\nfile", "https://zakupki.gov.ru/ file",
                    "https://[invalid/file"):
            with self.subTest(url):
                self.assertEqual(card.source_link(url, "Оригинал"), "")

    def test_allowed_eis_links_escape_html_and_protect_new_windows(self):
        for url in (SOURCE, "https://www.zakupki.gov.ru/test?a=1&b=2", "http://zakupki.gov.ru/test"):
            dom = DOM(card.source_link(url, '<img src=x onerror="alert(1)">')).root
            anchor = dom.find("a")[0]
            self.assertEqual(anchor.attrs["href"], url)
            self.assertEqual(anchor.attrs["target"], "_blank")
            self.assertEqual(set(anchor.attrs["rel"].split()), {"noopener", "noreferrer"})
            self.assertFalse(dom.find("img"))

    def test_unknown_law_is_not_relabelled_as_223_fz(self):
        for law in ("44-ФЗ, ст. 4 ч. 12", "ГК РФ, ст. 4", "Локальное положение, ст. 4"):
            with self.subTest(law):
                self.assertEqual(card.verified_legal_references({"law": law}), [])
                self.assertNotIn("href=", card.legal_links({"law": law}, law, "javascript:alert(1)"))

    def test_law_urls_require_https_and_exact_consultant_host(self):
        for url in ("javascript:alert(1)", "https://www.consultant.ru.evil.test/document/cons_doc_LAW_1/",
                    "https://evil.test@www.consultant.ru/document/cons_doc_LAW_1/",
                    "https://user:password@www.consultant.ru/document/cons_doc_LAW_1/",
                    "https://www.consultant.ru:9999/document/cons_doc_LAW_1/",
                    "https://www.consultant.ru\\@evil.test/document/cons_doc_LAW_1/",
                    "http://www.consultant.ru/document/cons_doc_LAW_1/",
                    "https://www.consultant.ru/document/cons_doc_LAW_1/\n"):
            with self.subTest(url):
                self.assertFalse(card.safe_law_url(url))
        self.assertTrue(card.safe_law_url("https://www.consultant.ru/document/cons_doc_LAW_116964/"))

    def test_sqlite_read_keeps_saved_location_fragment_note_and_is_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "synthetic.sqlite"
            db = sqlite3.connect(path)
            db.executescript("""
                CREATE TABLE purchases(reg_number TEXT PRIMARY KEY);
                CREATE TABLE findings(id INTEGER PRIMARY KEY, reg_number TEXT, kind TEXT,
                  title TEXT, law TEXT, evidence TEXT, source_url TEXT, document TEXT,
                  location TEXT, match_text TEXT, note TEXT);
                CREATE TABLE checks(id INTEGER PRIMARY KEY, reg_number TEXT, status TEXT);
                CREATE TABLE docs(id INTEGER PRIMARY KEY, reg_number TEXT, name TEXT,
                  doc_type TEXT, status TEXT, url TEXT, text_chars INTEGER);
            """)
            db.execute("INSERT INTO findings VALUES(1,?,?,?,?,?,?,?,?,?,?)", (
                REG, "violation", FINDING["title"], FINDING["law"], FINDING["evidence"], SOURCE,
                FINDING["document"], FINDING["location"], FINDING["match_text"], FINDING["note"]))
            db.commit()
            before = list(db.iterdump())
            saved = card.read({"conn": lambda: sqlite3.connect(path)}, REG)
            for field in ("location", "match_text", "note"):
                self.assertEqual(saved[1][0][field], FINDING[field])
            card.render(REG, saved)
            self.assertEqual(list(db.iterdump()), before)
            db.close()


@unittest.skipUnless(os.environ.get("EIS_CARD_BROWSER_TESTS") == "1", "Optional sandboxed Chromium checks")
class CardBrowserTests(unittest.TestCase):
    def test_click_enter_space_and_mobile_width(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path="/usr/bin/chromium", chromium_sandbox=True)
            try:
                for width in (320, 375, 1280):
                    page = browser.new_page(viewport={"width": width, "height": 900})
                    page.set_content(card.render(REG, data()))
                    for key in ("audit-violations", "audit-review"):
                        details = page.locator("#" + key)
                        summary = details.locator("summary")
                        self.assertFalse(details.evaluate("element => element.open"))
                        summary.click()
                        self.assertTrue(details.evaluate("element => element.open"))
                        summary.focus()
                        page.keyboard.press("Enter")
                        self.assertFalse(details.evaluate("element => element.open"))
                        page.keyboard.press("Space")
                        self.assertTrue(details.evaluate("element => element.open"))
                        self.assertGreaterEqual(summary.bounding_box()["height"], 44)
                    self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width)
                    page.close()
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
