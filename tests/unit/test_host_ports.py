"""
Host ports: `hosts.<name>.port` makes the router port a host is wired to an
untagged access port on the host's segment, and takes it off the flat
VLAN 1. dns_server defaults to router.lan_interface (the one host port
lankit modelled before `port:` existed).

Why it exists: a hand-set PVID on the app server's port (ether3) was reset
to 1 by every apply, moving the host onto the flat network.
"""
import json

import pytest

from lankit.core import revert
from lankit.core.config import ConfigError, load
from lankit.core.generator import render_texts


@pytest.fixture(autouse=True)
def isolated_known_good_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(revert, "_KNOWN_GOOD_DIR", tmp_path / "known-good")


def _with_app_server(port="ether3", segment="servers", enabled=True):
    def mutate(raw):
        raw["hosts"]["app_server"] = {
            "hostname": "apps", "segment": segment, "ip": "10.20.0.3",
            "mac": "DC:A6:32:00:00:02", "services": ["caddy"],
            "enabled": enabled,
        }
        if port is not None:
            raw["hosts"]["app_server"]["port"] = port
    return mutate


def _row_untagged(text, segment):
    """untagged= of a segment's bridge-vlan add, or None."""
    import re
    m = re.search(r'add bridge=bridge \\\n((?:    .*\\\n)*?)    comment="lankit:vlan:'
                  + segment + r':bridge"', text)
    assert m, f"no bridge row for {segment}"
    u = re.search(r"untagged=(\S+)", m.group(1))
    return u.group(1) if u else None


def _servers_row(text):
    return _row_untagged(text, "servers")


def _vlans(cfg) -> str:
    return render_texts(cfg, None, segment_filter=None, include_globals=True)["01-vlans.rsc"]


# ─── Config ──────────────────────────────────────────────────────────────────

def test_dns_server_port_defaults_to_lan_interface(cfg):
    assert cfg.hosts["dns_server"].port is None
    assert cfg.host_ports() == {"ether2": "servers"}


def test_explicit_ports(minimal_yml, mutated):
    cfg = load(mutated(minimal_yml, _with_app_server("ether3")))
    assert cfg.host_ports() == {"ether2": "servers", "ether3": "servers"}


def test_dns_server_explicit_port_overrides_lan_interface(minimal_yml, mutated):
    def m(raw):
        raw["hosts"]["dns_server"]["port"] = "ether4"
    cfg = load(mutated(minimal_yml, m))
    assert cfg.host_ports() == {"ether4": "servers"}


def test_app_server_without_port_has_none(minimal_yml, mutated):
    cfg = load(mutated(minimal_yml, _with_app_server(port=None)))
    assert cfg.host_ports() == {"ether2": "servers"}


def test_disabled_host_port_ignored(minimal_yml, mutated):
    cfg = load(mutated(minimal_yml, _with_app_server("ether3", enabled=False)))
    assert cfg.host_ports() == {"ether2": "servers"}


def test_two_hosts_one_port_same_segment_allowed(minimal_yml, mutated):
    # Both behind an unmanaged switch on the same port: still one segment.
    cfg = load(mutated(minimal_yml, _with_app_server("ether2")))
    assert cfg.host_ports() == {"ether2": "servers"}


def test_two_hosts_one_port_different_segments_rejected(minimal_yml, mutated):
    with pytest.raises(ConfigError) as exc:
        load(mutated(minimal_yml, _with_app_server("ether2", segment="iot")))
    assert "hosts.app_server.port" in str(exc.value)
    assert "One port carries one segment" in str(exc.value)


def test_port_on_wan_rejected(minimal_yml, mutated):
    with pytest.raises(ConfigError) as exc:
        load(mutated(minimal_yml, _with_app_server("ether1")))
    assert "hosts.app_server.port: ether1 is router.wan_interface" in str(exc.value)


def test_empty_port_rejected_by_schema(minimal_yml, mutated):
    with pytest.raises(ConfigError):
        load(mutated(minimal_yml, _with_app_server("")))


# ─── 01-vlans render ─────────────────────────────────────────────────────────

