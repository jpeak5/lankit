"""
Unit tests for lankit.core.generator — rendering the RouterOS .rsc
templates from a Config, asserting on the rendered text.

Covers: full renders, lankit: comment tagging, idempotent remove-pass
scoping (global vs segment_filter), client_isolation / force_dns
conditionals, and placeholder passphrase behaviour.
"""
import pytest
from pathlib import Path

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


# ─── Scripted defconf-fasttrack disable (G3.7) ───────────────────────────────

def test_global_mode_scripts_defconf_fasttrack_disable(cfg, tmp_path):
    fw = render(cfg, tmp_path)["03-firewall.rsc"]
    # Find-guarded, no-op-safe disable of the defconf blanket rule
    assert 'disable [find where comment="defconf: fasttrack" && disabled=no]' in fw
    # The disable must precede the scoped replacement rule
    assert fw.index('comment="defconf: fasttrack"') < fw.index("action=fasttrack-connection")
    # The manual step is gone (S6)
    assert "MANUAL STEP" not in fw
    # Binding constraint (tag-scoped-revert.md §5.3/§11): the defconf rule
    # is never tagged lankit: — the disable line must not touch its comment.
    disable_line = next(
        line for line in fw.splitlines() if line.startswith("disable [find")
    )
    assert "lankit:" not in disable_line
    assert "comment=" not in disable_line.replace('where comment="defconf: fasttrack"', "")


def test_segment_filter_omits_defconf_fasttrack_disable(cfg, tmp_path):
    fw = render(cfg, tmp_path, segment_filter="iot")["03-firewall.rsc"]
    assert 'comment="defconf: fasttrack"' not in fw


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


# ─── Multi-segment segment_filter (G1.2 — known-good archive re-render) ─────

def test_multi_segment_filter_requires_explicit_include_globals(cfg, tmp_path):
    with pytest.raises(ValueError, match="include_globals"):
        generate_all(cfg, output_dir=tmp_path, segment_filter=["trusted", "iot"])


def test_multi_segment_filter_with_globals_true(cfg, tmp_path):
    out = render(cfg, tmp_path, segment_filter={"trusted", "iot"}, include_globals=True)
    fw = out["03-firewall.rsc"]
    assert "net-trusted" in fw
    assert "net-iot" in fw
    # Cross-segment permits are NOT restricted to the filtered subset —
    # trusted's can_reach still names servers even though servers isn't
    # part of this render (matches the existing --segment X behavior:
    # forward references to not-yet-applied segments are intentional, see
    # generator._build_context's comment). A caller needing a fully
    # self-contained render must pre-filter permissions itself.
    assert 'dst-address-list=net-servers' in fw
    assert "add list=net-servers" not in fw  # but its own address-list isn't (re)created here
    # include_globals=True even though only a subset of segments is present
    assert 'comment="lankit:fw:all:default-deny"' in fw
    assert "vlan-filtering=yes" in out["01-vlans.rsc"]


def test_multi_segment_filter_with_globals_false(cfg, tmp_path):
    out = render(cfg, tmp_path, segment_filter=["iot", "servers"], include_globals=False)
    fw = out["03-firewall.rsc"]
    assert "net-iot" in fw
    assert "net-servers" in fw
    assert "net-trusted" not in fw
    assert "lankit:fw:all:default-deny" not in fw
    assert "vlan-filtering=yes" not in out["01-vlans.rsc"]


def test_single_string_segment_filter_still_defaults_include_globals_false(cfg, tmp_path):
    # Unchanged behavior for the existing single-segment call sites (apply.py
    # --segment NAME) — passing a bare str still needs no include_globals arg.
    out = render(cfg, tmp_path, segment_filter="iot")
    assert "vlan-filtering=yes" not in out["01-vlans.rsc"]


def test_render_texts_matches_generate_all_output(cfg):
    from lankit.core.generator import render_texts
    texts = render_texts(cfg)
    assert set(texts) == set(EXPECTED_FILES)
    assert 'comment="lankit:vlan:trusted:interface"' in texts["01-vlans.rsc"]


# ── #62 / #63 ────────────────────────────────────────────────────────────────

def test_no_hw_offload_parameter_anywhere(cfg, tmp_path):
    for name, text in render(cfg, tmp_path).items():
        assert "hw-offload" not in "\n".join(
            l for l in text.splitlines() if not l.lstrip().startswith("#")), name


def _wifi_masters(text):
    return [l.split()[1] for l in text.splitlines() if l.startswith("set wifi")]


def test_master_radios_set_for_every_band_in_use(cfg, tmp_path):
    # the minimal fixture uses both bands
    assert _wifi_masters(render(cfg, tmp_path)["04-wifi.rsc"]) == ["wifi1", "wifi2"]


def test_no_master_radio_block_when_no_segment_uses_wifi(minimal_yml, mutated, tmp_path):
    from lankit.core.config import load

    def wired_only(raw):
        for seg in raw["segments"].values():
            seg["wifi_bands"], seg["ssid"] = [], None
    text = render(load(mutated(minimal_yml, wired_only)), tmp_path)["04-wifi.rsc"]
    assert _wifi_masters(text) == []
    assert "/interface wifi\n" not in text


def test_only_the_used_band_master_is_set(minimal_yml, mutated, tmp_path):
    from lankit.core.config import load

    def two_gig_only(raw):
        for seg in raw["segments"].values():
            if seg["wifi_bands"]:
                seg["wifi_bands"] = ["2ghz"]
    text = render(load(mutated(minimal_yml, two_gig_only)), tmp_path)["04-wifi.rsc"]
    assert _wifi_masters(text) == ["wifi2"]


# ── output location and permissions ──────────────────────────────────────────

def test_generated_dir_is_beside_the_site_config_not_the_tool(minimal_yml, mutated):
    from lankit.core.config import load
    from lankit.core.generator import generated_dir
    site_yml = mutated(minimal_yml, lambda raw: None)        # a copy in tmp_path
    cfg = load(site_yml)
    assert generated_dir(cfg) == site_yml.parent.resolve() / "ansible" / "generated"
    tool_root = Path(__file__).resolve().parents[2]
    assert tool_root not in generated_dir(cfg).parents


def test_generated_scripts_are_owner_only(cfg, tmp_path):
    from lankit.core.generator import generate_all
    out = tmp_path / "gen"
    paths = generate_all(cfg, out)
    assert oct(out.stat().st_mode & 0o777) == oct(0o700)
    for p in paths:
        assert oct(p.stat().st_mode & 0o777) == oct(0o600), p.name


def test_filtered_segments_offer_only_the_filtering_resolver(cfg, tmp_path):
    text = render(cfg, tmp_path)["02-dhcp.rsc"]
    offers = [l.strip() for l in text.splitlines() if l.strip().startswith("dns-server=")]
    assert offers, "expected DHCP networks with dns-server"
    filtered = [o for o in offers if "1.1.1.1" not in o]
    assert filtered and all(o == f"dns-server={cfg.dns_ip} \\" for o in filtered), filtered
