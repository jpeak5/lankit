"""lankit.core.untagged: finding objects lankit doesn't own, and the subset
that would collide with what an apply adds (#70).

Router output below is trimmed from a RouterOS 7.24.2 CHR (the lab VM), so
the flag column and ';;;' comment shapes are the real ones.
"""

from lankit.core import untagged
from lankit.core.generator import render_texts

ADDRESS = """\
Flags: D - DYNAMIC
0 D address=192.168.79.1/24 network=192.168.79.0 interface=ether6
    actual-interface=ether6 vrf=main

1   ;;; defconf
    address=192.168.88.1/24 network=192.168.88.0 interface=bridge
    actual-interface=bridge vrf=main

2   ;;; lankit:dhcp:trusted:address
    address=10.10.0.1/24 network=10.10.0.0 interface=vlan-trusted
    actual-interface=vlan-trusted vrf=main

3   address=10.99.0.1/24 network=10.99.0.0 interface=ether5
    actual-interface=ether5 vrf=main
"""

POOL = """\
0  name="default-dhcp" ranges=192.168.88.10-192.168.88.254

1  ;;; lankit:dhcp:trusted:pool
   name="pool-trusted" ranges=10.10.0.10-10.10.0.254
"""

DHCP_SERVER = """\
0  name="defconf" interface=bridge lease-time=30m address-pool=default-dhcp
"""

LIST_MEMBER = """\
0  ;;; defconf
   list=LAN interface=bridge dynamic=no

1  ;;; lab:mgmt
   list=LAN interface=ether6 dynamic=no

2  list=LAN interface=vlan-x dynamic=yes
"""

VLAN = """\
Flags: R - RUNNING
0  R ;;; lankit:vlan:guest:interface
     name="vlan-guest" mtu=1500 vlan-id=50 interface=bridge

1  R name="vlan-trusted" mtu=1500 vlan-id=10 interface=bridge
"""

WIFI = """\
Flags: X - DISABLED; R - RUNNING
0  R name="wifi1" configuration.ssid="_master-5g-noconnect"

1  R ;;; lankit:wifi:trusted:ap-5g
     name="wifi-trusted-5g" master-interface=wifi1

2  X name="wifi-guest" master-interface=wifi2
"""

SCHEDULER = """\
0  name="lankit-failsafe" start-date=2026-11-15 start-time=10:00:00

1  name="nightly-reboot" start-time=03:00:00
"""


def _labels(items):
    return sorted((u.table, u.comment, u.label) for u in items)


def test_reports_only_hand_made_objects():
    found = untagged.find_untagged({
        "address": ADDRESS, "pool": POOL, "dhcp-server": DHCP_SERVER,
        "list-member": LIST_MEMBER, "vlan": VLAN, "wifi": WIFI,
        "scheduler": SCHEDULER,
    })
    assert _labels(found) == sorted([
        ("address", "", "address=10.99.0.1/24"),
        ("list-member", "lab:mgmt", "list=LAN interface=ether6"),
        ("scheduler", "", "name=nightly-reboot start-time=03:00:00"),
        ("vlan", "", "name=vlan-trusted"),
        ("wifi", "", "name=wifi-guest"),
    ])


def test_skips_dynamic_lankit_and_defconf():
    found = untagged.find_untagged({"address": ADDRESS})
    addrs = [dict(u.fields)["address"] for u in found]
    assert "192.168.79.1/24" not in addrs      # D flag
    assert "192.168.88.1/24" not in addrs      # defconf
    assert "10.10.0.1/24" not in addrs         # lankit:


def test_tables_without_output_are_skipped():
    assert untagged.find_untagged({}) == []


def test_scan_adds_reads_menu_and_continuations():
    text = (
        "/ip pool\n"
        "add name=pool-a \\\n"
        "    ranges=10.0.0.10-10.0.0.254 \\\n"
        "    comment=\"lankit:dhcp:a:pool\"\n"
        ":foreach i in=[/ip pool find] do={ /ip pool remove $i }\n"
        "/interface vlan\n"
        "add name=vlan-a vlan-id=7 interface=bridge comment=\"lankit:vlan:a:interface\"\n"
    )
    assert untagged.scan_adds(text) == [
        ("pool", {"name": "pool-a", "ranges": "10.0.0.10-10.0.0.254",
                  "comment": "lankit:dhcp:a:pool"}),
        ("vlan", {"name": "vlan-a", "vlan-id": "7", "interface": "bridge",
                  "comment": "lankit:vlan:a:interface"}),
    ]


def test_collision_with_a_hand_made_vlan_interface(cfg):
    # The #70 case: an existing untagged vlan-trusted survives the
    # remove-pass, so 01-vlans' add would fail with "already have such".
    texts = render_texts(cfg, segment_filter=None, include_globals=True)
    found = untagged.find_untagged({"vlan": VLAN, "address": ADDRESS})
    hits = untagged.find_collisions(found, texts.values())
    assert [u.label for u in hits] == ["name=vlan-trusted"]


def test_no_collision_from_unrelated_objects(cfg):
    texts = render_texts(cfg, segment_filter=None, include_globals=True)
    found = untagged.find_untagged({"address": ADDRESS, "list-member": LIST_MEMBER})
    assert found                       # there are untagged objects...
    assert untagged.find_collisions(found, texts.values()) == []   # ...but no clash


def test_render_has_adds_for_every_identity_table_it_uses(cfg):
    # Guards scan_adds against template shape changes: a full render must
    # yield adds for the core tables, or collision checks silently go blind.
    texts = render_texts(cfg, segment_filter=None, include_globals=True)
    tables = {t for text in texts.values() for t, _ in untagged.scan_adds(text)}
    assert {"vlan", "pool", "dhcp-server", "dhcp-network", "address",
            "address-list", "list-member", "bridge-vlan"} <= tables


# ─── apply's pre-flight ──────────────────────────────────────────────────────

import pytest
from rich.console import Console

from lankit.cli.commands.apply import _check_untagged


class _Conn:
    def __init__(self, raw):
        self.raw = raw

    def read_tag_tables(self):
        return self.raw


def test_apply_refuses_on_collision(cfg):
    texts = render_texts(cfg, segment_filter=None, include_globals=True)
    console = Console(record=True, width=200)
    with pytest.raises(SystemExit) as exc:
        _check_untagged(_Conn({"vlan": VLAN}), list(texts.values()), console)
    assert exc.value.code == 1
    out = console.export_text()
    assert "name=vlan-trusted" in out and "Nothing has been changed" in out


def test_apply_warns_and_continues_without_collision(cfg):
    texts = render_texts(cfg, segment_filter=None, include_globals=True)
    console = Console(record=True, width=200)
    _check_untagged(_Conn({"list-member": LIST_MEMBER}), list(texts.values()), console)
    assert "1 object(s) on the router aren't managed by lankit" in console.export_text()


def test_apply_silent_when_everything_is_owned(cfg):
    console = Console(record=True, width=200)
    _check_untagged(_Conn({"pool": POOL}), [], console)
    assert console.export_text() == ""
