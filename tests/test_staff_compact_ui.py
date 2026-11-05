"""Daily work stays visible while secondary tools remain keyboard reachable."""
import re
import unittest

from lxml import html

from scripts.v370_staff_ui import JS, render_workbench


class CompactStaff(unittest.TestCase):
    def page(self):
        return html.fromstring(render_workbench())

    def test_one_daily_overview_replaces_the_duplicate_today_screen(self):
        page = self.page()
        tabs = page.xpath('//*[@data-staff-tab]/@data-staff-tab')
        sections = page.xpath('//*[@data-staff-section]/@data-staff-section')
        self.assertEqual(set(tabs), set(sections))
        self.assertEqual(len(tabs), 7)
        self.assertEqual(len(tabs), len(set(tabs)))
        self.assertNotIn("today", tabs)
        self.assertEqual(page.xpath('//section[@data-staff-section and not(@hidden)]/@data-staff-section'), ["overview"])
        self.assertEqual(len(page.xpath('//h1')), 1)

    def test_primary_navigation_and_optional_sections_keep_all_existing_tools(self):
        page = self.page()
        navigation = page.xpath('//nav[@aria-label="Разделы рабочего места"]')[0]
        self.assertEqual(navigation.xpath('./button/@data-staff-tab'), ["overview", "goals", "bulk", "case"])
        secondary = navigation.xpath('./details')[0]
        self.assertIsNone(secondary.get("open"))
        self.assertEqual(secondary.xpath('./button/@data-staff-tab'), ["templates", "activity", "library"])

    def test_action_metrics_precede_worklists_and_historical_totals_are_collapsed(self):
        page = self.page()
        overview = page.xpath('//section[@data-staff-section="overview"]')[0]
        visible = overview.xpath('./div[@class="grid"]//strong/@id')
        self.assertEqual(visible, ["staff-active-tasks", "staff-active-campaigns", "staff-unread", "staff-reviewed"])
        statistics = overview.xpath('./details[contains(@class,"staff-statistics")]')[0]
        self.assertIsNone(statistics.get("open"))
        self.assertEqual(len(statistics.xpath('.//strong')), 6)
        self.assertTrue(overview.xpath('.//*[@id="staff-task-list"]'))
        self.assertTrue(overview.xpath('.//*[@id="staff-notification-list"]'))

    def test_long_case_tools_and_template_creation_are_available_on_demand(self):
        page = self.page()
        for identifier in ("staff-template-form", "staff-checklist", "staff-manual-form", "staff-related",
                           "staff-version-diff", "staff-original-search", "staff-search-form", "staff-message-form", "staff-priority"):
            with self.subTest(identifier=identifier):
                node = page.get_element_by_id(identifier)
                parents = node.xpath('ancestor::details')
                self.assertTrue(parents)
                self.assertTrue(all(parent.get("open") is None for parent in parents))
                self.assertTrue(all(parent.xpath('./summary') for parent in parents))

    def test_javascript_controls_remain_unique_and_existing_links_are_preserved(self):
        page = self.page()
        ids = page.xpath('//*[@id]/@id')
        self.assertEqual(len(ids), len(set(ids)))
        referenced = set(re.findall(r"el\(['\"](staff-[^'\"]+)['\"]\)", JS))
        # This link is created only after loading an administrator/manager profile.
        self.assertFalse(referenced - set(ids) - {"staff-manager-link"}, referenced - set(ids))
        self.assertTrue(page.xpath('//*[@id="staff-case-download"]'))
        self.assertTrue(page.xpath('//a[@href="/account/memos"]'))
        markup = render_workbench()
        self.assertLess(markup.index('id="eis223-account-polish"'), markup.index('id="staff-compact-layout"'))
        self.assertNotIn("loadToday", JS)
        self.assertIn('credentials:"same-origin"', JS)


if __name__ == "__main__":
    unittest.main()
