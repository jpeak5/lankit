"""
Reference test suite for register.internal.
"""
import re
from playwright.sync_api import Page, expect

URL = "https://register.internal"


class RegisterInterface:

    def test_page_loads(self, page: Page):
        page.goto(URL)
        expect(page.locator("header h1")).to_contain_text("Register this device")
        expect(page.locator("header .subdomain")).to_contain_text("register.internal")

    def test_device_ip_shown(self, page: Page):
        page.goto(URL)
        expect(page.locator(".stat-label", has_text="IP address")).to_be_visible()
        ip_value = page.locator(".stat-row", has=page.locator(".stat-label", has_text="IP address")) \
                       .locator(".stat-value")
        assert re.match(r"\d+\.\d+\.\d+\.\d+", ip_value.inner_text().strip()), \
            f"IP address value looks wrong: {ip_value.inner_text()!r}"

    def test_device_identifier_row_present(self, page: Page):
        """MAC shown as 'Device identifier' — not raw 'MAC address' per copy spec."""
        page.goto(URL)
        # MAC may be absent if Pi-hole can't resolve it; test the label copy if present
        labels = page.locator(".stat-label").all_inner_texts()
        if "Device identifier" in labels:
            expect(page.locator(".stat-label", has_text="Device identifier")).to_be_visible()
        if "MAC address" in labels:
            raise AssertionError(
                "Label says 'MAC address' — spec requires 'Device identifier' "
                "to avoid jargon for household members"
            )

    def test_name_form_present(self, page: Page):
        page.goto(URL)
        expect(page.locator("input[name='name']")).to_be_visible()
        submit = page.locator("button[type='submit']")
        expect(submit).to_be_visible()
        assert submit.inner_text() in ("Register", "Update"), \
            f"Submit button text unexpected: {submit.inner_text()!r}"

    def test_hint_text_present(self, page: Page):
        """'Letters, numbers, and hyphens only.' — sets expectations before the user types."""
        page.goto(URL)
        expect(page.get_by_text("Letters, numbers, and hyphens only.")).to_be_visible()

    def test_cert_card_absent_over_https(self, page: Page):
        """
        The 'Trust this network' cert card is only rendered for HTTP requests.
        Over HTTPS (X-Forwarded-Proto: https → ProxyFix → request.scheme == 'https')
        it must be absent — if it's visible, ProxyFix isn't working.
        """
        page.goto(URL)
        assert page.locator(".card-title", has_text="Trust this network").count() == 0, \
            "Cert card visible over HTTPS — ProxyFix may not be propagating X-Forwarded-Proto"

    def test_invalid_name_rejected(self, page: Page):
        page.goto(URL)
        page.fill("input[name='name']", "bad name!")
        page.locator("button[type='submit']").click()
        page.wait_for_selector("#register-result:not(:empty)", timeout=5000)
        expect(page.locator("#register-result .msg-error")).to_be_visible()

    def test_empty_name_rejected(self, page: Page):
        page.goto(URL)
        page.fill("input[name='name']", "")
        page.locator("button[type='submit']").click()
        page.wait_for_selector("#register-result:not(:empty)", timeout=5000)
        expect(page.locator("#register-result .msg-error")).to_be_visible()
