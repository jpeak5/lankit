"""
Unit tests for lankit.core.config — loading, schema validation,
cross-reference checks, template resolution, and Segment properties.

All configs are loaded by explicit fixture Path; nothing reads cwd
or LANKIT_CONFIG.
"""
import pytest

from lankit.core.config import ConfigError, Segment, load


# ─── Valid load ───────────────────────────────────────────────────────────────

def test_load_valid_minimal(cfg):
    assert cfg.household_name == "Testhouse"
    assert cfg.internal_domain == "internal"
    assert set(cfg.segments) == {"trusted", "iot", "servers"}
    assert cfg.permissions["trusted"].can_reach == ["iot", "servers"]
    assert cfg.privacy.query_logging == "full"
    assert cfg.privacy.query_retention == "7d"
    assert cfg.wifi_password_source == "prompt"
    assert cfg.vpn == "none"
    assert cfg.dnssec is True
    assert cfg.failsafe_seconds == 120
    assert cfg.router.wan_interface == "ether1"
    assert cfg.router.lan_interface == "ether2"


def test_dns_ip_inferred_from_dns_server_host(cfg):
    # No explicit dns_ip in the fixture — inferred from hosts.dns_server.ip
    assert cfg.dns_ip == "10.20.0.2"
    assert cfg.hosts["dns_server"].segment == "servers"
    assert cfg.hosts["dns_server"].enabled is True
    assert cfg.hosts["dns_server"].ssh_user == "pi"  # default


def test_dns_ip_fallback_without_pihole(no_pihole_yml):
    cfg = load(no_pihole_yml)
    assert cfg.dns_ip == "1.1.1.1"
    assert cfg.hosts == {}


# ─── Schema rejection: unfilled CHOOSE values ────────────────────────────────

def test_unfilled_choose_values_are_named(minimal_yml, mutated):
    def null_out(raw):
        raw["privacy"]["query_logging"] = None
        raw["wifi_password_source"] = None
        raw["vpn"] = None

    path = mutated(minimal_yml, null_out)
    with pytest.raises(ConfigError) as exc:
        load(path)

    msg = str(exc.value)
    # The error must name each undecided field, not just dump schema noise
    assert "privacy.query_logging" in msg
    assert "wifi_password_source" in msg
    assert "vpn" in msg
    assert "require a decision" in msg


def test_invalid_enum_value_rejected(minimal_yml, mutated):
    path = mutated(
        minimal_yml,
        lambda raw: raw["segments"]["iot"].__setitem__("internet", "sometimes"),
    )
    with pytest.raises(ConfigError) as exc:
        load(path)
    assert "internet" in str(exc.value)


def test_missing_required_top_level_key(minimal_yml, mutated):
    path = mutated(minimal_yml, lambda raw: raw.pop("failsafe_seconds"))
    with pytest.raises(ConfigError) as exc:
        load(path)
    assert "failsafe_seconds" in str(exc.value)


# ─── Cross-reference validation ──────────────────────────────────────────────

def test_permissions_for_unknown_segment(minimal_yml, mutated):
    path = mutated(
        minimal_yml,
        lambda raw: raw["permissions"].__setitem__("ghost", {"can_reach": []}),
    )
    with pytest.raises(ConfigError) as exc:
        load(path)
    assert "permissions.ghost" in str(exc.value)
    assert "'ghost' is not defined in segments" in str(exc.value)


def test_can_reach_unknown_target(minimal_yml, mutated):
    path = mutated(
        minimal_yml,
        lambda raw: raw["permissions"]["trusted"]["can_reach"].append("ghost"),
    )
    with pytest.raises(ConfigError) as exc:
        load(path)
    assert "permissions.trusted.can_reach" in str(exc.value)
    assert "'ghost' is not a defined segment" in str(exc.value)


def test_host_in_unknown_segment(minimal_yml, mutated):
    path = mutated(
        minimal_yml,
        lambda raw: raw["hosts"]["dns_server"].__setitem__("segment", "ghost"),
    )
    with pytest.raises(ConfigError) as exc:
        load(path)
    assert "hosts.dns_server.segment" in str(exc.value)


def test_dns_ip_required_when_no_dns_server_host(no_pihole_yml, mutated):
    path = mutated(no_pihole_yml, lambda raw: raw.pop("dns_ip"))
    with pytest.raises(ConfigError) as exc:
        load(path)
    assert "dns_ip" in str(exc.value)


def test_dns_ip_required_when_dns_server_disabled(minimal_yml, mutated):
    # A disabled Pi-hole host must not satisfy the dns_ip requirement
    path = mutated(
        minimal_yml,
        lambda raw: raw["hosts"]["dns_server"].__setitem__("enabled", False),
    )
    with pytest.raises(ConfigError) as exc:
        load(path)
    assert "dns_ip" in str(exc.value)


# ─── Template resolution ─────────────────────────────────────────────────────

def test_household_name_resolved_in_ssids(cfg):
    assert cfg.segments["trusted"].ssid == "Testhouse"
    assert cfg.segments["iot"].ssid == "Testhouse IoT"


def test_unknown_template_variable_left_intact(minimal_yml, mutated):
    # Jinja2 renders unknown vars as empty by default; config resolution
    # must not crash on strings that merely look template-ish.
    path = mutated(
        minimal_yml,
        lambda raw: raw["segments"]["trusted"].__setitem__(
            "comment", "brace {not-jinja}"
        ),
    )
    cfg = load(path)
    assert cfg.segments["trusted"].comment == "brace {not-jinja}"


# ─── Segment properties ──────────────────────────────────────────────────────

def test_segment_gateway_and_pool_range(cfg):
    trusted = cfg.segments["trusted"]
    assert trusted.gateway == "10.10.0.1"
    assert trusted.pool_range == "10.10.0.10-10.10.0.254"
    iot = cfg.segments["iot"]
    assert iot.gateway == "10.30.0.1"
    assert iot.pool_range == "10.30.0.10-10.30.0.254"


def test_segment_has_wifi(cfg):
    assert cfg.segments["trusted"].has_wifi is True
    assert cfg.segments["iot"].has_wifi is True
    assert cfg.segments["servers"].has_wifi is False  # ssid: null


def test_has_wifi_requires_both_ssid_and_bands():
    seg = Segment(
        name="odd", vlan_id=99, comment="", subnet="10.99.0.0/24",
        ssid="Orphan SSID", wifi_bands=[], ssid_hidden=False,
        bandwidth_up=None, bandwidth_down=None,
        dns="none", force_dns=False, internet="none", client_isolation=False,
    )
    assert seg.has_wifi is False


# ─── Config helper views ─────────────────────────────────────────────────────

def test_config_segment_views(cfg):
    assert set(cfg.segments_with_wifi()) == {"trusted", "iot"}
    assert set(cfg.segments_needing_dns()) == {"trusted", "iot"}
    assert set(cfg.segments_with_force_dns()) == {"iot"}
    assert set(cfg.segments_with_bandwidth()) == {"iot"}
