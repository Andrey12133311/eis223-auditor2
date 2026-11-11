"""Accessible navigation contracts for separate cabinet and registry views."""
import unittest
from lxml import html
from scripts.workspace_panels_v1 import workspace_panels

class WorkspaceClarityTests(unittest.TestCase):
    def test_registry_and_cabinet_are_explicit_navigation_targets(self):
        page=html.fromstring('<div>'+workspace_panels()+'</div>')
        registry=page.get_element_by_id('eis-shell-registry')
        self.assertEqual(registry.get('href'),'/')
        self.assertEqual(registry.get('aria-current'),'page')
        cabinet=page.get_element_by_id('eis-shell-overview')
        self.assertEqual(cabinet.get('aria-controls'),'eis223-cabinet')
        self.assertEqual(cabinet.get('aria-pressed'),'false')
        self.assertEqual(cabinet.get('aria-label'),'Мой кабинет')

    def test_tasks_entry_uses_plain_language_and_existing_route(self):
        page=html.fromstring('<div>'+workspace_panels('manager')+'</div>')
        link=page.xpath('//a[@href="/account/workbench"]')[0]
        self.assertEqual(link.get('aria-label'),'Задачи и поручения')
        self.assertEqual(link.text_content().strip(),'Задачи и поручения')

if __name__=='__main__':unittest.main()
