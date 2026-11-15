"""
lankit.core.revert
~~~~~~~~~~~~~~~~~~~

Tag-scoped revert mechanism (G1.2). Pure generation and verification —
no router I/O. Design: docs/design/tag-scoped-revert.md (signed off
2026-07-07, four amendments incorporated below).

Replaces additive `/export` snapshot import (which cannot remove resources
and aborts on the first duplicate) with a two-part revert bundle:

  1. A universal remove-pass, generated from the canonical TAG_TABLES
     registry below, that strips every `lankit:`-tagged resource in a
     dependency-safe order and restores the small set of untagged `set`
     mutations lankit makes (vlan-filtering, DNS-server port PVID, master
     WiFi hide/rename).
  2. A rebuild payload: the archived last-known-good rendered script set
     (see the known-good archive store below), concatenated in normal
     apply order. Every generated script is remove-then-add idempotent,
     so importing it converges lankit-owned state to that script set
     regardless of prior state.

Wiring this into apply.py/rollback.py/restore.py/test_failsafe.py is out
of scope here (G1.3/G1.4/G1.9) — this module only builds the artifacts
and the verification primitives those callers will use.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from lankit.core.config import Config

# ─── RouterOS output parsing ────────────────────────────────────────────────
#
# RouterOS 7's `print detail` output renders comments as a separate
# ';;; text' line, not an inline comment="text" field — confirmed against
# real hardware in lankit/cli/commands/audit.py, whose parser this is
# ported from. probe.py's original parser assumed an inline comment="..."
# field and would have under-read (or missed) tags on real RouterOS 7
# output; this is the corrected, single source of truth both probe.py and
# every future caller (apply/rollback/restore) should use for reading live
# tags. Scanning our OWN generated .rsc text (e.g. to derive expected_tags
# from an archive) is a different, simpler problem — see _scan_own_text
# below — because we control that format exactly.

_FIELD_RE = re.compile(r'([\w.-]+)=(?:"([^"]*)"|([\S]+))')
_LANKIT_TAG_RE = re.compile(r'^lankit:')


def _fields(text: str) -> dict[str, str]:
    """Extract all field=value pairs from a RouterOS record block."""
    result: dict[str, str] = {}
    for m in _FIELD_RE.finditer(text):
        key = m.group(1)
        val = m.group(2) if m.group(2) is not None else m.group(3)
        result[key] = val
    return result


def _split_records(output: str) -> list[dict[str, str]]:
    """Split RouterOS 'print detail without-paging' output into per-record
    field dicts, injecting the ';;; text' comment line as a synthetic
    comment= field. Ported from audit.py's _split_records (proven against
    real hardware) so revert.py's verification and probe.py's audit share
    one parser instead of two, one of which was wrong.
    """
    lines = output.splitlines()
    records: list[dict[str, str]] = []
    current: list[str] = []
    current_comment: Optional[str] = None

    def _flush() -> None:
        nonlocal current_comment
        if current:
            f = _fields(" ".join(current))
            if current_comment is not None:
                f["comment"] = current_comment
            if f:
                records.append(f)
        current.clear()
        current_comment = None

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("Flags:") or stripped.startswith("Columns:") \
                or stripped.startswith("#") or not stripped:
            _flush()
            continue
        if ";;;" in line:
            if re.match(r"^\s{0,6}\d+[\s]", line):
                _flush()
            comment_text = line[line.index(";;;") + 3:].strip()
            current_comment = comment_text
            line = line[:line.index(";;;")].rstrip()
            if line.strip():
                current.append(line)
            continue
        if re.match(r"^\s{0,6}\d+\s", line) or re.match(r"^\s{0,6}\d+$", line):
            _flush()
            current.append(line)
        else:
            current.append(line)

    _flush()
    return records


def read_wifi_master_facts(raw_wifi_output: str) -> Optional["MasterWifiFacts"]:
    """Parse wifi1/wifi2's live SSID and hidden state from
    '/interface wifi print detail without-paging' output.

    Returns None if either master record, or any of its four needed
    fields, can't be found — design §5.3's fail-open: the caller should
    then build the remove-pass with master_wifi_facts=None (masters stay
    hidden, wired recovery on 192.168.88.0/24 is unaffected) rather than
    guess at a restore value. Checks both `configuration.ssid`/
    `configuration.hide-ssid` and the bare `ssid`/`hide-ssid` field names,
    matching audit.py's own hedge (`_audit_wifi`) about which form RouterOS
    actually emits — unconfirmed without hardware, see G1.13.
    """
    by_name = {r.get("name"): r for r in _split_records(raw_wifi_output)}
    w1, w2 = by_name.get("wifi1"), by_name.get("wifi2")
    if w1 is None or w2 is None:
        return None

    def _ssid(r: dict[str, str]) -> Optional[str]:
        return r.get("configuration.ssid") or r.get("ssid")

    def _hidden(r: dict[str, str]) -> Optional[bool]:
        v = r.get("configuration.hide-ssid") or r.get("hide-ssid")
        return None if v is None else v == "yes"

    ssid1, ssid2 = _ssid(w1), _ssid(w2)
    hidden1, hidden2 = _hidden(w1), _hidden(w2)
    if ssid1 is None or ssid2 is None or hidden1 is None or hidden2 is None:
        return None
    return MasterWifiFacts(wifi1_ssid=ssid1, wifi1_hidden=hidden1, wifi2_ssid=ssid2, wifi2_hidden=hidden2)


def read_live_tags(raw_outputs: dict[str, str]) -> set[str]:
    """Parse raw 'print detail without-paging' output into the set of every
    lankit:/kit:-prefixed comment tag found.

    raw_outputs: {table_id: stdout_text}, one entry per TAG_TABLES id whose
    print command the caller successfully ran. Callers should omit entries
    for commands that errored (e.g. /interface wifi on a device without the
    wifi package) rather than pass empty/error text — matching probe.py's
    existing "skip on stderr" convention.

    Pure function: does no I/O. Callers (probe.py today; apply/rollback/
    restore in G1.3/G1.4) are responsible for running TAG_TABLES[*].print_cmd
    over their own connection and building this dict.
    """
    found: set[str] = set()
    for text in raw_outputs.values():
        for rec in _split_records(text):
            comment = rec.get("comment", "")
            if _LANKIT_TAG_RE.match(comment):
                found.add(comment)
    return found


# ─── Canonical tag-table registry (design §5.1) ─────────────────────────────
#
# Every RouterOS table lankit writes lankit:-tagged resources into, in
# dependency-safe removal order (design §5.2, steps 3-11 — step 1 is the
# scheduler, matched by name not comment; step 2 is the vlan-filtering
# anchor, a `set` not a table; step 12 is the untagged-mutation restore.
# Both are handled directly in build_remove_pass, not as registry rows).
#
# Cross-checked line-by-line against all seven ansible/roles/router/
# templates/*.rsc.j2 during G1.1a sign-off review — every comment shape
# and "written by" attribution below was independently verified, not just
# carried over from the design doc.

@dataclass(frozen=True)
class TagTable:
    id: str            # short id, used as the raw_outputs dict key
    path: str           # RouterOS command path
    step: int           # removal-order position (ascending = removed first)
    removable: bool = True  # False = read-only registry row: read_lankit_tags
                            # reads it for verification, but build_remove_pass
                            # never emits its broad comment-match remove.
                            # Currently only /system scheduler, where lankit
                            # removes by exact name — never by comment, which
                            # could match user-created schedulers (design §5.2
                            # step 1; G1.11 review finding).

    @property
    def print_cmd(self) -> str:
        return f"{self.path} print detail without-paging"

    @property
    def remove_script(self) -> str:
        """The :foreach ... remove RouterOS script for this table, full-removal mode."""
        return (
            f':foreach i in=[{self.path} find where comment~"^lankit:"] '
            f'do={{ {self.path} remove $i }}'
        )


TAG_TABLES: tuple[TagTable, ...] = (
    TagTable("scheduler",    "/system scheduler",            1, removable=False),
    TagTable("filter",       "/ip firewall filter",         3),
    TagTable("nat",          "/ip firewall nat",             4),
    TagTable("mangle",       "/ip firewall mangle",          4),
    TagTable("address-list", "/ip firewall address-list",    4),
    TagTable("queue-tree",   "/queue tree",                  5),
    TagTable("dhcp-lease",   "/ip dhcp-server lease",        6),
    TagTable("dhcp-network", "/ip dhcp-server network",      6),
    TagTable("dhcp-server",  "/ip dhcp-server",               6),
    TagTable("pool",         "/ip pool",                      6),
    TagTable("address",      "/ip address",                   6),
    TagTable("dns-static",   "/ip dns static",                7),
    TagTable("bridge-port",  "/interface bridge port",       8),
    TagTable("wifi",         "/interface wifi",              8),
    TagTable("wifi-security", "/interface wifi security",    8),
    TagTable("list-member",  "/interface list member",       9),
    TagTable("bridge-vlan",  "/interface bridge vlan",       10),
    TagTable("vlan",         "/interface vlan",              11),
)

# Sanity: TAG_TABLES must already be in ascending step order — build_remove_pass
# relies on iteration order, not on re-sorting, to keep the emitted script
# simple to read. Within a step, DHCP and WiFi sub-orders are references-first
# (mirrors each template's own idempotent remove order, verified against
# 02-dhcp.rsc.j2 and 04-wifi.rsc.j2 respectively).
assert list(t.step for t in TAG_TABLES) == sorted(t.step for t in TAG_TABLES), \
    "TAG_TABLES must be declared in removal-step order"

_FASTTRACK_COMMENT = "defconf: fasttrack"


# ─── Remove-pass generation (design §5.2, §5.3) ─────────────────────────────

@dataclass(frozen=True)
class MasterWifiFacts:
    """Live wifi1/wifi2 master state, captured by the caller before it is
    overwritten by an apply's 04-wifi global block. Passed into
    build_remove_pass so a revert can restore what the masters looked like
    before lankit touched them (or before this apply, on later applies —
    see design §5.3: because the hidden-master transformation is a
    constant, capturing "current live state" always captures the correct
    restore target, whether that's the true original SSID on a first apply
    or the already-lankit-hidden value on a later one)."""
    wifi1_ssid: str
    wifi1_hidden: bool
    wifi2_ssid: str
    wifi2_hidden: bool


def build_remove_pass(
    lan_interface: str,
    master_wifi_facts: Optional[MasterWifiFacts] = None,
    fasttrack_reenable: bool = False,
) -> str:
    """Build the universal remove-pass: strips every lankit:-tagged resource
    and restores lankit's untagged `set` mutations. Running this alone
    converges the router to "defconf plus whatever the operator did
    manually" — the flat 192.168.88.0/24 management network survives at
    every intermediate step (design §5.4).

    lan_interface: cfg.router.lan_interface. Always passed — the DNS-server
    port PVID restore is find-guarded and is a harmless no-op if that port
    was never touched (e.g. no DNS server host configured).

    master_wifi_facts: if None, the master-WiFi restore block is omitted
    with a warning comment — masters stay hidden, wired recovery is
    unaffected (design §5.3's stated fail-open behavior).

    fasttrack_reenable: pass True when the applied state includes the
    scripted defconf-fasttrack disable (03-firewall.rsc, G3.7). The rule
    is untagged by design (§5.3/§11), so the remove-pass re-enables it
    here instead of the tag registry handling it.
    """
    lines: list[str] = [
        "# lankit-revert: universal remove-pass",
        "# Generated by lankit — see docs/design/tag-scoped-revert.md",
        "",
        "# ── Step 1: disarm any pending failsafe scheduler ───────────────",
        "# No-op when fired by the failsafe itself (its on-event already",
        "# self-deleted first) — needed when this bundle runs via rollback,",
        "# restore, or a manual /import so a pending failsafe can't also fire.",
        ':do { /system scheduler remove [find name="lankit-failsafe"] } '
        'on-error={ :log warning "lankit-revert: scheduler remove failed" }',
        "",
        "# ── Step 2: management-path anchor ──────────────────────────────",
        "# Disable VLAN filtering before touching any bridge-vlan row. From",
        "# this point the bridge forwards flat — no later removal step can",
        "# partition an untagged port from the CPU. Availability over",
        "# isolation mid-revert (see design §5.4, open question Q1).",
        ':do { /interface bridge set bridge vlan-filtering=no } '
        'on-error={ :log warning "lankit-revert: vlan-filtering disable failed" }',
        "",
    ]

    step_labels = {
        3: "firewall filter (fail-open: default-deny goes first)",
        4: "firewall nat/mangle/address-list",
        5: "bandwidth queue tree",
        6: "DHCP chain (references-first)",
        7: "DNS static entries",
        8: "WiFi (references-first: bridge port -> AP -> security)",
        9: "interface list membership",
        10: "bridge VLAN table",
        11: "VLAN interfaces",
    }
    seen_steps: set[int] = set()
    for table in TAG_TABLES:
        if not table.removable:
            # Read-only registry rows (the scheduler) are verification-only:
            # the scheduler is removed by exact name in Step 1 above, never
            # by a broad comment match that could hit user schedulers.
            continue
        if table.step not in seen_steps:
            seen_steps.add(table.step)
            lines.append(f"# ── Step {table.step}: {step_labels.get(table.step, '')} " + "─" * 4)
        lines.append(
            f':do {{ {table.remove_script} }} '
            f'on-error={{ :log warning "lankit-revert: {table.id} remove failed" }}'
        )
    lines.append("")

    lines.append("# ── Step 12: restore untagged set mutations (design §5.3) ──────")
    lines.append(
        f':do {{ /interface bridge port set [find where interface={lan_interface}] pvid=1 }} '
        f'on-error={{ :log warning "lankit-revert: dns-server-port pvid restore failed" }}'
    )
    lines.append(
        ':do { :foreach p in={"ether3";"ether4";"ether5"} do={ '
        '/interface bridge port set [find where interface=$p] pvid=1 } } '
        'on-error={ :log warning "lankit-revert: ether3-5 pvid restore failed" }'
    )

    if master_wifi_facts is not None:
        f = master_wifi_facts
        wifi1_hide = "yes" if f.wifi1_hidden else "no"
        wifi2_hide = "yes" if f.wifi2_hidden else "no"
        lines.append(
            ':do { /interface wifi set wifi1 '
            f'configuration.ssid="{f.wifi1_ssid}" '
            f'configuration.hide-ssid={wifi1_hide} '
            '} on-error={ :log warning "lankit-revert: wifi1 master restore failed" }'
        )
        lines.append(
            ':do { /interface wifi set wifi2 '
            f'configuration.ssid="{f.wifi2_ssid}" '
            f'configuration.hide-ssid={wifi2_hide} '
            '} on-error={ :log warning "lankit-revert: wifi2 master restore failed" }'
        )
    else:
        lines.append(
            "# Master WiFi facts were not captured — masters stay hidden as"
            " _master-*-noconnect. Wired recovery on 192.168.88.0/24 is unaffected."
        )
        lines.append(
            ':log warning "lankit-revert: master-wifi restore skipped (no facts captured)"'
        )

    if fasttrack_reenable:
        lines.append(
            ':do { /ip firewall filter enable '
            f'[find where comment="{_FASTTRACK_COMMENT}" && disabled=yes] '
            '} on-error={ :log warning "lankit-revert: fasttrack re-enable failed" }'
        )

    return "\n".join(lines) + "\n"


# ─── Known-good archive store (design §3) ───────────────────────────────────
#
# At each successful, kept apply, the rendered script set that now describes
# live lankit state is archived here — not a tag-filtered export, because
# exports omit sensitive values (WiFi passphrases) and can't see lankit's
# untagged `set` mutations. Rendered text is the artifact lankit already
# proves importable on every apply, in a known order.
#
# Sign-off amendment: retention is enforced, not just stated. Archives are
# retained only as long as a snapshot in snapshots.py's index still pairs
# to them; sweep_orphaned_archives() is called from snapshots.py's rotation
# path (see lankit/core/snapshots.py's _update_index).

_KNOWN_GOOD_DIR = Path.home() / ".lankit" / "known-good"
_TAG_IN_TEXT_RE = re.compile(r'comment="(lankit:[^"]+)"')


def _scan_own_text(text: str) -> set[str]:
    """Extract lankit: tags from text WE generated (not live router output).

    Safe to use a simple comment="..." regex here because we control the
    exact rendering format (see the RouterOS output parsing note above for
    why this regex is NOT safe against live router output)."""
    return set(_TAG_IN_TEXT_RE.findall(text))


@dataclass(frozen=True)
class ArchiveManifest:
    archive_id: str
    router_ip: str
    created_at: str                # ISO8601 UTC
    network_yml_hash: str
    segments_applied: list[str]    # sorted
    globals_applied: bool
    expected_tags: list[str]       # sorted; derived from the archived scripts
    lan_interface: str             # needed by build_remove_pass's PVID restore


@dataclass(frozen=True)
class KnownGoodArchive:
    manifest: ArchiveManifest
    scripts: dict[str, str]        # output filename -> rendered text


def _router_dir(router_ip: str) -> Path:
    return _KNOWN_GOOD_DIR / router_ip.replace(".", "-")


def save_known_good(
    router_ip: str,
    scripts: dict[str, str],
    *,
    segments_applied: list[str],
    globals_applied: bool,
    network_yml_hash: str,
    lan_interface: str,
) -> str:
    """Archive a rendered script set as the new known-good state.

    scripts: {output_filename: text}, typically from generator.render_texts().
    Returns the archive id (its timestamp-derived directory name).
    """
    router_dir = _router_dir(router_ip)
    router_dir.mkdir(parents=True, exist_ok=True)
    router_dir.chmod(0o700)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    archive_dir = router_dir / ts
    archive_dir.mkdir(exist_ok=True)
    archive_dir.chmod(0o700)

    expected: set[str] = set()
    for name, text in scripts.items():
        script_path = archive_dir / name
        script_path.write_text(text)
        script_path.chmod(0o600)
        expected.update(_scan_own_text(text))

    manifest = ArchiveManifest(
        archive_id=ts,
        router_ip=router_ip,
        created_at=ts,
        network_yml_hash=network_yml_hash,
        segments_applied=sorted(segments_applied),
        globals_applied=globals_applied,
        expected_tags=sorted(expected),
        lan_interface=lan_interface,
    )
    manifest_path = archive_dir / "manifest.json"
    manifest_path.write_text(json.dumps(asdict(manifest), indent=2))
    manifest_path.chmod(0o600)
    return ts


def list_known_good_ids(router_ip: str) -> list[str]:
    """Archive ids for this router, oldest-first (lexicographic == chronological
    given the timestamp-derived id format)."""
    router_dir = _router_dir(router_ip)
    if not router_dir.exists():
        return []
    return sorted(p.name for p in router_dir.iterdir() if p.is_dir())


def load_known_good(router_ip: str, archive_id: Optional[str] = None) -> Optional[KnownGoodArchive]:
    """Load an archive by id, or the most recent one if archive_id is None.

    Returns None if no archive exists for this router (the first-apply
    signal — design §4: absence of the archive directory/manifest is what
    tells build_revert_bundle to emit a remove-only bundle)."""
    ids = list_known_good_ids(router_ip)
    if not ids:
        return None
    if archive_id is None:
        archive_id = ids[-1]
    elif archive_id not in ids:
        return None

    archive_dir = _router_dir(router_ip) / archive_id
    manifest_path = archive_dir / "manifest.json"
    if not manifest_path.exists():
        return None
    data = json.loads(manifest_path.read_text())
    manifest = ArchiveManifest(**data)

    scripts = {p.name: p.read_text() for p in sorted(archive_dir.glob("*.rsc"))}
    return KnownGoodArchive(manifest=manifest, scripts=scripts)


def delete_known_good(router_ip: str, archive_id: str) -> None:
    """Delete one archive. Safe to call when it's already absent."""
    archive_dir = _router_dir(router_ip) / archive_id
    if archive_dir.exists():
        shutil.rmtree(archive_dir)


def sweep_orphaned_archives(router_ip: str, still_paired_ids: set[str]) -> list[str]:
    """Delete every archive for this router NOT in still_paired_ids.

    Called from snapshots.py whenever its per-router index changes (save,
    rotation, or delete) with the set of archive ids the surviving index
    entries still reference. An archive with zero remaining references is
    unreachable — nothing can ever ask for it again — so it is removed
    immediately rather than left to accumulate (sign-off amendment: the
    retention invariant in design §8 needs enforcement, not just intent).
    Returns the ids actually deleted.

    The single most-recent archive is always protected, paired or not
    (G1.11 review finding): it is the live revert target the next apply's
    failsafe builds against, and it can legitimately be unpaired — right
    after apply's migration bootstrap records it, or after routine
    commit/rollback/restore snapshot saves rotate the one paired
    post-apply snapshot out of the 10-cap index. Deleting it would leave
    the next failsafe with only a remove-only revert (and destroy the one
    copy of the last known-good rendered state).
    """
    ids = list_known_good_ids(router_ip)
    protected = set(still_paired_ids)
    if ids:
        protected.add(ids[-1])
    deleted = []
    for archive_id in ids:
        if archive_id not in protected:
            delete_known_good(router_ip, archive_id)
            deleted.append(archive_id)
    return deleted


# ─── Revert bundle assembly and verification (design §4, §6, §7) ───────────

@dataclass(frozen=True)
class RevertVerdict:
    """Result of comparing live router tags against what a revert bundle
    should have produced. `ok` is the single source of truth every caller
    must check before printing a success mark (design §7.1's global
    invariant — no green checkmark over a silent partial failure)."""
    missing: frozenset[str]      # expected but absent — rebuild fell short
    unexpected: frozenset[str]   # present but not expected — remove-pass fell short

    @property
    def ok(self) -> bool:
        return not self.missing and not self.unexpected


_FIRED_MARKER_TAG_RE = re.compile(r"^lankit:failsafe-fired(:|$)")


def verify_revert(live_tags: set[str], expected_tags: set[str]) -> RevertVerdict:
    """Pure comparison — no I/O. Callers collect live_tags via
    read_live_tags() over their own connection.

    Only `lankit:` tags exist as far as this comparison is concerned:
    objects with any other comment are not lankit's and are never expected,
    removed, or reported.

    One deliberate exclusion: the durable failsafe-fired marker
    (`lankit:failsafe-fired:*`, a disabled scheduler entry) is ignored.
    It is left behind on purpose after a failsafe fires and must only be
    cleared by the next successful apply's keep path (design §7.2, sign-off
    amendment) — a revert must not fail verification because the marker
    that may have triggered it still exists."""
    missing = frozenset(expected_tags - live_tags)
    unexpected = frozenset(
        t for t in live_tags
        if _LANKIT_TAG_RE.match(t) and t not in expected_tags
        and not _FIRED_MARKER_TAG_RE.match(t)
    )
    return RevertVerdict(missing=missing, unexpected=unexpected)


_REBUILD_ORDER = [
    "01-vlans.rsc",
    "02-dhcp.rsc",
    "03-firewall.rsc",
    "04-wifi.rsc",
    "05-dns.rsc",
    "06-dns-redirect.rsc",
    "07-bandwidth.rsc",
]


def build_revert_bundle(
    archive: Optional[KnownGoodArchive],
    lan_interface: str,
    master_wifi_facts: Optional[MasterWifiFacts] = None,
    fasttrack_reenable: bool = False,
) -> str:
    """Build the complete lankit-revert.rsc: universal remove-pass, then
    (if archive is not None) the archived script set re-imported in normal
    apply order, then a terminal log marker used by callers with no user
    present (the failsafe path) to confirm the import ran to completion.

    archive is None (design §4, first apply): remove-pass only. Running
    it converges the router to defconf-plus-manual-state — the flat
    192.168.88.0/24 management network, matching what the rollback card
    already tells the household to fall back on.
    """
    parts = [build_remove_pass(lan_interface, master_wifi_facts, fasttrack_reenable)]

    if archive is None:
        parts.append('\n:log info "lankit-revert: remove-only revert complete"\n')
        return "".join(parts)

    parts.append("\n# ── Rebuild: last known-good state ──────────────────────────\n")
    for name in _REBUILD_ORDER:
        text = archive.scripts.get(name)
        if text is None:
            continue
        parts.append(f"\n# {name}\n")
        parts.append(text)
    parts.append('\n:log info "lankit-revert: complete"\n')
    return "".join(parts)


def expected_tags(archive: Optional[KnownGoodArchive]) -> frozenset[str]:
    """The tag set a successful revert should produce: empty for a
    remove-only revert (no archive), the manifest's stored set otherwise.

    Derived from the same object build_revert_bundle uses to build the
    payload, so the payload and its acceptance test cannot disagree
    (design §4)."""
    if archive is None:
        return frozenset()
    return frozenset(archive.manifest.expected_tags)


def expected_tags_for_config(cfg: Config, wifi_passphrases: Optional[dict[str, str]] = None) -> frozenset[str]:
    """The tag set a full apply of the CURRENT network.yml would produce —
    "what should be present right now" rather than "what a specific saved
    archive contains" (expected_tags above). Used by probe's config audit,
    which has no notion of a known-good archive; it just wants to compare
    live tags against the config on disk.

    Renders a full script set via generator.render_texts (no files written)
    and scans it the same way save_known_good does — this is scanning text
    lankit generated itself, not live router output, so the simple
    comment="..." regex is exactly right here (see the module docstring's
    note on why that's NOT true for read_live_tags)."""
    from lankit.core.generator import render_texts  # local: keeps generator off this module's top-level imports
    texts = render_texts(cfg, wifi_passphrases, segment_filter=None, include_globals=True)
    found: set[str] = set()
    for text in texts.values():
        found.update(_scan_own_text(text))
    return frozenset(found)