def test_default_render_unchanged(cfg):
    text = _vlans(cfg)
    assert "untagged=ether3,ether4,ether5" in text
    for p in ("ether3", "ether4", "ether5"):
        assert f"set [find where interface={p}] pvid=1" in text
    assert "set [find where interface=ether2] pvid=20" in text
    assert _servers_row(text) == "ether2"


def test_app_server_port_leaves_flat_vlan(minimal_yml, mutated):
    text = _vlans(load(mutated(minimal_yml, _with_app_server("ether3"))))
    assert "untagged=ether4,ether5" in text
    assert "set [find where interface=ether3] pvid=1" not in text
    assert "set [find where interface=ether3] pvid=20" in text
    # Both host ports on one row; and never a `set [find where vlan-ids=N]`,
    # which also matches the dynamic row a port already at PVID N has
    # (CHR 7.24.2: "can not change dynamic", import aborted).
    assert _servers_row(text) == "ether2,ether3"
    assert not [l for l in text.splitlines()
                if l.startswith("set [find where vlan-ids=")]


def test_host_ports_on_different_segments(minimal_yml, mutated):
    text = _vlans(load(mutated(minimal_yml, _with_app_server("ether3", segment="iot"))))
    iot_vlan = load(minimal_yml).segments["iot"].vlan_id
    assert f"set [find where interface=ether3] pvid={iot_vlan}" in text
    assert _row_untagged(text, "iot") == "ether3"
    assert _servers_row(text) == "ether2"


def test_host_port_pvid_set_before_vlan_filtering(minimal_yml, mutated):
    text = _vlans(load(mutated(minimal_yml, _with_app_server("ether3"))))
    assert text.index("interface=ether3] pvid=20") < text.index("vlan-filtering=yes")


def test_every_flat_port_taken_omits_untagged(minimal_yml, mutated, monkeypatch):
    # Two hosts can't cover ether3-5, so shrink the flat set to check the
    # template renders a valid add with no untagged= at all.
    import lankit.core.generator as gen
    monkeypatch.setattr(gen, "_FLAT_PORTS", ("ether3",))
    text = _vlans(load(mutated(minimal_yml, _with_app_server("ether3"))))
    flat = text[text.index("vlan-ids=1 "):text.index('comment="lankit:vlan:legacy:flat"')]
    assert "untagged" not in flat
    assert "pvid=1" not in text


def test_segment_render_has_no_port_changes(minimal_yml, mutated):
    cfg = load(mutated(minimal_yml, _with_app_server("ether3")))
    text = render_texts(cfg, None, segment_filter="servers")["01-vlans.rsc"]
    assert "pvid=" not in text
    assert _servers_row(text) is None       # membership comes with the PVIDs


# ─── Revert ──────────────────────────────────────────────────────────────────

def test_remove_pass_restores_host_port_outside_flat_set():
    text = revert.build_remove_pass("ether2", host_ports=["ether2", "ether3", "ether7"])
    assert "set [find where interface=ether7] pvid=1" in text
    assert "host-port ether7" in text
    # lan_interface and ether3-5 already have their own restore lines.
    assert "host-port ether2" not in text
    assert "host-port ether3" not in text


def test_remove_pass_default_has_no_extra_host_port_lines():
    assert "host-port" not in revert.build_remove_pass("ether2")


def test_manifest_records_host_ports():
    revert.save_known_good(
        "192.168.88.1", {"01-vlans.rsc": ""}, segments_applied=[], globals_applied=True,
        network_yml_hash="h", lan_interface="ether2", host_ports=["ether3", "ether2"],
    )
    assert revert.load_known_good("192.168.88.1").manifest.host_ports == ["ether2", "ether3"]


def test_archive_from_before_host_ports_still_loads():
    revert.save_known_good(
        "192.168.88.1", {"01-vlans.rsc": ""}, segments_applied=[], globals_applied=True,
        network_yml_hash="h", lan_interface="ether2",
    )
    aid = revert.list_known_good_ids("192.168.88.1")[0]
    path = revert._router_dir("192.168.88.1") / aid / "manifest.json"
    data = json.loads(path.read_text())
    del data["host_ports"]
    path.write_text(json.dumps(data))
    assert revert.load_known_good("192.168.88.1").manifest.host_ports == []


