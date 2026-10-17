"""Read-only EIS-inspired design regression tests. No user data is modified."""
from html.parser import HTMLParser

from scripts.account_site_ui_v1 import site_header, login_portal
from scripts.account_pages_v1 import HOME, THEMATIC, MEMOS
from scripts.v361_home_shell import home_shell


class AccountMarkup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.cabinet_hidden = False
        self.panels = 0
        self.toggle_target = None

    def handle_starttag(self, tag, attrs):
        props = dict(attrs)
        if "id" in props:
            self.ids.append(props["id"])
        if props.get("id") == "eis223-cabinet":
            self.cabinet_hidden = "hidden" in props
        if props.get("id") == "eis223-toggle-cabinet":
            self.toggle_target = props.get("aria-controls")
        if "eis223-tile" in props.get("class", "").split():
            self.panels += 1


def run():
    html = site_header().decode("utf-8")
    parser = AccountMarkup()
    parser.feed(html)
    assert len(parser.ids) == len(set(parser.ids)), "Duplicate ids in account shell"
    assert parser.cabinet_hidden and parser.toggle_target == "eis223-cabinet"
    assert parser.panels == 3
    assert 'id="eis223-my-procurements"' in html
    assert 'id="eis223-my-batches"' in html
    assert 'id="eis223-my-memos"' in html
    assert "const counters=[" in html
    assert "get('/me/summary')" in html
    assert "get('/api/thematic/history')" in html
    assert "get('/me/memos')" in html
    assert "eis223-new-batch" not in html
    assert "eis223-hero" not in html
    assert "Независимая аналитическая система" in html
    assert "#174c76" in html
    assert "<span class=\"eis223-brand-icon\"" in html

    source = home_shell().decode("utf-8")
    assert 'id="eis361-home-style"' in source
    assert 'id="eis361-home-script"' in source
    assert "#174c76" not in source or "var(--eis-blue)" in source
    assert "eis361-hero" in source  # safe removal of any stale banner
    assert "insertBefore(hero" not in source
    assert "Независимый аналитический сервис" in source
    assert "Источник: ЕИС" in source
    assert ".stats" in source and "table th" in source and ".filters" in source

    for page in (HOME, THEMATIC, MEMOS):
        assert 'id="eis223-pages-modern"' in page
        assert "ЕИС Аудитор" in page
        assert "#174c76" in page
    for tab in ("login", "register"):
        login = login_portal(tab)
        assert 'id="eis223-signin-style"' in login
        assert 'id="login-password"' in login
        assert "fetch('/auth/me'" not in login
    print("V364_EIS_DESIGN_SELFTEST_OK reference=EIS-inspired not_official=True "
          "real_account_metrics=True three_sections=True no_hero=True "
          "login_form=True no_data_mutation=True", flush=True)


if __name__ == "__main__":
    run()
