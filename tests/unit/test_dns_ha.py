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
