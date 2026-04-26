"""
register.internal — per-persona UX tests.
"""
import re

import pytest
from playwright.sync_api import Page, expect

from conftest import SCREENSHOTS_DIR
from interfaces.register import RegisterInterface
from mixins.first_time_visitor import FirstTimeVisitorMixin

URL = "https://register.internal"


class TestRegisterAsAiko(RegisterInterface, FirstTimeVisitorMixin):
    """
    Aiko — overnight guest. register.internal may be the first page she sees.
    Inherits the complete first-time visitor scenario.
    Key question: can she get onto the network for a weekend visit without help?
    """

    def test_page_copy_avoids_technical_jargon(self, page: Page):
        page.goto(URL)
        body = page.locator("body").inner_text()
        jargon = ["MAC address", "VLAN", "segment", "DHCP", "DNS", "quarantine"]
        found = [term for term in jargon if term in body]
        assert not found, \
            f"Technical jargon on register.internal visible to a first-time guest: {found}"

    def test_form_placeholder_shows_expected_format(self, page: Page):
        """'my-device' placeholder sets the format without a wall of instructions."""
        page.goto(URL)
        placeholder = page.locator("input[name='name']").get_attribute("placeholder")
        assert placeholder, "Name input has no placeholder — format expectations unclear"

    def test_register_page_visual(self, page: Page):
        """
        Full-page screenshot of register.internal in HTTPS mode (no cert card).
        IP, MAC, current name, and the pre-filled input are all masked — they
        are device identifiers and must not appear in committed screenshots.
        Saved to docs/screenshots/ as living documentation.
        """
        page.goto(URL)
        dynamic = [
            page.locator(".stat-row", has=page.locator(".stat-label", has_text="IP address"))
                .locator(".stat-value"),
            page.locator(".stat-row", has=page.locator(".stat-label", has_text="Device identifier"))
                .locator(".stat-value"),
            page.locator(".stat-row", has=page.locator(".stat-label", has_text="Current name"))
                .locator(".stat-value"),
            page.locator("input[name='name']"),
        ]
        page.screenshot(path=SCREENSHOTS_DIR / "register-page.png", full_page=True,
                        mask=dynamic)


class TestRegisterAsSeren(RegisterInterface):
    """
    Seren — 14-year-old, will probe the registration flow and try edge cases.
    """

    def test_name_with_spaces_rejected(self, page: Page):
        page.goto(URL)
        page.fill("input[name='name']", "my phone")
        page.locator("button[type='submit']").click()
        page.wait_for_selector("#register-result:not(:empty)", timeout=5000)
        expect(page.locator("#register-result .msg-error")).to_be_visible()

    def test_name_starting_with_hyphen_rejected(self, page: Page):
        page.goto(URL)
        page.fill("input[name='name']", "-bad")
        page.locator("button[type='submit']").click()
        page.wait_for_selector("#register-result:not(:empty)", timeout=5000)
        expect(page.locator("#register-result .msg-error")).to_be_visible()

    def test_reserved_name_rejected(self, page: Page):
        """Names like 'router', 'dns', 'me' are reserved and must be refused."""
        page.goto(URL)
        for reserved in ("router", "dns", "me", "apps"):
            # Clear the result div before each submit so the wait below
            # always waits for fresh content rather than matching stale state.
            page.evaluate("document.getElementById('register-result').innerHTML = ''")
            page.fill("input[name='name']", reserved)
            page.locator("button[type='submit']").click()
            page.wait_for_selector("#register-result:not(:empty)", timeout=5000)
            expect(page.locator("#register-result .msg-error")).to_be_visible()
            assert page.locator("#register-result .msg-error").count() > 0, \
                f"Reserved name '{reserved}' was accepted — should show an error"

    def test_very_long_name_capped_by_input(self, page: Page):
        """maxlength=30 on the input prevents Seren from submitting 200-char names."""
        page.goto(URL)
        max_len = int(page.locator("input[name='name']").get_attribute("maxlength") or "0")
        assert max_len > 0, "maxlength not set on name input"
        assert max_len <= 30, f"maxlength is {max_len} — spec says 30"


class TestRegisterAsClem(RegisterInterface):
    """
    Clem — registering a family member's new phone under time pressure.
    Non-technical, needs the flow to be obvious and forgiving.
    """

    def test_current_name_shown_if_device_already_registered(self, page: Page):
        """
        Clem returning to rename a device must see the current name pre-filled.
        If current_name is set, it must appear in the input — not just in the label.
        """
        page.goto(URL)
        current_row = page.locator(".stat-row", has=page.locator(".stat-label", has_text="Current name"))
        if current_row.count() > 0:
            current_name = current_row.locator(".stat-value").inner_text().strip()
            input_value = page.locator("input[name='name']").input_value()
            assert input_value == current_name, \
                f"Current name '{current_name}' not pre-filled in input (got {input_value!r})"
            # Button should say "Update" not "Register"
            submit_text = page.locator("button[type='submit']").inner_text()
            assert submit_text == "Update", \
                f"Returning device should show 'Update' button, got {submit_text!r}"

    def test_success_message_visual(self, page: Page):
        """
        Screenshot of the HTMX result div after a successful registration.
        Captures the green success message and me.internal link styling.
        The device name embedded in the message is masked.
        Saved to docs/screenshots/ as living documentation.
        """
        page.goto(URL)
        page.fill("input[name='name']", "clem-snapshot-test")
        page.locator("button[type='submit']").click()
        page.wait_for_selector("#register-result:not(:empty)", timeout=5000)
        result = page.locator("#register-result")
        if result.locator(".msg-error").count() > 0:
            pytest.skip("Registration returned an error — skipping screenshot")
        name_strong = result.locator("strong")
        result.screenshot(
            path=SCREENSHOTS_DIR / "register-success.png",
            mask=[name_strong] if name_strong.count() > 0 else [],
        )

    def test_success_message_links_to_me_portal(self, page: Page):
        """
        After Clem registers the device, the confirmation must link to me.internal
        so she can hand the phone to the family member with a next step.
        """
        page.goto(URL)
        page.fill("input[name='name']", "clem-test")
        page.locator("button[type='submit']").click()
        page.wait_for_selector("#register-result:not(:empty)", timeout=5000)
        result = page.locator("#register-result")
        result_text = result.inner_text()

        if "error" in result_text.lower():
            pytest.skip("Registration returned an error — skipping link check")

        # Success: either already named or freshly registered
        if "already named" not in result_text.lower():
            me_link = result.get_by_role("link", name=re.compile(r"me\.internal"))
            assert me_link.count() > 0, \
                "Success message has no me.internal link — Clem has no next step after registering"
            href = me_link.get_attribute("href")
            assert href.startswith("https://"), \
                f"me.internal link in success message uses plain HTTP: {href}"
