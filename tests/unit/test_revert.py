"""
Unit tests for lankit.core.revert — the tag-scoped revert mechanism (G1.2).
Design: docs/design/tag-scoped-revert.md (signed off 2026-07-07).

Covers, per the design's own §12 testing requirements: remove-pass order
and :do/on-error wrapping, the first-apply (no archive) case, expected-tag
derivation against the templates' actual comment shapes, known-good
archive manifest round-trip, retention sweep, and bundle re-runnability
(removes precede adds). Also covers the live-tag parser (RouterOS 7's
';;; text' comment format, ported from audit.py) and the legacy `kit:`
prefix detection in verify_revert.
"""
import json

import pytest

from lankit.core import revert
from lankit.core.generator import render_texts


@pytest.fixture(autouse=True)
def isolated_known_good_dir(tmp_path, monkeypatch):
    """Every test gets its own known-good archive directory — never touch
    the real ~/.lankit/known-good/."""
    monkeypatch.setattr(revert, "_KNOWN_GOOD_DIR", tmp_path / "known-good")


ROUTER = "192.168.88.1"


# ─── RouterOS output parsing (ported from audit.py) ─────────────────────────

def test_split_records_handles_semicolon_comment_format():
    # RouterOS 7's actual print-detail format: comment on its own ';;; text'
    # line, not an inline comment="..." field.
    output = """Flags: X - disabled
 0   ;;; lankit:vlan:trusted:interface
     name=vlan-trusted vlan-id=10 interface=bridge

 1   ;;; lankit:vlan:iot:interface
     name=vlan-iot vlan-id=30 interface=bridge
"""
    records = revert._split_records(output)
    assert len(records) == 2
    assert records[0]["comment"] == "lankit:vlan:trusted:interface"
    assert records[0]["vlan-id"] == "10"
    assert records[1]["comment"] == "lankit:vlan:iot:interface"


def test_split_records_ignores_records_without_a_comment():
    output = """Flags: X - disabled
 0   name=ether3 pvid=1
"""
    records = revert._split_records(output)
    assert len(records) == 1
    assert "comment" not in records[0]


def test_read_live_tags_collects_across_tables_and_ignores_non_lankit():
    raw = {
        "filter": ' 0   ;;; lankit:fw:all:default-deny\n     chain=forward action=drop\n',
        "vlan": ' 0   ;;; lankit:vlan:trusted:interface\n     name=vlan-trusted\n'
                ' 1   name=vlan-legacy\n',  # no comment — must be ignored
    }
    tags = revert.read_live_tags(raw)
    assert tags == {"lankit:fw:all:default-deny", "lankit:vlan:trusted:interface"}


def test_read_live_tags_includes_legacy_kit_prefix():
    raw = {"filter": ' 0   ;;; kit:fw:iot:isolation\n     chain=forward\n'}
    assert revert.read_live_tags(raw) == {"kit:fw:iot:isolation"}


# ─── read_wifi_master_facts ─────────────────────────────────────────────────

def test_read_wifi_master_facts_configuration_prefixed_fields():
    raw = (
        ' 0    name=wifi1 configuration.ssid="MikroTik-5G" configuration.hide-ssid=no\n'
        ' 1    name=wifi2 configuration.ssid="MikroTik-2G" configuration.hide-ssid=yes\n'
        ' 2    name=wifi-trusted-5g configuration.ssid="Home" configuration.hide-ssid=no\n'
    )
    facts = revert.read_wifi_master_facts(raw)
    assert facts.wifi1_ssid == "MikroTik-5G"
    assert facts.wifi1_hidden is False
    assert facts.wifi2_ssid == "MikroTik-2G"
    assert facts.wifi2_hidden is True


def test_read_wifi_master_facts_falls_back_to_bare_field_names():
    # audit.py hedges that RouterOS might emit `ssid`/`hide-ssid` instead of
    # the `configuration.`-prefixed form — read_wifi_master_facts must too.
    raw = ' 0    name=wifi1 ssid="Bare" hide-ssid=yes\n 1    name=wifi2 ssid="Bare2" hide-ssid=no\n'
    facts = revert.read_wifi_master_facts(raw)
    assert facts.wifi1_ssid == "Bare"
    assert facts.wifi1_hidden is True


def test_read_wifi_master_facts_none_when_a_master_is_missing():
    raw = ' 0    name=wifi1 configuration.ssid="X" configuration.hide-ssid=no\n'
    assert revert.read_wifi_master_facts(raw) is None


