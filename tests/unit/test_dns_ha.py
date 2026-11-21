"""dns_ha: two Pi-hole hosts behind one shared address (keepalived).

The router side only ever sees the shared address; each node keeps its own
address for its lease and for Ansible. Validation keeps the pair coherent.
"""

import json
import re
from pathlib import Path

import jinja2
import pytest

from lankit.core.config import ConfigError, load
from lankit.core.generator import render_texts
from lankit.cli.commands.provision import _dns_ha_vars

ROLE = Path(__file__).parents[2] / "ansible/roles/dns-server"


def _pair(raw, vip="10.20.0.2", dns_ip="10.20.0.4"):
    raw["hosts"]["dns_server"]["hostname"] = "dns"
    raw["hosts"]["dns_server"]["ip"] = dns_ip
    raw["hosts"]["dns_server"]["services"] = ["pihole", "unbound"]
    raw["hosts"]["app_server"] = {
        "hostname": "apps", "segment": "servers", "ip": "10.20.0.3",
        "mac": "DC:A6:32:00:00:02", "services": ["caddy", "pihole", "unbound"],
    }
    raw["dns_ha"] = {"vip": vip, "nodes": ["app_server", "dns_server"]}


@pytest.fixture
def pair_cfg(minimal_yml, mutated):
    return load(mutated(minimal_yml, _pair))


def _load_err(minimal_yml, mutated, fn):
    with pytest.raises(ConfigError) as exc:
        load(mutated(minimal_yml, fn))
    return str(exc.value)


# ── config ───────────────────────────────────────────────────────────────────

def test_service_address_is_the_vip(pair_cfg):
    assert pair_cfg.dns_ip == "10.20.0.2"
    assert pair_cfg.hosts["dns_server"].ip == "10.20.0.4"
    assert pair_cfg.dns_ha.nodes == ["app_server", "dns_server"]
    assert pair_cfg.dns_ha.vrid == 53


def test_no_dns_ha_is_unchanged(cfg):
    assert cfg.dns_ha is None
    assert cfg.dns_ip == cfg.hosts["dns_server"].ip


@pytest.mark.parametrize("vip, expect", [
    ("10.20.0.4", "must not be any host's own address"),
    ("10.20.0.50", "inside the DHCP pool"),
    ("10.20.0.1", "segment gateway"),
    ("10.10.0.5", "outside the nodes' segment"),
    ("10.20.0.0", "network address"),
    ("10.20.0.999", "not an IPv4 address"),
])
def test_bad_vip_is_refused(minimal_yml, mutated, vip, expect):
    msg = _load_err(minimal_yml, mutated, lambda raw: _pair(raw, vip=vip))
    assert expect in msg


def test_node_without_pihole_is_refused(minimal_yml, mutated):
    def fn(raw):
        _pair(raw)
        raw["hosts"]["app_server"]["services"] = ["caddy"]
    assert "must list pihole" in _load_err(minimal_yml, mutated, fn)


def test_nodes_on_different_segments_are_refused(minimal_yml, mutated):
    def fn(raw):
        _pair(raw)
        raw["hosts"]["app_server"]["segment"] = "trusted"
        raw["hosts"]["app_server"]["ip"] = "10.10.0.3"
    assert "same segment" in _load_err(minimal_yml, mutated, fn)


def test_unknown_node_is_refused_by_schema(minimal_yml, mutated):
    def fn(raw):
        _pair(raw)
        raw["dns_ha"]["nodes"] = ["dns_server", "printer"]
    with pytest.raises(ConfigError):
        load(mutated(minimal_yml, fn))


# ── router render ────────────────────────────────────────────────────────────

def test_router_sees_only_the_vip_and_the_lease_pins_the_host(pair_cfg):
    texts = render_texts(pair_cfg, segment_filter=None, include_globals=True)
    dhcp = texts["02-dhcp.rsc"]
    assert "dns-server=10.20.0.2" in dhcp
    lease = re.search(r'add address=(\S+) \\\s+mac-address=DC:A6:32:00:00:01', dhcp)
    assert lease and lease.group(1) == "10.20.0.4"
    fw = texts["03-firewall.rsc"]
    assert "dst-address=10.20.0.2" in fw and "10.20.0.4" not in fw


# ── Ansible side ─────────────────────────────────────────────────────────────

def test_provision_vars(pair_cfg):
    v = _dns_ha_vars(pair_cfg)
    assert v == {
        "vip": "10.20.0.2", "prefix": 24, "vrid": 53,
        "nodes": [
            {"name": "app_server", "ip": "10.20.0.3", "priority": 150},
            {"name": "dns_server", "ip": "10.20.0.4", "priority": 100},
        ],
    }


def test_provision_vars_empty_without_dns_ha(cfg):
    assert _dns_ha_vars(cfg) == {}


