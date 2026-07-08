"""
Unit tests for lankit.core.generator — rendering the RouterOS .rsc
templates from a Config, asserting on the rendered text.

Covers: full renders, lankit: comment tagging, idempotent remove-pass
scoping (global vs segment_filter), client_isolation / force_dns
conditionals, and placeholder passphrase behaviour.
"""
import pytest

from lankit.core.config import load
from lankit.core.generator import generate_all, generate_one

EXPECTED_FILES = [
    "01-vlans.rsc",
    "02-dhcp.rsc",
    "03-firewall.rsc",
    "04-wifi.rsc",
    "05-dns.rsc",
    "06-dns-redirect.rsc",
    "07-bandwidth.rsc",
]


def render(cfg, tmp_path, **kwargs) -> dict[str, str]:
    """generate_all into tmp_path, return {filename: rendered text}."""
    outputs = generate_all(cfg, output_dir=tmp_path, **kwargs)
    return {p.name: p.read_text() for p in outputs}


# ─── Output files ─────────────────────────────────────────────────────────────

def test_all_seven_files_produced(cfg, tmp_path):
    outputs = generate_all(cfg, output_dir=tmp_path)
    assert [p.name for p in outputs] == EXPECTED_FILES
    for p in outputs:
        assert p.exists() and p.stat().st_size > 0
        assert p.parent == tmp_path


# ─── lankit: comment tags per segment ────────────────────────────────────────

def test_lankit_tags_for_every_segment(cfg, tmp_path):
    out = render(cfg, tmp_path)
    for name in cfg.segments:
        assert f'comment="lankit:vlan:{name}:interface"' in out["01-vlans.rsc"]
        assert f'comment="lankit:dhcp:{name}:pool"' in out["02-dhcp.rsc"]
        assert f'comment="lankit:fw:{name}:address-list"' in out["03-firewall.rsc"]
        assert f'comment="lankit:dns:{name}:gateway"' in out["05-dns.rsc"]


def test_segment_values_rendered(cfg, tmp_path):
    out = render(cfg, tmp_path)
    assert "vlan-id=10" in out["01-vlans.rsc"]
    assert "vlan-id=30" in out["01-vlans.rsc"]
    assert "ranges=10.10.0.10-10.10.0.254" in out["02-dhcp.rsc"]
    assert "add address=10.30.0.1/24" in out["02-dhcp.rsc"]
    # household_name template resolved before rendering reaches the SSID
    assert 'configuration.ssid="Testhouse"' in out["04-wifi.rsc"]
    assert 'configuration.ssid="Testhouse IoT"' in out["04-wifi.rsc"]
    # servers has no wifi — no security profile or AP for it
    assert "sec-servers" not in out["04-wifi.rsc"]


# ─── Remove-pass scoping: global mode ────────────────────────────────────────

def test_global_mode_removes_broadly_and_includes_globals(cfg, tmp_path):
    out = render(cfg, tmp_path)
    fw = out["03-firewall.rsc"]

    # Broad remove of everything lankit-managed
    assert 'find where comment~"^lankit:"]' in fw
    # Global-only sections present
    assert 'comment="lankit:nat:all:masquerade"' in fw
    assert 'comment="lankit:fw:all:default-deny"' in fw
    assert 'comment="lankit:fw:all:fasttrack-internet"' in fw
    # VLAN filtering enable is global-only
    assert "vlan-filtering=yes" in out["01-vlans.rsc"]
    assert 'find where comment~"^lankit:vlan:"]' in out["01-vlans.rsc"]


# ─── Remove-pass scoping: segment_filter mode ────────────────────────────────

def test_segment_filter_scopes_removes_and_drops_globals(cfg, tmp_path):
    out = render(cfg, tmp_path, segment_filter="iot")
    fw = out["03-firewall.rsc"]

    # Removes are scoped to the one segment...
    assert 'find where comment~"^lankit:fw:iot:"]' in fw
    assert 'find where comment~"^lankit:fw:iot>"]' in fw
    # ...never the broad global pattern
    assert 'comment~"^lankit:"]' not in fw
    # include_globals=False: NAT, default-deny, fasttrack absent
    # (match rule text, not the file-header comment that mentions them)
    assert "action=masquerade" not in fw
    assert 'comment="lankit:nat:all:masquerade"' not in fw
    assert "lankit:fw:all:default-deny" not in fw
    assert "action=fasttrack-connection" not in fw

    # Only the filtered segment's infrastructure is rendered
    assert "net-iot" in fw
    assert "net-trusted" not in fw
    assert "vlan-trusted" not in out["01-vlans.rsc"]
    # VLAN filtering enable must NOT run in segment mode
    assert "vlan-filtering=yes" not in out["01-vlans.rsc"]
    # DHCP remove-pass scoped too
    assert 'comment~"^lankit:dhcp:iot:"]' in out["02-dhcp.rsc"]
    assert 'comment~"^lankit:dhcp:"]' not in out["02-dhcp.rsc"].replace(
        'comment~"^lankit:dhcp:iot:"]', ""
    )