def test_read_wifi_master_facts_none_when_a_field_is_missing():
    raw = (
        ' 0    name=wifi1 configuration.ssid="X" configuration.hide-ssid=no\n'
        ' 1    name=wifi2 configuration.ssid="Y"\n'  # hide-ssid absent
    )
    assert revert.read_wifi_master_facts(raw) is None


def test_read_wifi_master_facts_none_on_empty_output():
    assert revert.read_wifi_master_facts("") is None


# ─── TAG_TABLES registry ─────────────────────────────────────────────────────

def test_tag_tables_declared_in_removal_step_order():
    steps = [t.step for t in revert.TAG_TABLES]
    assert steps == sorted(steps)


def test_tag_tables_dhcp_references_first_matches_template_order():
    # Mirrors 02-dhcp.rsc.j2's own idempotent remove order exactly.
    ids = [t.id for t in revert.TAG_TABLES if t.step == 6]
    assert ids == ["dhcp-lease", "dhcp-network", "dhcp-server", "pool", "address"]


def test_tag_tables_wifi_references_first_matches_template_order():
    # Mirrors 04-wifi.rsc.j2's own idempotent remove order exactly.
    ids = [t.id for t in revert.TAG_TABLES if t.step == 8]
    assert ids == ["bridge-port", "wifi", "wifi-security"]


def test_tag_table_remove_script_uses_broad_lankit_match():
    table = next(t for t in revert.TAG_TABLES if t.id == "mangle")
    # Broad ^lankit: match, not a template's own narrower per-origin prefix
    # (e.g. 03-firewall's ^lankit:fw: or 07-bandwidth's ^lankit:bw:) — the
    # universal remove-pass must sweep a table regardless of which
    # template originally wrote a given row into it.
    assert table.remove_script == (
        ':foreach i in=[/ip firewall mangle find where comment~"^lankit:"] '
        'do={ /ip firewall mangle remove $i }'
    )


# ─── build_remove_pass ───────────────────────────────────────────────────────

def test_remove_pass_is_brace_balanced():
    text = revert.build_remove_pass("ether2")
    assert text.count("{") == text.count("}")


def test_remove_pass_every_removable_table_wrapped_in_do_on_error():
    text = revert.build_remove_pass("ether2")
    for table in revert.TAG_TABLES:
        if not table.removable:
            continue
        assert f':do {{ {table.remove_script} }} ' in text
        assert f'lankit-revert: {table.id} remove failed' in text


def test_remove_pass_scheduler_removed_by_exact_name_not_comment():
    text = revert.build_remove_pass("ether2")
    assert '/system scheduler remove [find name="lankit-failsafe"]' in text
    # Never a broad comment match against user-created schedulers — the
    # scheduler registry row is read-only (G1.11 review finding: it exists
    # so verification can SEE lankit scheduler tags, not remove them).
    scheduler = next(t for t in revert.TAG_TABLES if t.id == "scheduler")
    assert scheduler.removable is False
    assert scheduler.remove_script not in text
    assert '/system scheduler find where comment' not in text


def test_scheduler_registry_row_is_readable_for_verification():
    # Design §5.1 row 1: verification must be able to see a lingering
    # lankit-failsafe scheduler (or the durable fired marker).
    scheduler = next(t for t in revert.TAG_TABLES if t.id == "scheduler")
    assert scheduler.print_cmd == "/system scheduler print detail without-paging"


def test_remove_pass_vlan_filtering_disabled_before_any_bridge_vlan_removal():
    text = revert.build_remove_pass("ether2")
    anchor_pos = text.index("vlan-filtering=no")
    bridge_vlan_remove_pos = text.index(
        ':do { :foreach i in=[/interface bridge vlan find'
    )
    assert anchor_pos < bridge_vlan_remove_pos


def test_remove_pass_filter_removed_before_address_list():
    # Design §5.2: fail-open — default-deny disappears before address lists,
    # erring toward availability mid-revert.
    text = revert.build_remove_pass("ether2")
    filter_pos = text.index('/ip firewall filter find where comment~"^lankit:"')
    addr_list_pos = text.index('/ip firewall address-list find where comment~"^lankit:"')
    assert filter_pos < addr_list_pos


def test_remove_pass_lan_interface_pvid_restored_to_one():
    text = revert.build_remove_pass("ether5")
    assert "/interface bridge port set [find where interface=ether5] pvid=1" in text


def test_remove_pass_ether3_5_pvid_restore_present():
    text = revert.build_remove_pass("ether2")
    assert '{"ether3";"ether4";"ether5"}' in text
    assert "pvid=1" in text


