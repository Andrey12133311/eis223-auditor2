"""The homepage account panel stays compact without changing account access."""
import re
import unittest

from lxml import html

from scripts.account_site_ui_v1 import site_header


class CompactCabinetShell(unittest.TestCase):
    def page(self, role="user"):
        return html.fromstring(site_header(role).decode("utf-8"))

    def test_primary_lists_and_entry_points_are_visible_when_cabinet_opens(self):
        page = self.page()
        cabinet = page.get_element_by_id("eis223-cabinet")
        self.assertIsNotNone(cabinet.get("hidden"))
        for identifier in ("eis223-my-procurements", "eis223-my-batches", "eis223-my-memos", "eis223-add-procurement"):
            element = page.get_element_by_id(identifier)
            self.assertFalse(element.xpath('ancestor::details'))
        links = cabinet.xpath('.//div[contains(@class,"eis223-employee-links")]/a/@href')
        self.assertEqual(links, ["/account/workbench", "/account/home", "/account/thematic", "/account/memos"])
        self.assertEqual(len(cabinet.xpath('.//div[@class="eis223-columns"]/div')), 3)

    def test_secondary_widgets_are_closed_but_their_forms_and_statuses_are_retained(self):
        page = self.page()
        for identifier in ("eis223-alert-list", "eis223-favorite-form", "eis223-assignment-list", "eis223-manager-goals"):
            with self.subTest(identifier=identifier):
                details = page.get_element_by_id(identifier).xpath('ancestor::details')[0]
                self.assertIsNone(details.get("open"))
                self.assertTrue(details.xpath('./summary'))

    def test_cabinet_has_one_named_entry_action_and_accessible_identity(self):
        page = self.page()
        title = page.get_element_by_id('eis223-cabinet-title')
        self.assertEqual(title.text_content(), 'Мой кабинет')
        action = page.get_element_by_id('eis223-start-check')
        self.assertEqual(action.get('type'), 'button')
        self.assertEqual(action.get('aria-controls'), 'eis223-add-procurement')
        self.assertEqual(action.text_content(), 'Проверить закупку')
        identity = page.xpath('//*[@class="eis223-auth-user"]//a[@href="/account/profile"]')
        self.assertEqual(len(identity), 1)
        for identifier in ('eis223-topbar-fullname', 'eis223-topbar-role'):
            self.assertTrue(identity[0].xpath('.//*[@id="'+identifier+'"]'))
        self.assertEqual(page.xpath('//*[@class="eis223-tile eis223-tile-main"]//form/@id'), ['eis223-add-procurement'])

    def test_admin_grouping_preserves_role_gates_and_opening_control(self):
        for role in ("user", "manager"):
            page = self.page(role)
            self.assertFalse(page.xpath('//*[@id="eis223-admin-controls"]'))
            self.assertFalse(page.xpath('//*[@id="eis223-admin-open"]'))
        page = self.page("admin")
        controls = page.get_element_by_id("eis223-admin-controls")
        self.assertEqual(controls.tag, "details")
        self.assertIsNone(controls.get("open"))
        self.assertTrue(controls.xpath('.//*[@id="eis223-admin-open"]'))
        self.assertTrue(page.xpath('//a[@href="/account/admin"]'))
        self.assertFalse(self.page().xpath('//a[@href="/account/manager"]'))
        self.assertTrue(self.page("manager").xpath('//a[@href="/account/manager"]'))

    def test_all_javascript_targets_stay_unique_and_available(self):
        admin_ids = {"eis223-admin-controls", "eis223-admin-open", "eis223-admin-nav", "eis223-admin-panel",
                     "eis223-admin-users", "eis223-admin-status"}
        for role in ("user", "manager", "admin"):
            page = self.page(role)
            ids = page.xpath('//*[@id]/@id')
            self.assertEqual(len(ids), len(set(ids)))
            script = page.xpath('//script[@id="eis223-account-runtime"]/text()')[0]
            targets = set(re.findall(r"q\(['\"](eis223-[^'\"]+)['\"]\)", script))
            self.assertFalse(targets - set(ids) - (admin_ids if role != "admin" else set()))
            markup = site_header(role).decode()
            self.assertGreater(markup.index('id="eis223-workspace-polish"'), markup.index('id="eis223-cabinet-compact"'))
            self.assertIn("controls.open=true", script)


if __name__ == "__main__":
    unittest.main()
