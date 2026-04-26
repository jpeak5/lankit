"""
Reference test suite for network.internal.
"""
from playwright.sync_api import Page, expect

URL = "https://network.internal"


class NetworkInterface:

    def test_page_loads(self, page: Page):
        page.goto(URL)
        expect(page.locator("header .subdomain")).to_contain_text("network.internal")

    def test_internet_card_present(self, page: Page):
        page.goto(URL)
        expect(page.get_by_text("Internet")).to_be_visible()

    def test_latency_data_or_pending_message(self, page: Page):
        """Latency card shows real data or an explicit 'not yet available' message."""
        page.goto(URL)
        internet_card = page.locator(".card", has=page.get_by_text("Internet"))
        text = internet_card.inner_text()
        has_data = "ms" in text
        has_pending = "not yet available" in text or "no data" in text.lower()
        has_error = "failed" in text.lower()
        assert has_data or has_pending or has_error, \
            "Internet card has no readable latency state — user left without context"

    def test_speed_card_present(self, page: Page):
        page.goto(URL)
        expect(page.get_by_text("Speed test")).to_be_visible()

    def test_run_now_button_present(self, page: Page):
        page.goto(URL)
        expect(page.get_by_role("button", name="Run now")).to_be_visible()

    def test_devices_card_present(self, page: Page):
        page.goto(URL)
        expect(page.get_by_text("Devices")).to_be_visible()

    def test_devices_load_via_htmx(self, page: Page):
        """Device list replaces the 'Loading…' placeholder after HTMX fires."""
        page.goto(URL)
        # Wait for HTMX to replace the placeholder
        page.wait_for_function(
            "() => !document.querySelector('[hx-trigger*=\"load\"]')?.innerText.includes('Loading')",
            timeout=10000,
        )
        devices_card = page.locator(".card", has=page.get_by_text("Devices"))
        text = devices_card.inner_text()
        # Either device rows or explicit 'No devices found' — never a bare spinner
        assert "Loading" not in text, "Device list still shows 'Loading…' after timeout"
        assert "online" in text.lower() or "no devices" in text.lower(), \
            f"Devices card content unrecognisable: {text!r}"