def test_segment_filter_unknown_segment_raises(cfg, tmp_path):
    with pytest.raises(ValueError, match="ghost"):
        generate_all(cfg, output_dir=tmp_path, segment_filter="ghost")


# ─── Conditional rules per segment flags ─────────────────────────────────────

def test_client_isolation_only_where_enabled(cfg, tmp_path):
    fw = render(cfg, tmp_path)["03-firewall.rsc"]
    assert 'comment="lankit:fw:iot:isolation"' in fw
    assert "lankit:fw:trusted:isolation" not in fw
    assert "lankit:fw:servers:isolation" not in fw


def test_force_dns_redirect_only_where_enabled(cfg, tmp_path):
    redirect = render(cfg, tmp_path)["06-dns-redirect.rsc"]
    assert 'comment="lankit:fw:iot:dns-redirect-udp"' in redirect
    assert 'comment="lankit:fw:iot:dns-redirect-tcp"' in redirect
    assert "to-addresses=10.20.0.2" in redirect
    assert "trusted:dns-redirect" not in redirect
    assert "servers:dns-redirect" not in redirect


def test_force_dns_skipped_without_pihole(no_pihole_yml, tmp_path):
    # iot has force_dns: true but there is no Pi-hole host — redirecting
    # to a public resolver is pointless, so no rules must be generated.
    cfg = load(no_pihole_yml)
    redirect = render(cfg, tmp_path)["06-dns-redirect.rsc"]
    assert "dns-redirect-udp" not in redirect
    assert "dns-redirect-tcp" not in redirect
    assert "add chain=dstnat" not in redirect


def test_dns_permits_skipped_without_pihole(no_pihole_yml, tmp_path):
    cfg = load(no_pihole_yml)
    fw = render(cfg, tmp_path)["03-firewall.rsc"]
    assert ">dns:permit" not in fw


def test_permission_permits_rendered(cfg, tmp_path):
    fw = render(cfg, tmp_path)["03-firewall.rsc"]
    assert 'comment="lankit:fw:trusted>iot:permit"' in fw
    assert 'comment="lankit:fw:trusted>servers:permit"' in fw
    # No reverse permits invented
    assert "lankit:fw:iot>trusted:permit" not in fw


def test_bandwidth_queues_only_for_limited_segments(cfg, tmp_path):
    bw = render(cfg, tmp_path)["07-bandwidth.rsc"]
    assert "lankit:bw:iot:" in bw
    assert "lankit:bw:trusted:" not in bw
    assert "lankit:bw:servers:" not in bw


# ─── Passphrases ─────────────────────────────────────────────────────────────

def test_placeholder_passphrases_when_none_supplied(cfg, tmp_path):
    wifi = render(cfg, tmp_path)["04-wifi.rsc"]
    assert 'passphrase="CHANGEME-trusted"' in wifi
    assert 'passphrase="CHANGEME-iot"' in wifi


def test_supplied_passphrases_used(cfg, tmp_path):
    wifi = render(
        cfg, tmp_path,
        wifi_passphrases={"trusted": "correct-horse", "iot": "battery-staple"},
    )["04-wifi.rsc"]
    assert 'passphrase="correct-horse"' in wifi
    assert 'passphrase="battery-staple"' in wifi
    assert "CHANGEME" not in wifi


# ─── generate_one ────────────────────────────────────────────────────────────

def test_generate_one_appends_j2_suffix(cfg, tmp_path):
    path = generate_one(cfg, "03-firewall.rsc", output_dir=tmp_path)
    assert path.name == "03-firewall.rsc"
    assert 'comment="lankit:fw:iot:isolation"' in path.read_text()
