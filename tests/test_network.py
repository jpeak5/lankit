"""
network.internal — per-persona UX tests.
"""
import re
import pytest
from playwright.sync_api import Page, expect

from conftest import SCREENSHOTS_DIR
from interfaces.network import NetworkInterface

URL = "https://network.internal"


class TestNetworkAsKwame(NetworkInterface):
    """
    Kwame — visits network.internal when he suspects 'the internet is down'.
    Key question: can he determine on his own whether the network is the problem?
    """

    def test_latency_card_gives_a_clear_verdict(self, page: Page):
        """
        Kwame needs a readable signal, not raw numbers.
        The card must show either real ms values or an explicit 'not yet available' —
        never an empty card that looks like data but says nothing.
        """
        page.goto(URL)
        internet_card = page.locator(".card", has=page.get_by_text("Internet"))
        text = internet_card.inner_text()
        assert text.strip() not in ("", "Internet"), \
            "Internet card appears empty — Kwame has no way to know if the network is up"

    def test_latency_targets_are_labelled(self, page: Page):
        """Both 1.1.1.1 and 8.8.8.8 should be labelled so Kwame isn't looking at mystery IPs."""
        page.goto(URL)
        text = page.locator(".card", has=page.get_by_text("Internet")).inner_text()
        if "ms" in text:  # latency data present
            assert "1.1.1.1" in text and "8.8.8.8" in text, \
                "Latency targets unlabelled — Kwame sees numbers with no context"

    def test_speedtest_button_present_and_one_click(self, page: Page):
        """Kwame checking 'is my connection slow?' must find the speed test in one click."""
        page.goto(URL)
        expect(page.get_by_role("button", name="Run now")).to_be_visible()

    def test_devices_list_shows_online_count(self, page: Page):
        """Kwame looking for 'is it just me?' needs an online device count."""
        page.goto(URL)
        page.wait_for_function(
            "() => !document.querySelector('[hx-trigger*=\"load\"]')?.innerText.includes('Loading')",
            timeout=10000,
        )
        devices_text = page.locator(".card", has=page.get_by_text("Devices")).inner_text()
        if "no devices" not in devices_text.lower():
            assert "online" in devices_text.lower(), \
                "Device list loaded but doesn't show an online count"


class TestNetworkAsClem(NetworkInterface):
    """
    Clem — may visit network.internal when she sees an unfamiliar device
    on the network. Needs to assess whether it's a threat.
    """

    def test_each_device_entry_shows_hostname_and_ip(self, page: Page):
        """
        Clem needs enough info to identify a device (is 'unknown-a3f2' the thermostat?).
        Each entry must show at least a hostname and IP.
        """
        page.goto(URL)
        page.wait_for_function(
            "() => !document.querySelector('[hx-trigger*=\"load\"]')?.innerText.includes('Loading')",
            timeout=10000,
        )
        device_items = page.locator("ul.plain li").all()
        for item in device_items:
            text = item.inner_text()
            has_ip = bool(re.search(r"\d+\.\d+\.\d+\.\d+", text))
            has_name = len(text.strip()) > 0
            assert has_name and has_ip, \
                f"Device entry doesn't show enough info for Clem to identify it: {text!r}"

    def test_device_list_visual(self, page: Page):
        """
        Screenshot of the devices card after HTMX has loaded.
        Device names, IPs, and the online/offline summary are masked (change between
        runs) — the image documents the flex layout and status-dot colours.
        Saved to docs/screenshots/ as living documentation.
        """
        page.goto(URL)
        page.wait_for_function(
            "() => !document.querySelector('[hx-trigger*=\"load\"]')?.innerText.includes('Loading')",
            timeout=10000,
        )
        card = page.locator(".card", has=page.get_by_text("Devices"))
        hostnames = card.locator("ul.plain li > span:first-child")
        ips = card.locator("ul.plain li .muted")
        summary = card.locator("p.muted")  # "N online, M offline" — counts change
        card.screenshot(
            path=SCREENSHOTS_DIR / "network-devices-card.png",
            mask=[hostnames, ips, summary],
        )

    def test_online_offline_status_visually_distinguishable(self, page: Page):
        """
        Clem needs to quickly spot which devices are active.
        Online/offline must be distinguishable — not just a text label buried in a row.
        """
        page.goto(URL)
        page.wait_for_function(
            "() => !document.querySelector('[hx-trigger*=\"load\"]')?.innerText.includes('Loading')",
            timeout=10000,
        )
        # The template uses colored inline spans as status dots
        # Check that the status indicator elements are present in the DOM
        dots = page.locator("ul.plain li span[style*='border-radius']").all()
        if page.locator("ul.plain li").count() > 0:
            assert len(dots) > 0, \
                "Device list has entries but no visual status indicators (colored dots)"