def _render_keepalived(ha, host):
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(ROLE / "templates")),
                             undefined=jinja2.StrictUndefined)
    return env.get_template("keepalived.conf.j2").render(
        lankit_dns_ha=ha, inventory_hostname=host,
        ansible_default_ipv4={"interface": "eth0"})


def test_keepalived_config_per_node(pair_cfg):
    ha = _dns_ha_vars(pair_cfg)
    pi4 = _render_keepalived(ha, "app_server")
    pi3 = _render_keepalived(ha, "dns_server")
    assert "priority 150" in pi4 and "priority 100" in pi3
    assert "unicast_src_ip 10.20.0.3" in pi4
    assert re.search(r"unicast_peer \{\s+10\.20\.0\.4\s+\}", pi4)
    assert re.search(r"unicast_peer \{\s+10\.20\.0\.3\s+\}", pi3)
    for conf in (pi3, pi4):
        assert "10.20.0.2/24 dev eth0" in conf
        assert "virtual_router_id 53" in conf
        assert "track_script" in conf
        # A node fresh from boot must prove itself before it can preempt.
        assert re.search(r"vrrp_script lankit_dns_check \{[^}]*\binit_fail\b", conf)


def test_health_check_covers_pihole_and_unbound():
    check = (ROLE / "files/lankit-dns-check.sh").read_text()
    assert "@127.0.0.1 pi.hole" in check
    assert "-p 5335 @127.0.0.1" in check


# ── Pi-hole dashboard by name ────────────────────────────────────────────────

from lankit.cli.commands.provision import _build_dns_hosts

CADDY = Path(__file__).parents[2] / "ansible/roles/caddy/templates"


def _render_caddy(**v):
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(CADDY)),
                             undefined=jinja2.StrictUndefined)
    base = dict(lankit_internal_domain="internal", lankit_portals={"registration": True},
                lankit_pihole_enabled=True, lankit_dns_server_ip="10.20.0.2",
                lankit_tls_enabled=True)
    base.update(v)
    return env.get_template("Caddyfile.j2").render(**base)


@pytest.mark.parametrize("tls", [True, False])
def test_caddy_serves_the_dashboard_via_the_service_address(tls):
    conf = _render_caddy(lankit_tls_enabled=tls)
    site = "pihole.internal {" if tls else "http://pihole.internal {"
    assert site in conf
    assert "reverse_proxy 10.20.0.2:8081" in conf
    if tls:
        assert "http://pihole.internal" in conf     # in the http->https redirect list


def test_caddy_has_no_dashboard_without_pihole():
    assert "pihole.internal" not in _render_caddy(lankit_pihole_enabled=False)


def test_dashboard_name_points_at_the_app_server(pair_cfg):
    hosts = _build_dns_hosts(pair_cfg)
    assert "10.20.0.3 pihole.internal" in hosts
    texts = render_texts(pair_cfg, segment_filter=None, include_globals=True)
    rec = re.search(r'add name=pihole\.internal \\\s+address=(\S+)', texts["05-dns.rsc"])
    assert rec and rec.group(1) == "10.20.0.3"


def test_dns_name_follows_the_shared_address(pair_cfg):
    hosts = _build_dns_hosts(pair_cfg)
    assert "10.20.0.2 dns.internal" in hosts
    assert "10.20.0.4 dns.internal" not in hosts


def test_no_dashboard_record_when_a_host_is_named_pihole(cfg):
    # The minimal fixture's DNS host is called "pihole": its own record stands.
    hosts = [h for h in _build_dns_hosts(cfg) if h.endswith(" pihole.internal")]
    assert hosts == [f"{cfg.hosts['dns_server'].ip} pihole.internal"]


def test_post_apply_probe_asks_the_shared_address(pair_cfg):
    # The shared address can be on the other node and another router port;
    # the DNS server's own address answering proves nothing for clients.
    from lankit.cli.commands.apply import _probe_plan
    _, dns_ip = _probe_plan(pair_cfg, None)
    assert dns_ip == "10.20.0.2"


def test_post_apply_probe_without_pair_asks_the_host(cfg):
    from lankit.cli.commands.apply import _probe_plan
    _, dns_ip = _probe_plan(cfg, None)
    assert dns_ip == cfg.hosts["dns_server"].ip


def test_post_apply_probe_skips_public_resolver_when_pihole_disabled(minimal_yml, mutated):
    # dns_server disabled + dns_ip 1.1.1.1: this apply can't have broken a
    # public resolver, and a WAN blip must not fail the probe.
    from lankit.cli.commands.apply import _probe_plan

    def m(raw):
        raw["hosts"]["dns_server"]["enabled"] = False
        raw["dns_ip"] = "1.1.1.1"
    _, dns_ip = _probe_plan(load(mutated(minimal_yml, m)), None)
    assert dns_ip is None