def test_remove_pass_master_wifi_omitted_without_facts_but_warns():
    text = revert.build_remove_pass("ether2", master_wifi_facts=None)
    assert "wifi1" not in text.split("Step 12")[-1] or "configuration.ssid" not in text
    assert "master-wifi restore skipped" in text


def test_remove_pass_master_wifi_restored_when_facts_given():
    facts = revert.MasterWifiFacts(
        wifi1_ssid="MikroTik-5G", wifi1_hidden=False,
        wifi2_ssid="MikroTik-2G", wifi2_hidden=False,
    )
    text = revert.build_remove_pass("ether2", master_wifi_facts=facts)
    assert 'set wifi1 configuration.ssid="MikroTik-5G" configuration.hide-ssid=no' in text
    assert 'set wifi2 configuration.ssid="MikroTik-2G" configuration.hide-ssid=no' in text
    assert "master-wifi restore skipped" not in text


def test_remove_pass_fasttrack_reenable_only_when_requested():
    without = revert.build_remove_pass("ether2", fasttrack_reenable=False)
    assert "defconf: fasttrack" not in without
    with_it = revert.build_remove_pass("ether2", fasttrack_reenable=True)
    assert 'enable [find where comment="defconf: fasttrack" && disabled=yes]' in with_it
    # It must be an enable, never a remove/delete of the defconf rule (§11),
    # and it must be error-wrapped like the rest of the remove-pass.
    assert (
        ':do { /ip firewall filter enable '
        '[find where comment="defconf: fasttrack" && disabled=yes] '
        '} on-error={ :log warning "lankit-revert: fasttrack re-enable failed" }'
    ) in with_it


def test_bundle_propagates_fasttrack_reenable():
    bundle = revert.build_revert_bundle(None, "ether2", fasttrack_reenable=True)
    assert 'enable [find where comment="defconf: fasttrack" && disabled=yes]' in bundle


# ─── Known-good archive store ────────────────────────────────────────────────

def _sample_scripts():
    return {
        "01-vlans.rsc": 'add name=vlan-trusted comment="lankit:vlan:trusted:interface"\n',
        "03-firewall.rsc": 'add comment="lankit:fw:all:default-deny"\n',
    }


def test_save_and_load_known_good_round_trip():
    aid = revert.save_known_good(
        ROUTER, _sample_scripts(),
        segments_applied=["trusted"], globals_applied=True,
        network_yml_hash="abc123", lan_interface="ether2",
    )
    archive = revert.load_known_good(ROUTER)
    assert archive is not None
    assert archive.manifest.archive_id == aid
    assert archive.manifest.segments_applied == ["trusted"]
    assert archive.manifest.globals_applied is True
    assert archive.manifest.lan_interface == "ether2"
    assert set(archive.scripts) == {"01-vlans.rsc", "03-firewall.rsc"}
    assert archive.scripts["01-vlans.rsc"] == _sample_scripts()["01-vlans.rsc"]


def test_manifest_expected_tags_derived_from_archived_text():
    revert.save_known_good(
        ROUTER, _sample_scripts(),
        segments_applied=["trusted"], globals_applied=True,
        network_yml_hash="abc123", lan_interface="ether2",
    )
    archive = revert.load_known_good(ROUTER)
    assert set(archive.manifest.expected_tags) == {
        "lankit:vlan:trusted:interface",
        "lankit:fw:all:default-deny",
    }


def test_manifest_is_json_round_trippable_on_disk():
    revert.save_known_good(
        ROUTER, _sample_scripts(),
        segments_applied=["trusted"], globals_applied=True,
        network_yml_hash="abc123", lan_interface="ether2",
    )
    router_dir = revert._router_dir(ROUTER)
    archive_id = revert.list_known_good_ids(ROUTER)[0]
    manifest_path = router_dir / archive_id / "manifest.json"
    data = json.loads(manifest_path.read_text())
    assert data["router_ip"] == ROUTER
    assert data["network_yml_hash"] == "abc123"


def test_load_known_good_returns_none_when_absent():
    assert revert.load_known_good("10.0.0.99") is None


def test_load_known_good_defaults_to_most_recent():
    revert.save_known_good(
        ROUTER, {"01-vlans.rsc": "first\n"},
        segments_applied=["trusted"], globals_applied=True,
        network_yml_hash="h1", lan_interface="ether2",
    )
    revert.save_known_good(
        ROUTER, {"01-vlans.rsc": "second\n"},
        segments_applied=["trusted"], globals_applied=True,
        network_yml_hash="h2", lan_interface="ether2",
    )
    latest = revert.load_known_good(ROUTER)
    assert latest.scripts["01-vlans.rsc"] == "second\n"
    assert latest.manifest.network_yml_hash == "h2"


