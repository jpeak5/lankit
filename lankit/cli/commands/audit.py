"""
lankit audit — full live inventory audit with ok / missing / drifted / rogue status.

For every resource lankit manages (VLANs, DHCP, firewall, WiFi, hosts) this command
compares the live router state against what network.yml specifies and classifies
each item as:

  ok       — lankit: tag present and key field values match expected
  missing  — lankit expects it but the router has nothing
  drifted  — lankit: tag present but a key field was changed after apply
  rogue    — resource exists on the router with no lankit: tag at all
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import click
from lankit.cli.__main__ import cli

# ── RouterOS output parsing ───────────────────────────────────────────────────

_FIELD_RE = re.compile(r'([\w-]+)=(?:"([^"]*)"|([\S]+))')


def _fields(text: str) -> dict[str, str]:
    """Extract all field=value pairs from a RouterOS record block."""
    result = {}
    for m in _FIELD_RE.finditer(text):
        key = m.group(1)
        val = m.group(2) if m.group(2) is not None else m.group(3)
        result[key] = val
    return result


def _split_records(output: str) -> list[dict[str, str]]:
    """Split RouterOS 'print detail without-paging' output into per-record field dicts.

    RouterOS 7 renders comments as ';;; text' lines rather than comment="text"
    key-value pairs. We extract those and inject them as a synthetic comment= field.
    """
    lines = output.splitlines()
    records: list[dict[str, str]] = []
    current: list[str] = []
    current_comment: Optional[str] = None

    def _flush():
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
        # Skip Flags/Columns header lines and blank lines between records
        if stripped.startswith("Flags:") or stripped.startswith("Columns:") \
                or stripped.startswith("#") or not stripped:
            _flush()
            continue
        # RouterOS 7: comment rendered as ';;; text' (may be on same line as index)
        if ";;;" in line:
            # Could be " 0   ;;; comment text" (new record + comment on same line)
            if re.match(r"^\s{0,6}\d+[\s]", line):
                _flush()
            comment_text = line[line.index(";;;") + 3:].strip()
            current_comment = comment_text
            # Strip the ;;; part from the line before adding to current
            line = line[:line.index(";;;")].rstrip()
            if line.strip():
                current.append(line)
            continue
        # New record: line starts with optional whitespace then one or more digits
        if re.match(r"^\s{0,6}\d+\s", line) or re.match(r"^\s{0,6}\d+$", line):
            _flush()
            current.append(line)
        else:
            current.append(line)

    _flush()

    return records


# ── Audit result ──────────────────────────────────────────────────────────────

@dataclass
class AuditResult:
    name: str                   # human label (e.g. "trusted", "trusted→work")
    tag: Optional[str]          # expected lankit: tag (None for rogues)
    status: str                 # ok | missing | drifted | rogue
    detail: str                 # what's wrong, or "" if ok


def _status_fmt(status: str) -> str:
    return {
        "ok":      "[green]✓ ok[/green]",
        "missing": "[red]✗ missing[/red]",
        "drifted": "[yellow]~ drifted[/yellow]",
        "rogue":   "[magenta]! rogue[/magenta]",
    }.get(status, status)


# ── Per-section auditors ───────────────────────────────────────────────────────

def _audit_vlans(cfg, conn) -> list[AuditResult]:
    out, _ = conn.run_tolerant("/interface vlan print detail without-paging")
    records = _split_records(out)
    results = []

    for name, seg in cfg.segments.items():
        tag = f"lankit:vlan:{name}:interface"
        expected_fields = {
            "vlan-id": str(seg.vlan_id),
            "interface": "bridge",
        }
        match = _find_by_tag(records, tag)
        if match is None:
            results.append(AuditResult(name, tag, "missing", ""))
        else:
            drift = _check_drift(match, expected_fields)
            results.append(AuditResult(name, tag, "ok" if not drift else "drifted", drift))

    # Rogues: VLAN interfaces without any lankit: tag
    kit_tags = {f"lankit:vlan:{n}:interface" for n in cfg.segments}
    for rec in records:
        comment = rec.get("comment", "")
        if not comment.startswith("lankit:") or comment not in kit_tags:
            rogue_name = rec.get("name", "?")
            # Skip master interfaces (not created by lankit)
            if not rogue_name.startswith("_master"):
                results.append(AuditResult(
                    rogue_name, None, "rogue",
                    f"vlan-id={rec.get('vlan-id','?')} interface={rec.get('interface','?')}"
                ))

    return results


def _audit_ip_addresses(cfg, conn) -> list[AuditResult]:
    out, _ = conn.run_tolerant("/ip address print detail without-paging")
    records = _split_records(out)
    results = []

    for name, seg in cfg.segments.items():
        tag = f"lankit:dhcp:{name}:address"
        expected_fields = {
            "address": f"{seg.gateway}/24",
            "interface": f"vlan-{name}",
        }
        match = _find_by_tag(records, tag)
        if match is None:
            results.append(AuditResult(name, tag, "missing", ""))
        else:
            drift = _check_drift(match, expected_fields)
            results.append(AuditResult(name, tag, "ok" if not drift else "drifted", drift))

    # Rogues: IPs on vlan-* interfaces without lankit: tag
    kit_tags = {f"lankit:dhcp:{n}:address" for n in cfg.segments}
    for rec in records:
        comment = rec.get("comment", "")
        iface = rec.get("interface", "")
        if iface.startswith("vlan-") and (not comment.startswith("lankit:") or comment not in kit_tags):
            results.append(AuditResult(
                rec.get("address", "?"), None, "rogue",
                f"interface={iface}"
            ))

    return results


def _audit_dhcp_pools(cfg, conn) -> list[AuditResult]:
    out, _ = conn.run_tolerant("/ip pool print detail without-paging")
    records = _split_records(out)
    results = []

    for name, seg in cfg.segments.items():
        tag = f"lankit:dhcp:{name}:pool"
        expected_fields = {"ranges": seg.pool_range}
        match = _find_by_tag(records, tag)
        if match is None:
            results.append(AuditResult(name, tag, "missing", ""))
        else:
            drift = _check_drift(match, expected_fields)
            results.append(AuditResult(name, tag, "ok" if not drift else "drifted", drift))

    return results


def _audit_dhcp_servers(cfg, conn) -> list[AuditResult]:
    out, _ = conn.run_tolerant("/ip dhcp-server print detail without-paging")
    records = _split_records(out)
    results = []

    for name, seg in cfg.segments.items():
        tag = f"lankit:dhcp:{name}:server"
        expected_fields = {"interface": f"vlan-{name}"}
        match = _find_by_tag(records, tag)
        if match is None:
            results.append(AuditResult(name, tag, "missing", ""))
        else:
            drift = _check_drift(match, expected_fields)
            results.append(AuditResult(name, tag, "ok" if not drift else "drifted", drift))

    # Rogues: DHCP servers on vlan-* without lankit: tag
    kit_tags = {f"lankit:dhcp:{n}:server" for n in cfg.segments}
    for rec in records:
        comment = rec.get("comment", "")
        iface = rec.get("interface", "")
        if iface.startswith("vlan-") and (not comment.startswith("lankit:") or comment not in kit_tags):
            results.append(AuditResult(
                rec.get("name", "?"), None, "rogue",
                f"interface={iface}"
            ))

    return results


def _audit_dhcp_lease(cfg, conn) -> list[AuditResult]:
    """Check the static DNS server lease."""
    out, _ = conn.run_tolerant("/ip dhcp-server lease print detail without-paging")
    records = _split_records(out)
    tag = "lankit:dhcp:dns-server:lease"
    dns = cfg.hosts.get("dns_server")
    if not dns:
        return []

    expected_fields = {
        "address": dns.ip,
        "mac-address": dns.mac.upper(),
    }
    match = _find_by_tag(records, tag)
    if match is None:
        return [AuditResult("dns-server lease", tag, "missing", "")]

    # Normalise MAC case for comparison
    if "mac-address" in match:
        match = dict(match)
        match["mac-address"] = match["mac-address"].upper()

    drift = _check_drift(match, expected_fields)
    return [AuditResult("dns-server lease", tag, "ok" if not drift else "drifted", drift)]


def _audit_firewall(cfg, conn) -> list[AuditResult]:
    """
    Firewall audit: check tag presence for all expected filter rules.
    Rogue detection: forward-chain rules without lankit: comment.
    No field-level drift — rule positions are fragile and order-dependent.
    """
    out, _ = conn.run_tolerant("/ip firewall filter print detail without-paging")
    records = _split_records(out)
    live_tags = {rec.get("comment", "") for rec in records}

    results = []

    # Expected filter tags from config
    expected_filter_tags = _expected_filter_tags(cfg)
    for tag, label in expected_filter_tags:
        if tag in live_tags:
            results.append(AuditResult(label, tag, "ok", ""))
        else:
            results.append(AuditResult(label, tag, "missing", ""))

    # Rogues: forward-chain rules without any lankit: comment
    for rec in records:
        comment = rec.get("comment", "")
        chain = rec.get("chain", "")
        if chain == "forward" and not comment.startswith("lankit:") and not comment.startswith("defconf"):
            results.append(AuditResult(
                comment or "(no comment)", None, "rogue",
                f"chain={chain} action={rec.get('action','?')}"
            ))

    # Address lists
    out_al, _ = conn.run_tolerant("/ip firewall address-list print detail without-paging")
    records_al = _split_records(out_al)
    live_al_tags = {rec.get("comment", "") for rec in records_al}

    for name in cfg.segments:
        tag = f"lankit:fw:{name}:address-list"
        label = f"{name} address-list"
        if tag in live_al_tags:
            results.append(AuditResult(label, tag, "ok", ""))
        else:
            results.append(AuditResult(label, tag, "missing", ""))

    # Global address lists
    for tag, label in [
        ("lankit:fw:all:local-rfc1918", "all-local RFC1918"),
        ("lankit:fw:all:local-flat",    "all-local flat"),
    ]:
        status = "ok" if tag in live_al_tags else "missing"
        results.append(AuditResult(label, tag, status, ""))

    # Rogue address-list entries (on net-* or all-local without lankit: tag)
    kit_al_tags = {f"lankit:fw:{n}:address-list" for n in cfg.segments} | {
        "lankit:fw:all:local-rfc1918", "lankit:fw:all:local-flat"
    }
    for rec in records_al:
        comment = rec.get("comment", "")
        lst = rec.get("list", "")
        if (lst.startswith("net-") or lst == "all-local") and \
                (not comment.startswith("lankit:") or comment not in kit_al_tags):
            results.append(AuditResult(
                f"{lst}={rec.get('address','?')}", None, "rogue", f"list={lst}"
            ))

    return results


def _audit_wifi(cfg, conn) -> list[AuditResult]:
    out, err = conn.run_tolerant("/interface wifi print detail without-paging")
    if err:
        return []
    records = _split_records(out)
    results = []

    for name, seg in cfg.segments.items():
        if not seg.has_wifi:
            continue
        for band, tag_suffix in [("5ghz", "ap-5g"), ("2ghz", "ap-2g")]:
            if band not in seg.wifi_bands:
                continue
            tag = f"lankit:wifi:{name}:{tag_suffix}"
            match = _find_by_tag(records, tag)
            if match is None:
                results.append(AuditResult(f"{name} ({band})", tag, "missing", ""))
            else:
                # Check SSID — RouterOS stores it as configuration.ssid or ssid
                live_ssid = match.get("configuration.ssid") or match.get("ssid", "")
                if live_ssid and live_ssid != seg.ssid:
                    results.append(AuditResult(
                        f"{name} ({band})", tag, "drifted",
                        f"ssid: expected {seg.ssid!r}, got {live_ssid!r}"
                    ))
                else:
                    results.append(AuditResult(f"{name} ({band})", tag, "ok", ""))

    # Rogues: wifi interfaces that aren't masters and have no lankit: tag
    kit_tags = set()
    for name, seg in cfg.segments.items():
        if seg.has_wifi:
            for suffix in ("ap-5g", "ap-2g"):
                kit_tags.add(f"lankit:wifi:{name}:{suffix}")

    for rec in records:
        comment = rec.get("comment", "")
        iface_name = rec.get("name", "")
        if iface_name.startswith("_master") or iface_name in ("wifi1", "wifi2"):
            continue
        if not comment.startswith("lankit:") or comment not in kit_tags:
            results.append(AuditResult(
                iface_name, None, "rogue",
                f"ssid={rec.get('configuration.ssid') or rec.get('ssid','?')}"
            ))

    return results


def _audit_wifi_security(cfg, conn) -> list[AuditResult]:
    out, err = conn.run_tolerant("/interface wifi security print detail without-paging")
    if err:
        return []
    records = _split_records(out)
    results = []

    kit_tags = set()
    for name, seg in cfg.segments.items():
        if not seg.has_wifi:
            continue
        tag = f"lankit:wifi:{name}:security"
        kit_tags.add(tag)
        match = _find_by_tag(records, tag)
        if match is None:
            results.append(AuditResult(f"{name} security profile", tag, "missing", ""))
        else:
            results.append(AuditResult(f"{name} security profile", tag, "ok", ""))

    # Rogues: security profiles without a lankit: tag
    for rec in records:
        comment = rec.get("comment", "")
        if not comment.startswith("lankit:") or comment not in kit_tags:
            results.append(AuditResult(
                rec.get("name", "?"), None, "rogue",
                f"comment={comment!r}" if comment else "no comment"
            ))

    return results


# ── Helpers ───────────────────────────────────────────────────────────────────

def _find_by_tag(records: list[dict], tag: str) -> Optional[dict]:
    for rec in records:
        if rec.get("comment") == tag:
            return rec
    return None


def _check_drift(record: dict, expected: dict[str, str]) -> str:
    """Return a human-readable drift description, or '' if fields match."""
    diffs = []
    for field, exp_val in expected.items():
        live_val = record.get(field, "")
        if live_val != exp_val:
            diffs.append(f"{field}: expected {exp_val!r}, got {live_val!r}")
    return "; ".join(diffs)


def _expected_filter_tags(cfg) -> list[tuple[str, str]]:
    """Return (tag, label) pairs for all expected /ip firewall filter rules."""
    pairs = []

    for name, seg in cfg.segments.items():
        if seg.client_isolation:
            pairs.append((f"lankit:fw:{name}:isolation", f"{name} isolation"))
        if seg.dns != "none":
            pairs.append((f"lankit:fw:{name}>dns:permit-udp", f"{name}→dns UDP"))
            pairs.append((f"lankit:fw:{name}>dns:permit-tcp", f"{name}→dns TCP"))
        if seg.internet in ("full", "egress_only"):
            pairs.append((f"lankit:fw:{name}:egress", f"{name} egress"))

    for src, perm in cfg.permissions.items():
        for dst in perm.can_reach:
            pairs.append((f"lankit:fw:{src}>{dst}:permit", f"{src}→{dst}"))

    pairs += [
        ("lankit:fw:all:default-deny",        "default deny"),
        ("lankit:fw:all:fasttrack-internet",   "fasttrack internet"),
    ]
    return pairs


def _summary_counts(results: list[AuditResult]) -> tuple[int, int, int, int]:
    ok = sum(1 for r in results if r.status == "ok")
    missing = sum(1 for r in results if r.status == "missing")
    drifted = sum(1 for r in results if r.status == "drifted")
    rogue = sum(1 for r in results if r.status == "rogue")
    return ok, missing, drifted, rogue


# ── Command ───────────────────────────────────────────────────────────────────

@cli.command(name="audit")
@click.option("--config", "-c", "config_path", type=click.Path(), default=None,
              metavar="PATH", help="Path to network.yml")
@click.option("--section", "-s", type=click.Choice(
    ["vlans", "dhcp", "firewall", "wifi", "all"], case_sensitive=False),
    default="all", show_default=True, help="Limit to one section")
@click.option("--rogue-only", is_flag=True, default=False,
              help="Show only rogue entries")
@click.option("--problems", is_flag=True, default=False,
              help="Show only missing, drifted, and rogue entries")
def audit(config_path, section, rogue_only, problems):
    """Full live audit: compare router state against network.yml.

    Classifies every managed resource as:

    \b
      ✓ ok       — present and matches expected config
      ✗ missing  — lankit expects it but it's not on the router
      ~ drifted  — lankit: tag present, but a key field was changed
      ! rogue    — exists on the router with no lankit: tag

    Sections: vlans, dhcp (address/pool/server/lease), firewall, wifi.

    \b
    Examples:
      lankit audit
      lankit audit --section vlans
      lankit audit --problems
      lankit audit --rogue-only
    """
    from lankit.core.config import load, ConfigError
    from lankit.core.router import RouterConnection, RouterError
    from pathlib import Path
    from rich.console import Console
    from rich.table import Table
    from rich import box

    console = Console()

    try:
        cfg = load(Path(config_path) if config_path else None)
    except ConfigError as e:
        console.print(f"[bold red]Config error:[/bold red] {e}")
        raise SystemExit(1)

    console.print(f"\n[bold]Audit[/bold] — {cfg.household_name} — {cfg.router.ip}\n")

    try:
        with RouterConnection(cfg.router.ip, cfg.router.ssh_user, cfg.ssh_key) as conn:
            identity = conn.identity()
            version = conn.version()
            console.print(f"  [dim]{identity}  RouterOS {version}[/dim]\n")

            sections_to_run = {
                "vlans":    section in ("vlans", "all"),
                "dhcp":     section in ("dhcp", "all"),
                "firewall": section in ("firewall", "all"),
                "wifi":     section in ("wifi", "all"),
            }

            all_results: list[tuple[str, list[AuditResult]]] = []

            if sections_to_run["vlans"]:
                console.print("[dim]  Auditing VLANs...[/dim]")
                all_results.append(("VLANs", _audit_vlans(cfg, conn)))

            if sections_to_run["dhcp"]:
                console.print("[dim]  Auditing DHCP...[/dim]")
                dhcp_results = (
                    _audit_ip_addresses(cfg, conn)
                    + _audit_dhcp_pools(cfg, conn)
                    + _audit_dhcp_servers(cfg, conn)
                    + _audit_dhcp_lease(cfg, conn)
                )
                all_results.append(("DHCP", dhcp_results))

            if sections_to_run["firewall"]:
                console.print("[dim]  Auditing firewall...[/dim]")
                all_results.append(("Firewall", _audit_firewall(cfg, conn)))

            if sections_to_run["wifi"]:
                console.print("[dim]  Auditing WiFi...[/dim]")
                wifi_results = (
                    _audit_wifi(cfg, conn)
                    + _audit_wifi_security(cfg, conn)
                )
                all_results.append(("WiFi", wifi_results))

            console.print()

            # ── Render per-section tables ─────────────────────────────────────
            total_ok = total_missing = total_drifted = total_rogue = 0

            for sec_name, results in all_results:
                # Apply filters
                visible = results
                if rogue_only:
                    visible = [r for r in results if r.status == "rogue"]
                elif problems:
                    visible = [r for r in results if r.status != "ok"]

                ok, missing, drifted, rogue = _summary_counts(results)
                total_ok += ok
                total_missing += missing
                total_drifted += drifted
                total_rogue += rogue

                # Section header with counts
                counts_str = _counts_str(ok, missing, drifted, rogue, len(results))
                console.print(f"[bold]{sec_name}[/bold]  {counts_str}")

                if not visible:
                    console.print("  [dim](nothing to show)[/dim]\n")
                    continue

                table = Table(box=box.SIMPLE_HEAD, show_lines=False,
                              padding=(0, 1), show_header=True)
                table.add_column("Item", style="cyan", no_wrap=True)
                table.add_column("Status", no_wrap=True)
                table.add_column("Detail", style="dim")

                for r in visible:
                    table.add_row(r.name, _status_fmt(r.status), r.detail)

                console.print(table)
                console.print()

            # ── Overall summary ───────────────────────────────────────────────
            console.print("[dim]─────────────────────────────────────────────────────[/dim]")
            total = total_ok + total_missing + total_drifted + total_rogue
            console.print(
                f"  Total: {total}  "
                + _counts_str(total_ok, total_missing, total_drifted, total_rogue, total)
            )

            if total_missing or total_drifted:
                console.print("  Run [bold]lankit apply[/bold] to reconcile missing/drifted rules.")
            if total_rogue:
                console.print(
                    "  [magenta]Rogue entries[/magenta] were not created by lankit. "
                    "Review manually via WinBox or SSH."
                )
            console.print()

    except RouterError as e:
        console.print(f"[bold red]Router error:[/bold red] {e}")
        raise SystemExit(1)


def _counts_str(ok: int, missing: int, drifted: int, rogue: int, total: int) -> str:
    parts = []
    if ok:
        parts.append(f"[green]{ok} ok[/green]")
    if missing:
        parts.append(f"[red]{missing} missing[/red]")
    if drifted:
        parts.append(f"[yellow]{drifted} drifted[/yellow]")
    if rogue:
        parts.append(f"[magenta]{rogue} rogue[/magenta]")
    if not parts:
        return f"[dim]{total} items[/dim]"
    return "  ".join(parts)
