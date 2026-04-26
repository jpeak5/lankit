"""
apps.internal — per-persona UX tests.
"""
import pytest
from playwright.sync_api import Page, expect

from interfaces.apps import AppsInterface
from mixins.first_time_visitor import FirstTimeVisitorMixin

URL = "https://apps.internal"


class TestAppsAsAiko(AppsInterface, FirstTimeVisitorMixin):
    """
    Aiko — apps.internal may be the first page a guest is directed to.
    Inherits the full first-time visitor scenario.
    Key question: can she work out what to do from this page alone?
    """

    def test_page_communicates_purpose_without_prior_context(self, page: Page):
        """
        Aiko has no idea what 'apps.internal' is. The page title and first heading
        must give enough context that she can orient herself.
        """
        page.goto(URL)
        heading = page.locator("header h1").inner_text()
        assert heading.strip(), "Landing page has no heading — Aiko has no orientation"
        # Must not be generic filler
        assert heading.lower() not in ("home", "localhost", "untitled"), \
            f"Heading {heading!r} gives Aiko no context about whose network this is"

    def test_each_portal_card_has_a_description(self, page: Page):
        """
        Each portal link must have a description so Aiko knows what she's clicking into.
        A bare link with no context is not enough.
        """
        page.goto(URL)
        cards = page.locator(".card").all()
        for card in cards:
            desc = card.locator(".desc")
            if card.locator("a[href*='.internal']").count() > 0:
                assert desc.count() > 0, \
                    f"Portal card has a link but no description: {card.inner_text()!r}"

    def test_cert_download_description_explains_why(self, page: Page):
        """
        Aiko won't install a certificate she doesn't understand.
        The download card must explain why — not just say 'download'.
        """
        page.goto(URL)
        cert_card = page.locator(".card", has=page.get_by_text("Download network certificate"))
        if cert_card.count() > 0:
            desc_text = cert_card.locator(".desc").inner_text()
            assert len(desc_text.strip()) > 10, \
                "Cert download card description is too short — Aiko won't know why to install it"