def test_load_known_good_by_explicit_id():
    aid1 = revert.save_known_good(
        ROUTER, {"01-vlans.rsc": "first\n"},
        segments_applied=["trusted"], globals_applied=True,
        network_yml_hash="h1", lan_interface="ether2",
    )
    revert.save_known_good(
        ROUTER, {"01-vlans.rsc": "second\n"},
        segments_applied=["trusted"], globals_applied=True,
        network_yml_hash="h2", lan_interface="ether2",
    )
    archive = revert.load_known_good(ROUTER, archive_id=aid1)
    assert archive.manifest.network_yml_hash == "h1"


def test_delete_known_good_is_safe_when_absent():
    revert.delete_known_good(ROUTER, "nonexistent-id")  # must not raise


def test_sweep_orphaned_archives_deletes_unreferenced():
    aid1 = revert.save_known_good(
        ROUTER, {"01-vlans.rsc": "a\n"},
        segments_applied=["trusted"], globals_applied=True,
        network_yml_hash="h1", lan_interface="ether2",
    )
    aid2 = revert.save_known_good(
        ROUTER, {"01-vlans.rsc": "b\n"},
        segments_applied=["trusted"], globals_applied=True,
        network_yml_hash="h2", lan_interface="ether2",
    )
    deleted = revert.sweep_orphaned_archives(ROUTER, still_paired_ids={aid2})
    assert deleted == [aid1]
    assert revert.list_known_good_ids(ROUTER) == [aid2]


def test_sweep_never_deletes_most_recent_archive_even_when_unpaired():
    # G1.11 review finding: the migration bootstrap saves an archive without
    # pairing it to any snapshot — a subsequent index write must not sweep
    # the live revert target out from under the failsafe.
    aid = revert.save_known_good(
        ROUTER, {"01-vlans.rsc": "a\n"},
        segments_applied=["trusted"], globals_applied=True,
        network_yml_hash="h1", lan_interface="ether2",
    )
    deleted = revert.sweep_orphaned_archives(ROUTER, still_paired_ids=set())
    assert deleted == []
    assert revert.list_known_good_ids(ROUTER) == [aid]


def test_sweep_still_deletes_older_orphans_while_protecting_latest():
    import time
    aid1 = revert.save_known_good(
        ROUTER, {"01-vlans.rsc": "a\n"},
        segments_applied=["trusted"], globals_applied=True,
        network_yml_hash="h1", lan_interface="ether2",
    )
    time.sleep(0.01)  # distinct microsecond timestamps -> distinct ids
    aid2 = revert.save_known_good(
        ROUTER, {"01-vlans.rsc": "b\n"},
        segments_applied=["trusted"], globals_applied=True,
        network_yml_hash="h2", lan_interface="ether2",
    )
    deleted = revert.sweep_orphaned_archives(ROUTER, still_paired_ids=set())
    assert deleted == [aid1]
    assert revert.list_known_good_ids(ROUTER) == [aid2]


def test_archive_directory_permissions_are_owner_only():
    revert.save_known_good(
        ROUTER, _sample_scripts(),
        segments_applied=["trusted"], globals_applied=True,
        network_yml_hash="abc123", lan_interface="ether2",
    )
    router_dir = revert._router_dir(ROUTER)
    archive_dir = next(router_dir.iterdir())
    assert oct(router_dir.stat().st_mode)[-3:] == "700"
    assert oct(archive_dir.stat().st_mode)[-3:] == "700"
    for f in archive_dir.iterdir():
        assert oct(f.stat().st_mode)[-3:] == "600"


# ─── build_revert_bundle / expected_tags / verify_revert ────────────────────

def test_first_apply_bundle_is_remove_only():
    bundle = revert.build_revert_bundle(None, "ether2")
    assert "Rebuild" not in bundle
    assert "lankit-revert: remove-only revert complete" in bundle
    assert revert.expected_tags(None) == frozenset()


def test_bundle_with_archive_appends_rebuild_in_apply_order():
    revert.save_known_good(
        ROUTER, _sample_scripts(),
        segments_applied=["trusted"], globals_applied=True,
        network_yml_hash="abc123", lan_interface="ether2",
    )
    archive = revert.load_known_good(ROUTER)
    bundle = revert.build_revert_bundle(archive, "ether2")

    vlans_pos = bundle.index("01-vlans.rsc")
    firewall_pos = bundle.index("03-firewall.rsc")
    assert vlans_pos < firewall_pos
    assert "lankit-revert: complete" in bundle
    assert bundle.count("{") == bundle.count("}")


