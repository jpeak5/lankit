"""
FirstTimeVisitorMixin — thorough first-time visitor scenario.

Mix this into any persona class that represents someone encountering
the network for the first time: no CA cert installed, no prior context.

Architecture note — how first-time visitors get the cert in TLS mode
----------------------------------------------------------------------
All HTTP portals redirect to HTTPS (308). A browser that hasn't
installed the CA cert will hit a TLS error on the redirect target.
The cert is NOT discoverable through any browser UI in this state.

The only pre-trust entry points are the direct HTTP cert URLs:
    http://apps.internal/ca.crt
    http://register.internal/ca.crt

These are served by Caddy's `handle /ca.crt` block, which runs before
the redirect. A first-time visitor must be told one of these URLs
out-of-band (QR code, welcome note, etc.).

The 'Trust this network' card on register.internal is only rendered
when request.scheme == 'http'. In TLS mode this never happens, so
the card — and its per-platform installation instructions — is only
meaningful in HTTP-only deployments.

Tests here verify:
  - cert is reachable before trust (HTTP URLs)
  - cert content is structurally valid
  - HTTP→HTTPS redirect is in place
  - HTTPS fails correctly before trust (with untrusted_page fixture)
  - portals work correctly after trust (with standard page fixture)
"""

import os
import re
import subprocess
import tempfile

import pytest
import requests
from playwright.sync_api import Page, expect

_PORTALS = ["me", "network", "register", "apps"]

# Direct Flask address — bypasses Caddy so the HTTP-scheme render is visible.
# Override with LANKIT_APP_SERVER env var if the Pi's address changes.
_APP_SERVER = os.environ.get("LANKIT_APP_SERVER", "10.40.0.3:5000")


