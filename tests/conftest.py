"""
Pytest fixtures for the lankit portal UX test suite.

Fixture topology
----------------
pytest-playwright provides: browser (session-scoped), context and page
(function-scoped). We override browser_context_args so the standard
`page` fixture ignores TLS errors — simulating a device that has
installed the CA cert and trusts all .internal addresses.

The `untrusted_page` fixture creates a strict-TLS context for the one
test that verifies enforcement. See its docstring for a caveat about
running from a machine that already has the CA cert installed.
"""
from pathlib import Path
import pytest
from playwright.sync_api import Browser, Page

SCREENSHOTS_DIR = Path(__file__).parent.parent / "docs" / "screenshots"


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    return {**browser_type_launch_args, "headless": True}


@pytest.fixture
def browser_context_args(browser_context_args):
    return {**browser_context_args, "ignore_https_errors": True}


@pytest.fixture
def untrusted_page(browser: Browser) -> Page:
    """Strict TLS — simulates a device that has NOT installed the CA cert.

    Caveat: if the machine running the tests has the CA cert installed in
    its system trust store, TLS enforcement tests will produce a false pass
    (the cert is trusted, so no error fires). Run those tests from a machine
    or container that has not installed the cert.
    """
    ctx = browser.new_context(ignore_https_errors=False)
    pg = ctx.new_page()
    yield pg
    ctx.close()


@pytest.fixture(scope="session", autouse=True)
def screenshots_dir() -> Path:
    """Ensure docs/screenshots/ exists before any test runs."""
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    return SCREENSHOTS_DIR
