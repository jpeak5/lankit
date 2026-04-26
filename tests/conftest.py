from pathlib import Path
import pytest
from playwright.sync_api import Browser, Page

# pytest-playwright provides: browser (session), context (function), page (function)
# We override browser_context_args so the standard `page` fixture ignores cert errors,
# simulating a device that has installed the CA cert.

SCREENSHOTS_DIR = Path(__file__).parent.parent / "docs" / "screenshots"


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    return {**browser_type_launch_args, "headless": True}


@pytest.fixture
def browser_context_args(browser_context_args):
    return {**browser_context_args, "ignore_https_errors": True}


@pytest.fixture
def untrusted_page(browser: Browser) -> Page:
    """Strict TLS — simulates a device that has NOT installed the CA cert."""
    ctx = browser.new_context(ignore_https_errors=False)
    pg = ctx.new_page()
    yield pg
    ctx.close()


@pytest.fixture(scope="session", autouse=True)
def screenshots_dir() -> Path:
    """Ensure docs/screenshots/ exists before any test runs."""
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    return SCREENSHOTS_DIR
