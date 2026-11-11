"""The registry inspector adds navigation without widening data access."""
import unittest
from lxml import html

class WorkspacePanelsTests(unittest.TestCase):
    def markup(self, role='user'):
        from scripts.workspace_panels_v1 import workspace_panels
        return html.fromstring('<div>'+workspace_panels(role)+'</div>')

    def test_navigation_reuses_existing_routes_and_role_visibility(self):
        page=self.markup()
        links=page.xpath('//*[@id="eis-shell-nav"]//a/@href')
        self.assertIn('/account/workbench',links)
        self.assertIn('/account/home',links)
        self.assertIn('/account/thematic',links)
        self.assertIn('/account/memos',links)
        self.assertNotIn('/account/manager',links)
        self.assertIn('/account/manager',self.markup('manager').xpath('//a/@href'))
        self.assertIn('/account/admin',self.markup('admin').xpath('//a/@href'))
        self.assertNotIn('/account/admin',self.markup('manager').xpath('//a/@href'))

    def test_inspector_is_labelled_hidden_and_keeps_full_card_escape(self):
        page=self.markup()
        pane=page.get_element_by_id('eis-quicklook')
        self.assertEqual(pane.get('role'),'dialog')
        self.assertIn('hidden',pane.attrib)
        self.assertEqual(pane.get('aria-labelledby'),'eis-quicklook-title')
        self.assertEqual(page.xpath('//*[@data-quicklook-tab]/@data-quicklook-tab'),['summary','findings','documents'])
        self.assertIsNotNone(page.get_element_by_id('eis-quicklook-full'))
        ids=page.xpath('//*[@id]/@id')
        self.assertEqual(len(ids),len(set(ids)))

    def test_no_unapproved_backend_action_is_added(self):
        from scripts.workspace_panels_v1 import SCRIPT
        self.assertNotIn("method:'POST'",SCRIPT)
        self.assertNotIn("method:'DELETE'",SCRIPT)
        self.assertNotIn('innerHTML',SCRIPT)
        self.assertIn('AbortController',SCRIPT)
        self.assertIn('createElement',SCRIPT)
        self.assertIn('credentials:',SCRIPT)

if __name__=='__main__':unittest.main()