class FirstTimeVisitorMixin:

    # ── Cert availability (no browser, no cert trust required) ────────────

    def test_cert_served_over_http_from_apps(self):
        r = requests.get("http://apps.internal/ca.crt", timeout=5, allow_redirects=False)
        assert r.status_code == 200, \
            f"http://apps.internal/ca.crt returned {r.status_code} — " \
            "first-time visitors cannot download the cert"

    def test_cert_served_over_http_from_register(self):
        r = requests.get("http://register.internal/ca.crt", timeout=5, allow_redirects=False)
        assert r.status_code == 200, \
            f"http://register.internal/ca.crt returned {r.status_code}"

    def test_cert_served_over_https_from_register(self, page: Page):
        """Also reachable over HTTPS once cert is trusted (regression: was 404)."""
        r = page.request.get("https://register.internal/ca.crt")
        assert r.ok, f"https://register.internal/ca.crt returned {r.status}"

    # ── Cert content validity ──────────────────────────────────────────────

    def test_cert_is_well_formed_pem(self):
        r = requests.get("http://apps.internal/ca.crt", timeout=5)
        body = r.text
        assert body.strip().startswith("-----BEGIN CERTIFICATE-----"), \
            "ca.crt does not start with a PEM header — may be an error page"
        assert "-----END CERTIFICATE-----" in body, \
            "ca.crt has no PEM footer — file may be truncated"

    def test_cert_is_single_not_a_chain(self):
        r = requests.get("http://apps.internal/ca.crt", timeout=5)
        count = r.text.count("BEGIN CERTIFICATE")
        assert count == 1, \
            f"ca.crt contains {count} certificates — expected exactly 1 (the root CA)"

    def test_cert_is_a_ca_cert(self):
        """ca.crt must be a CA cert (BasicConstraints: CA:TRUE), not the leaf wildcard cert."""
        r = requests.get("http://apps.internal/ca.crt", timeout=5)
        with tempfile.NamedTemporaryFile(suffix=".pem", delete=False, mode="w") as f:
            f.write(r.text)
            tmp = f.name
        try:
            try:
                out = subprocess.check_output(
                    ["openssl", "x509", "-in", tmp, "-text", "-noout"],
                    text=True, stderr=subprocess.DEVNULL,
                )
            except FileNotFoundError:
                pytest.skip("openssl not available — skipping CA cert structure check")
            assert "CA:TRUE" in out, \
                "Downloaded cert is not a CA cert (no CA:TRUE in BasicConstraints) — " \
                "wrong cert served; installing it won't establish trust"
        finally:
            os.unlink(tmp)

    def test_cert_not_expired(self):
        r = requests.get("http://apps.internal/ca.crt", timeout=5)
        with tempfile.NamedTemporaryFile(suffix=".pem", delete=False, mode="w") as f:
            f.write(r.text)
            tmp = f.name
        try:
            try:
                result = subprocess.run(
                    ["openssl", "x509", "-in", tmp, "-checkend", "0"],
                    capture_output=True, text=True,
                )
            except FileNotFoundError:
                pytest.skip("openssl not available — skipping cert expiry check")
            assert result.returncode == 0, \
                "CA cert has expired — new devices will see TLS errors after installing it " \
                "and have no self-service path to recover"
        finally:
            os.unlink(tmp)

    # ── HTTP→HTTPS redirect behaviour ─────────────────────────────────────

    def test_http_portals_redirect_to_https(self):
        """Every HTTP portal must redirect to its HTTPS counterpart."""
        for sub in _PORTALS:
            r = requests.get(f"http://{sub}.internal/", allow_redirects=False, timeout=5)
            assert r.status_code in (301, 302, 307, 308), \
                f"http://{sub}.internal/ returned {r.status_code} — expected a redirect"
            location = r.headers.get("location", "")
            assert location.startswith("https://"), \
                f"http://{sub}.internal/ redirected to {location!r} — expected https://"

    def test_http_cert_path_is_exempt_from_redirect(self):
        """ca.crt must NOT be redirected — it's the bootstrap path for new devices."""
        for sub in ("apps", "register"):
            r = requests.get(f"http://{sub}.internal/ca.crt",
                             allow_redirects=False, timeout=5)
            assert r.status_code == 200, \
                f"http://{sub}.internal/ca.crt was redirected (status {r.status_code}) " \
                "instead of served directly — first-time visitors cannot get the cert"

    # ── TLS enforcement ────────────────────────────────────────────────────

    def test_https_portal_fails_without_cert(self, untrusted_page: Page):
        """
        A browser that hasn't installed the CA cert must get a TLS error.
        If this passes without error, something is wrong with TLS enforcement.
        """
        try:
            untrusted_page.goto("https://me.internal", timeout=10000)
            pytest.fail(
                "Expected a TLS certificate error for an untrusted context, "
                "but the page loaded — is TLS actually enforced?"
            )
        except Exception as e:
            msg = str(e)
            assert any(s in msg for s in ("net::", "ERR_CERT", "certificate", "ssl", "tls")), \
                f"Navigation failed, but not with a cert error: {msg!r}"

    # ── Post-trust behaviour (cert installed, standard page fixture) ───────

    def test_all_portals_load_after_cert_install(self, page: Page):
        """All four portals must be reachable once the CA cert is trusted."""
        for sub in _PORTALS:
            page.goto(f"https://{sub}.internal", timeout=15000)
            assert page.url.startswith("https://"), \
                f"https://{sub}.internal didn't stay on HTTPS: {page.url}"

    def test_register_cert_card_absent_after_cert_install(self, page: Page):
        """
        Once visiting over HTTPS the cert card must be gone.
        If it's still visible, request.scheme detection is broken (ProxyFix issue).
        """
        page.goto("https://register.internal")
        assert page.locator(".card-title", has_text="Trust this network").count() == 0, \
            "Cert card visible on HTTPS — ProxyFix not propagating X-Forwarded-Proto"

    def test_register_shows_device_ip_to_new_visitor(self, page: Page):
        """A first-time visitor must immediately see their device IP — no prior state needed."""
        page.goto("https://register.internal")
        ip_value = page.locator(
            ".stat-row", has=page.locator(".stat-label", has_text="IP address")
        ).locator(".stat-value")
        assert re.match(r"\d+\.\d+\.\d+\.\d+", ip_value.inner_text().strip()), \
            "IP address not shown or malformed on register page"

    def test_register_form_visible_for_unnamed_device(self, page: Page):
        """New visitor without a registered name sees a 'Name this device' prompt."""
        page.goto("https://register.internal")
        # The card title is either "Name this device" (new) or "Change name" (returning)
        card_title = page.locator(".card-title", has_text=re.compile(r"Name this device|Change name"))
        expect(card_title).to_be_visible()
        expect(page.locator("input[name='name']")).to_be_visible()

    # ── Platform instructions (template-level sanity check) ────────────────

    def test_cert_install_instructions_cover_all_platforms(self):
        """
        The register.internal template contains instructions for all major platforms.
        These are only rendered on HTTP (not HTTPS), but the template must include them.
        This test reads the deployed page source via HTTP-level request to verify.
        """
        # Hit Flask directly (port 5000) to get the HTTP-scheme render without Caddy's redirect.
        # This is the only way to see the cert card in a TLS deployment.
        try:
            r = requests.get(f"http://{_APP_SERVER}/", timeout=5,
                             headers={"Host": "register.internal"})
        except requests.exceptions.ConnectionError:
            pytest.skip("Cannot reach Flask directly on port 5000 — skipping template check")

        html = r.text
        required_platforms = ["iOS", "Android", "Mac", "Windows", "Linux"]
        missing = [p for p in required_platforms if p not in html]
        assert not missing, \
            f"Platform install instructions missing for: {missing}"
