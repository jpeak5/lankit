import pytest
from playwright.sync_api import sync_playwright, Browser, Page

DOMAIN = "internal"
SCHEME = "https"


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture
def page(browser: Browser) -> Page:
    """Standard page: cert errors ignored — equivalent to having installed the CA cert."""
    ctx = browser.new_context(ignore_https_errors=True)
    pg = ctx.new_page()
    yield pg
    ctx.close()


@pytest.fixture
def untrusted_page(browser: Browser) -> Page:
    """Strict TLS page: simulates a device that has NOT installed the CA cert."""
    ctx = browser.new_context(ignore_https_errors=False)
    pg = ctx.new_page()
    yield pg
    ctx.close()
