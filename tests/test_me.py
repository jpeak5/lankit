"""
me.internal — per-persona UX tests.

Each class inherits the full MeInterface contract and adds
scenarios specific to how that persona encounters the portal.
"""
import pytest
from playwright.sync_api import Page, expect

from conftest import SCREENSHOTS_DIR
from interfaces.me import MeInterface
from mixins.first_time_visitor import FirstTimeVisitorMixin

URL = "https://me.internal"


class TestMeAsKwame(MeInterface):
    """
    Kwame — work-from-home partner, heavy passive user.
    Visits me.internal when something loads slowly or ads reappear.
    Key question: can he tell whether the network is the problem?
    """

    def test_stats_give_enough_context_to_self_triage(self, page: Page):
        """
        Kwame needs more than a raw number — the page must show both
        the query count and the blocked percentage so he can judge
        whether 'something is wrong' without calling Priya.
        """
        page.goto(URL)
        blocked_row = page.locator(".stat-row", has=page.locator(".stat-label", has_text="Blocked"))
        value = blocked_row.locator(".stat-value").inner_text()
        assert "%" in value, \
            "Blocked stat must show a percentage alongside the count " \
            "so Kwame can interpret it without network knowledge"

    def test_bypass_duration_options_visible(self, page: Page):
        """Kwame pausing before a client presentation needs time options, not just an on/off."""
        page.goto(URL)
        blocking = page.locator("#blocking")
        # Only relevant when no bypass is active
        if blocking.get_by_text("Pause ad blocking").count() > 0:
            expect(blocking.locator("select[name='duration']")).to_be_visible()

    def test_bypass_controls_visual(self, page: Page):
        """
        Screenshot of the ad-blocking card. Captures button colour and form layout.
        The countdown timer is masked when a bypass is active (live seconds).
        Saved to docs/screenshots/ as living documentation.
        """
        page.goto(URL)
        page.wait_for_selector("#blocking:not(:empty)", timeout=10000)
        card = page.locator(".card", has=page.locator("#blocking"))
        timer = page.locator("#blocking .msg").filter(has_text="remaining")
        mask = [timer] if timer.count() > 0 else []
        card.screenshot(path=SCREENSHOTS_DIR / "me-blocking-card.png", mask=mask)

    def test_stats_card_visual(self, page: Page):
        """
        Screenshot of the stats card. Query counts and percentages are masked
        (change with real traffic). Blocked domain names are also masked —
        they are browsing history and must not appear in committed screenshots.
        Saved to docs/screenshots/ as living documentation.
        """
        page.goto(URL)
        stats_card = page.locator(".card", has=page.get_by_text("Last 24 hours"))
        stats_card.screenshot(
            path=SCREENSHOTS_DIR / "me-stats-card.png",
            mask=[
                stats_card.locator(".stat-value"),
                stats_card.locator("ul.plain li"),  # blocked domain names = browsing history
            ],
        )


class TestMeAsSeren(MeInterface):
    """
    Seren — Dale's 14-year-old, curious and persistent.
    Notices blocking, finds the bypass flow, tests limits.
    """

    def test_bypass_form_is_accessible(self, page: Page):
        """Seren will find the bypass. The test confirms she can — by design, not accident."""
        page.goto(URL)
        blocking = page.locator("#blocking")
        # Seren can reach the pause button without any prior context
        assert blocking.get_by_text("Pause ad blocking").count() > 0 \
               or blocking.get_by_text("Resume now").count() > 0, \
               "Bypass control not found — Seren cannot exercise (or circumvent) it"

    def test_rename_form_accepts_valid_hostname(self, page: Page):
        """Seren renames her phone. Valid hostname-style names must be accepted."""
        page.goto(URL)
        current = page.locator("input[name='name']").input_value()
        # Use a clearly test-named value to avoid polluting real state
        page.fill("input[name='name']", "seren-test")
        page.get_by_role("button", name="Rename").click()
        page.wait_for_selector("#rename-result:not(:empty)", timeout=5000)
        result_text = page.locator("#rename-result").inner_text()
        assert "error" not in result_text.lower(), \
            f"Valid name 'seren-test' was rejected: {result_text}"
        # Restore original name to avoid test side-effects on state
        if current:
            page.fill("input[name='name']", current)
            page.get_by_role("button", name="Rename").click()
            page.wait_for_selector("#rename-result:not(:empty)", timeout=5000)


class TestMeAsAiko(MeInterface, FirstTimeVisitorMixin):
    """
    Aiko — overnight guest, arriving at me.internal without context.
    Inherits the full first-time visitor scenario on top of me.internal tests.
    """

    def test_page_is_readable_without_network_knowledge(self, page: Page):
        """
        Aiko has no idea what Pi-hole is. The page must not expose jargon
        that requires prior knowledge to interpret.
        """
        page.goto(URL)
        body = page.locator("body").inner_text()
        jargon = ["Pi-hole", "VLAN", "DHCP", "upstream", "FTL"]
        found = [term for term in jargon if term in body]
        assert not found, \
            f"Technical jargon visible to household members on me.internal: {found}"


class TestMeAsClem(MeInterface):
    """
    Clem — Yemi's partner, competent but non-technical first responder.
    May visit me.internal when an unexpected device appears on the network.
    """

    def test_hostname_shown_in_header(self, page: Page):
        """
        Clem needs to know whose device she's looking at.
        If a hostname is registered, it must be shown prominently in the header.
        """
        page.goto(URL)
        header_text = page.locator("header h1").inner_text()
        # header shows hostname OR IP — either is identifiable
        assert header_text.strip(), "Header h1 is empty — device has no identity on this page"

    def test_ad_blocking_card_has_no_irreversible_actions(self, page: Page):
        """
        Clem can pause ad blocking but the UI must not offer anything
        that can't be undone (e.g., deleting DNS records).
        """
        page.goto(URL)
        blocking = page.locator("#blocking")
        text = blocking.inner_text()
        destructive = ["delete", "remove", "reset", "wipe"]
        found = [w for w in destructive if w in text.lower()]
        assert not found, \
            f"Potentially destructive action visible in blocking card: {found}"
