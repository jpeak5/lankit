"""
Reference test suite for me.internal.

Persona classes subclass this and inherit all tests. They may extend
with persona-specific assertions or override tests to encode a different
expectation (e.g. Seren actively probing the bypass flow).
"""
from playwright.sync_api import Page, expect

URL = "https://me.internal"


class MeInterface:

    def test_page_loads(self, page: Page):
        page.goto(URL)
        expect(page.locator("header .subdomain")).to_contain_text("me.internal")

    def test_stats_card_present(self, page: Page):
        page.goto(URL)
        expect(page.get_by_text("Last 24 hours")).to_be_visible()

    def test_queries_and_blocked_rows_shown(self, page: Page):
        page.goto(URL)
        expect(page.locator(".stat-label", has_text="Queries")).to_be_visible()
        expect(page.locator(".stat-label", has_text="Blocked")).to_be_visible()

    def test_blocked_domains_or_explanation_shown(self, page: Page):
        """Either a domain list or an explanatory message — never silent."""
        page.goto(URL)
        stats_card = page.locator(".card").first
        text = stats_card.inner_text()
        has_domains = page.locator("ul.plain li").count() > 0
        has_message = any(phrase in text for phrase in [
            "No blocked domains",
            "Domain names are hidden",
        ])
        assert has_domains or has_message, (
            "Stats card shows blocked count but neither a domain list "
            "nor an explanation — user has no context for the number"
        )

    def test_ad_blocking_card_present(self, page: Page):
        page.goto(URL)
        # Use .card-title to avoid matching "Ad blocking is globally off" in the body
        expect(page.locator(".card-title", has_text="Ad blocking")).to_be_visible()

    def test_pause_button_or_active_bypass_shown(self, page: Page):
        page.goto(URL)
        blocking = page.locator("#blocking")
        expect(blocking).to_be_visible()
        # Either the pause form or an active-bypass timer must be present
        has_pause_btn = blocking.get_by_text("Pause ad blocking").count() > 0
        has_resume_btn = blocking.get_by_text("Resume now").count() > 0
        assert has_pause_btn or has_resume_btn, (
            "#blocking rendered but contains neither pause nor resume control"
        )

    def test_device_name_card_and_rename_form(self, page: Page):
        page.goto(URL)
        expect(page.get_by_text("Device name")).to_be_visible()
        expect(page.locator("input[name='name']")).to_be_visible()
        expect(page.get_by_role("button", name="Rename")).to_be_visible()

    def test_rename_rejects_invalid_name(self, page: Page):
        page.goto(URL)
        page.fill("input[name='name']", "bad name!")
        page.get_by_role("button", name="Rename").click()
        page.wait_for_selector("#rename-result:not(:empty)", timeout=5000)
        expect(page.locator("#rename-result .msg-error")).to_be_visible()