def test_ports_to_restore_union_of_config_and_archive(minimal_yml, mutated):
    cfg = load(mutated(minimal_yml, _with_app_server("ether3")))
    revert.save_known_good(
        "192.168.88.1", {"01-vlans.rsc": ""}, segments_applied=[], globals_applied=True,
        network_yml_hash="h", lan_interface="ether2", host_ports=["ether2", "ether7"],
    )
    archive = revert.load_known_good("192.168.88.1")
    assert revert.host_ports_to_restore(cfg, archive) == ["ether2", "ether3", "ether7"]
    assert revert.host_ports_to_restore(cfg, None) == ["ether2", "ether3"]


def test_revert_bundle_passes_host_ports_through():
    bundle = revert.build_revert_bundle(None, "ether2", host_ports=["ether8"])
    assert "set [find where interface=ether8] pvid=1" in bundle


def test_host_port_outside_rendered_segments_left_alone(minimal_yml, mutated):
    # Archive re-renders may pass a segment subset with include_globals=True.
    # The global remove drops every lankit VLAN row, so a host port on a
    # segment outside the render must not get a PVID with no row behind it.
    cfg = load(mutated(minimal_yml, _with_app_server("ether3")))
    text = render_texts(cfg, None, segment_filter={"trusted", "iot"},
                        include_globals=True)["01-vlans.rsc"]
    assert "pvid=20" not in text
    assert "vlan-ids=20" not in text
    assert "untagged=ether4,ether5" in text      # still off the flat VLAN


def test_flat_ports_shared_by_render_and_revert(cfg):
    from lankit.core.config import FLAT_LAN_PORTS
    text = revert.build_remove_pass("ether2")
    assert "{" + ";".join(f'"{p}"' for p in FLAT_LAN_PORTS) + "}" in text


# ─── apply pre-flight: host ports must be bridge ports ───────────────────────

from rich.console import Console

from lankit.cli.commands.apply import _check_host_ports

_BRIDGE = (
    "0 comment=defconf interface=ether2 bridge=bridge pvid=40\n"
    "1 comment=defconf interface=ether3 bridge=bridge pvid=1\n"
    "2 comment=defconf interface=ether4 bridge=bridge pvid=1\n"
)


class _Conn:
    def __init__(self, out, err=""):
        self.out, self.err = out, err

    def run_tolerant(self, cmd):
        assert cmd == "/interface bridge port print terse where bridge=bridge"
        return self.out, self.err


def test_preflight_passes_when_host_ports_are_bridge_ports(minimal_yml, mutated):
    cfg = load(mutated(minimal_yml, _with_app_server("ether3")))
    _check_host_ports(_Conn(_BRIDGE), cfg, Console(record=True))


def test_preflight_refuses_unknown_port(minimal_yml, mutated):
    # A typo'd name in untagged= aborts the import mid-script (RouterOS
    # 7.24.2: "input does not match any value of interface").
    cfg = load(mutated(minimal_yml, _with_app_server("ether9")))
    console = Console(record=True, width=200)
    with pytest.raises(SystemExit) as exc:
        _check_host_ports(_Conn(_BRIDGE), cfg, console)
    assert exc.value.code == 1
    out = console.export_text()
    assert "ether9" in out and "Nothing has been changed" in out


def test_preflight_continues_when_bridge_unreadable(minimal_yml, mutated):
    cfg = load(mutated(minimal_yml, _with_app_server("ether9")))
    console = Console(record=True)
    _check_host_ports(_Conn("", "failure"), cfg, console)
    assert "continuing" in console.export_text()


# ─── Flat VLAN 1 must reach the router itself ───────────────────────────────

def test_flat_row_leaves_the_cpu_untagged(cfg):
    # The flat address is untagged on the bridge interface. tagged=bridge on
    # VLAN 1 hands the CPU tagged frames instead, and with filtering on the
    # router stops answering on ether3-5 (CHR 7.24.2: no ARP reply).
    text = _vlans(cfg)
    flat = text[text.index("vlan-ids=1 "):text.index('comment="lankit:vlan:legacy:flat"')]
    assert "tagged=bridge" not in flat
    start = text.rindex("add bridge=bridge", 0, text.index("vlan-ids=1 "))
    assert "tagged=" not in text[start:text.index("vlan-ids=1 ")].replace("untagged=", "")