def test_bundle_removes_precede_adds_shape():
    # Re-runnability (design §5.5): the remove-pass (all "remove"/"find")
    # must appear before any "add" from the rebuild payload.
    revert.save_known_good(
        ROUTER, _sample_scripts(),
        segments_applied=["trusted"], globals_applied=True,
        network_yml_hash="abc123", lan_interface="ether2",
    )
    archive = revert.load_known_good(ROUTER)
    bundle = revert.build_revert_bundle(archive, "ether2")
    last_remove = bundle.rindex(" remove ")
    first_add = bundle.index("add name=vlan-trusted")
    assert last_remove < first_add


def test_expected_tags_matches_manifest():
    revert.save_known_good(
        ROUTER, _sample_scripts(),
        segments_applied=["trusted"], globals_applied=True,
        network_yml_hash="abc123", lan_interface="ether2",
    )
    archive = revert.load_known_good(ROUTER)
    assert revert.expected_tags(archive) == frozenset(archive.manifest.expected_tags)


def test_verify_revert_ok_when_live_matches_expected():
    verdict = revert.verify_revert(
        live_tags={"lankit:vlan:trusted:interface"},
        expected_tags={"lankit:vlan:trusted:interface"},
    )
    assert verdict.ok
    assert not verdict.missing
    assert not verdict.unexpected


def test_verify_revert_flags_missing():
    verdict = revert.verify_revert(live_tags=set(), expected_tags={"lankit:vlan:trusted:interface"})
    assert not verdict.ok
    assert verdict.missing == frozenset({"lankit:vlan:trusted:interface"})


def test_verify_revert_flags_unexpected_lankit_tag():
    verdict = revert.verify_revert(
        live_tags={"lankit:vlan:stale:interface"}, expected_tags=set(),
    )
    assert not verdict.ok
    assert verdict.unexpected == frozenset({"lankit:vlan:stale:interface"})


def test_verify_revert_ignores_durable_fired_marker():
    # The lankit:failsafe-fired:* marker is deliberately durable — cleared
    # only by the next kept apply, never by a revert (design §7.2 sign-off
    # amendment). Its presence must not fail an otherwise-clean revert.
    for marker in ("lankit:failsafe-fired:ok", "lankit:failsafe-fired:error"):
        verdict = revert.verify_revert(live_tags={marker}, expected_tags=set())
        assert verdict.ok


def test_verify_revert_still_flags_lingering_failsafe_scheduler():
    # But an ACTIVE lankit:failsafe scheduler tag (comment on the armed
    # one-shot) lingering after a revert is a real problem — flagged.
    verdict = revert.verify_revert(live_tags={"lankit:failsafe"}, expected_tags=set())
    assert not verdict.ok
    assert "lankit:failsafe" in verdict.unexpected


def test_verify_revert_flags_legacy_kit_prefix_as_unexpected():
    # A revert that leaves pre-rename kit:-prefixed rows behind hasn't
    # fully converged, even though current templates only emit lankit:.
    verdict = revert.verify_revert(live_tags={"kit:fw:iot:isolation"}, expected_tags=set())
    assert not verdict.ok
    assert "kit:fw:iot:isolation" in verdict.unexpected


# ─── expected_tags_for_config (probe.py's migration target) ─────────────────

def test_expected_tags_for_config_matches_actual_template_tags(cfg):
    tags = revert.expected_tags_for_config(cfg)
    # Spot-check a representative tag from each template, cross-checked
    # against the same fixture generator.py's own tests already exercise.
    assert "lankit:vlan:trusted:interface" in tags
    assert "lankit:fw:trusted>iot:permit" in tags
    assert "lankit:fw:all:default-deny" in tags
    assert "lankit:bw:iot:up" in tags
    assert "lankit:dns:trusted:gateway" in tags


def test_expected_tags_for_config_matches_render_texts_directly(cfg):
    # No double standard between the two ways of asking "what tags exist
    # in a full render" — one scans generate_all's files, the other calls
    # render_texts() directly and scans the strings.
    texts = render_texts(cfg, segment_filter=None, include_globals=True)
    manual = set()
    import re
    for text in texts.values():
        manual.update(re.findall(r'comment="(lankit:[^"]+)"', text))
    assert set(revert.expected_tags_for_config(cfg)) == manual
