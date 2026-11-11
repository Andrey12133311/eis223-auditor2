"""The compact manager view keeps daily work visible and all actions reachable."""
import re
import unittest

from lxml import html

from scripts.v368_manager_ui import SCRIPT, render_manager


class CompactManager(unittest.TestCase):
    def page(self, role="manager"):
        return html.fromstring(render_manager(role))

    def test_daily_navigation_is_visible_and_additional_sections_remain_reachable(self):
        page = self.page()
        navigation = page.xpath('//nav[@aria-label="Разделы руководителя"]')[0]
        self.assertEqual([button.get("data-tab") for button in navigation.xpath('./button')],
                         ["overview", "tasks", "thematic", "findings", "memos", "team"])
        secondary = navigation.xpath('./details')[0]
        self.assertIsNone(secondary.get("open"))
        self.assertEqual([button.get("data-tab") for button in secondary.xpath('./button')],
                         ["quality", "reports", "history"])
        tabs = page.xpath('//*[@data-tab]/@data-tab')
        sections = page.xpath('//*[@data-section]/@data-section')
        self.assertEqual(set(tabs), set(sections))
        self.assertEqual(len(tabs), len(set(tabs)))

    def test_overview_prioritizes_four_action_metrics_without_removing_statistics(self):
        page = self.page()
        overview = page.xpath('//section[@data-section="overview"]')[0]
        metrics = overview.xpath('./div[contains(@class,"mgr-grid")]//strong/@id')
        self.assertEqual(metrics, ["mgr-kpi-pending", "mgr-kpi-overdue", "mgr-kpi-memos", "mgr-kpi-staff"])
        statistics = overview.xpath('./details')[0]
        self.assertIsNone(statistics.get("open"))
        self.assertEqual(set(statistics.xpath('.//strong/@id')),
                         {"mgr-kpi-tasks", "mgr-kpi-findings", "mgr-kpi-accepted", "mgr-kpi-documents"})
        self.assertTrue(statistics.xpath('.//*[@id="mgr-bars"]'))
        self.assertTrue(overview.xpath('./div//*[@id="mgr-alerts"]'))

    def test_long_forms_and_settings_are_closed_but_controls_stay_in_the_dom(self):
        page = self.page("admin")
        for identifier, control in (
            ("mgr-new-task", "mgr-task-create"),
            ("mgr-new-goal", "mgr-goal-form"),
            ("mgr-case-review", "mgr-finding-load"),
            ("mgr-self-attach", "mgr-attach-self"),
            ("mgr-admin-section", "mgr-admin-search"),
        ):
            with self.subTest(identifier=identifier):
                node = page.get_element_by_id(identifier)
                self.assertEqual(node.tag, "details")
                self.assertIsNone(node.get("open"))
                self.assertTrue(node.xpath('./summary'))
                self.assertTrue(node.xpath('.//*[@id=$control]', control=control))

    def test_page_keeps_every_static_javascript_control_and_unique_ids(self):
        for role in ("manager", "admin"):
            page = self.page(role)
            ids = page.xpath('//*[@id]/@id')
            self.assertEqual(len(ids), len(set(ids)))
            referenced = set(re.findall(r'\$\(["\'](mgr-[^"\']+)["\']\)', SCRIPT))
            self.assertFalse(referenced - set(ids), referenced - set(ids))
            self.assertGreater(render_manager(role).index('id="eis223-account-polish"'),
                            render_manager(role).index('id="eis223-manager-compact"'))

    def test_role_routes_and_guard_are_preserved(self):
        self.assertFalse(self.page("manager").xpath('//header//a[@href="/account/admin"]'))
        self.assertTrue(self.page("admin").xpath('//header//a[@href="/account/admin"]'))
        with self.assertRaises(ValueError):
            render_manager("user")

    def test_action_metrics_are_native_keyboard_controls_with_existing_destinations(self):
        page = self.page()
        shortcuts = page.xpath('//*[@data-mgr-shortcut]')
        self.assertEqual([node.get('data-mgr-shortcut') for node in shortcuts],
                         ['pending', 'overdue', 'memos', 'team'])
        self.assertTrue(all(node.tag == 'button' and node.get('type') == 'button'
                            for node in shortcuts))
        self.assertTrue(all(node.xpath('./strong[@id]') for node in shortcuts))
        self.assertEqual(page.get_element_by_id('mgr-title').get('tabindex'), '-1')
        statuses = page.get_element_by_id('mgr-task-status').xpath('./option/@value')
        self.assertIn('overdue', statuses)
        self.assertIn('submitted', statuses)
        self.assertEqual(page.get_element_by_id('mgr-task-count').get('role'), 'status')
        self.assertEqual(page.get_element_by_id('mgr-memo-count').get('role'), 'status')
        for identifier in ('mgr-task-reset', 'mgr-task-empty-reset', 'mgr-memo-empty-reset'):
            self.assertEqual(page.get_element_by_id(identifier).get('type'), 'button')

    def test_navigation_icons_are_decorative_local_vectors(self):
        page = self.page()
        icons = page.xpath('//nav[@aria-label="Разделы руководителя"]//span[@class="mgr-tab-icon"]')
        self.assertEqual(len(icons), 9)
        for icon in icons:
            self.assertEqual(icon.get('aria-hidden'), 'true')
            self.assertEqual(len(icon.xpath('./svg')), 1)
            self.assertEqual(icon.xpath('./svg/@focusable'), ['false'])
            self.assertFalse(icon.text and icon.text.strip())
        self.assertNotIn('__ICON_', render_manager('manager'))


if __name__ == "__main__":
    unittest.main()
