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
            self.assertLess(render_manager(role).index('id="eis223-account-polish"'),
                            render_manager(role).index('id="eis223-manager-compact"'))

    def test_role_routes_and_guard_are_preserved(self):
        self.assertFalse(self.page("manager").xpath('//header//a[@href="/account/admin"]'))
        self.assertTrue(self.page("admin").xpath('//header//a[@href="/account/admin"]'))
        with self.assertRaises(ValueError):
            render_manager("user")


if __name__ == "__main__":
    unittest.main()
