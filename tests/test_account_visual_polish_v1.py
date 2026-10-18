"""Presentation-only contracts: no requests, hidden features, or role changes."""
import unittest
from html.parser import HTMLParser

from scripts.account_visual_polish_v1 import (
    account_page_style, home_style, workspace_style,
)
from scripts.account_site_ui_v1 import site_header
from scripts.v361_home_shell import home_shell


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.items = []

    def handle_starttag(self, tag, attrs):
        self.items.append((tag, dict(attrs)))


def contrast(foreground, background):
    def luminance(color):
        channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4
                  for v in channels]
        return sum(a * b for a, b in zip(linear, (.2126, .7152, .0722)))
    values = sorted((luminance(foreground), luminance(background)))
    return (values[1] + .05) / (values[0] + .05)


class VisualPolishTests(unittest.TestCase):
    def test_builders_are_small_css_only_fragments(self):
        for builder in (workspace_style, home_style, account_page_style):
            with self.subTest(builder=builder.__name__):
                fragment = builder()
                parser = Tags()
                parser.feed(fragment)
                self.assertEqual([tag for tag, _ in parser.items], ['style'])
                self.assertLess(len(fragment.encode()), 10000)
                self.assertNotIn('@import', fragment)
                self.assertNotIn('url(', fragment)
                self.assertNotIn('<script', fragment)
                self.assertNotRegex(fragment, r'display\s*:\s*none')
                self.assertNotRegex(fragment, r'visibility\s*:\s*hidden')
                self.assertEqual(fragment.count('{'), fragment.count('}'))

    def test_home_and_account_shell_have_one_local_style_layer(self):
        self.assertEqual(home_shell().count(b'id="eis223-home-polish"'), 1)
        for role in ('user', 'manager', 'admin'):
            markup = site_header(role).decode()
            self.assertEqual(markup.count('id="eis223-workspace-polish"'), 1)
            parser = Tags()
            parser.feed(markup)
            ids = [attrs['id'] for _, attrs in parser.items if 'id' in attrs]
            self.assertEqual(len(ids), len(set(ids)))
            cabinet = next(attrs for _, attrs in parser.items
                           if attrs.get('id') == 'eis223-cabinet')
            self.assertIn('hidden', cabinet)
            self.assertIn('aria-controls="eis223-cabinet"', markup)
            self.assertEqual('/account/manager' in markup, role in ('manager', 'admin'))

    def test_standalone_builders_include_the_layer_once_and_keep_role_gating(self):
        from scripts.account_pages_v1 import HOME, THEMATIC, MEMOS
        from scripts.v370_staff_ui import render_workbench
        from scripts.v368_manager_ui import render_manager
        for markup in (HOME, THEMATIC, MEMOS, render_workbench(),
                       render_manager('manager'), render_manager('admin')):
            self.assertEqual(markup.count('id="eis223-account-polish"'), 1)
            self.assertLess(markup.index('id="eis223-account-polish"'),
                            markup.index('</head>'))
        with self.assertRaises(ValueError):
            render_manager('user')

    def test_narrow_layout_keyboard_motion_and_input_safety(self):
        for fragment in (workspace_style(), account_page_style()):
            self.assertIn(':focus-visible', fragment)
            self.assertIn('prefers-reduced-motion:reduce', fragment)
            self.assertIn('pointer:coarse', fragment)
            self.assertIn('min-height:44px', fragment)
            self.assertIn('max-width:100%', fragment)
        self.assertIn('summary:focus-visible', home_style())
        self.assertIn('input:is([type=checkbox],[type=radio])', home_style())
        self.assertIn('min-height:0!important', home_style())
        self.assertIn('forced-colors:active', account_page_style())
        self.assertIn('max-height:calc(100dvh - 28px)', account_page_style())

    def test_normal_text_and_active_navigation_color_contrast(self):
        pairs = [('#263b4d', '#f3f6f9'), ('#536b7d', '#ffffff'),
                 ('#536b7d', '#f3f6f9'), ('#ffffff', '#18384f'),
                 ('#ffffff', '#175d8d'), ('#ffffff', '#28678f'),
                 ('#174f78', '#e7f0f7'), ('#a34043', '#ffffff')]
        for foreground, background in pairs:
            with self.subTest(foreground=foreground, background=background):
                self.assertGreaterEqual(contrast(foreground, background), 4.5)


if __name__ == '__main__':
    unittest.main()
