"""
Reference test suite for apps.internal (static landing page).
"""
import re
from playwright.sync_api import Page, expect

URL = "https://apps.internal"


class AppsInterface:

    def test_page_loads(self, page: Page):
        page.goto(URL)
        # apps.internal is a static Jinja2-rendered page served by Caddy
        title = page.title()
        assert "network" in title.lower() or "internal" in title.lower(), \
            f"Unexpected page title: {title!r}"

    def test_me_link_present(self, page: Page):
        page.goto(URL)
        expect(page.get_by_role("link", name=re.compile(r"me\.internal"))).to_be_visible()

    def test_network_link_present(self, page: Page):
        page.goto(URL)
        expect(page.get_by_role("link", name=re.compile(r"network\.internal"))).to_be_visible()

    def test_register_link_present(self, page: Page):
        page.goto(URL)
        expect(page.get_by_role("link", name=re.compile(r"register\.internal"))).to_be_visible()

    def test_cert_download_card_present(self, page: Page):
        """CA cert download is offered from the apps landing page when TLS is enabled."""
        page.goto(URL)
        expect(page.get_by_text("Download network certificate")).to_be_visible()

    def test_all_portal_links_go_to_https(self, page: Page):
        page.goto(URL)
        for link in page.locator("a[href*='.internal']").all():
            href = link.get_attribute("href") or ""
            assert href.startswith("https://"), \
                f"Portal link uses plain HTTP in TLS mode: {href}"
