# ENH-010: `lankit audit` flags rogue entries with no actionable remediation path

**Persona(s):** Sam, Riley, Jordan
**Surface:** CLI — `lankit audit`
**Priority:** Medium

## Problem

When `lankit audit` finds rogue entries (router resources without a `kit:` tag), it prints:

```
! rogue   trusted-old   vlan-id=10 interface=bridge

Rogue entries were not created by lankit. Review manually via WinBox or SSH.
```

"Review manually via WinBox or SSH" is the only guidance. For Riley returning after a firmware update, for Jordan who doesn't have WinBox installed, and for Sam who wants to clean up efficiently from the CLI, this is a dead end. There is no `lankit audit --fix-rogues` or even the RouterOS command to run.

The audit correctly identifies the resource type and name — enough information to generate the removal command — but doesn't surface it.

## Proposed fix

For each rogue entry, show the RouterOS command that would remove it. This can be displayed with `--verbose` or in a `--remediate` output mode:

```
! rogue   trusted-old (vlan-id=10)
  To remove:  /interface vlan remove [find name=trusted-old]
```

For firewall rogues where safe removal is order-sensitive, show a warning instead of a command:
```
! rogue   (forward chain, no comment)
  Note: firewall rule removal is order-sensitive — review in WinBox or SSH
  before removing.
```

Additionally, add a `--export-fixes` flag that writes the removal commands to a `.rsc` file the user can review and import manually.

## Acceptance criteria

- [ ] `lankit audit` with `--verbose` shows the RouterOS removal command for rogue VLANs, DHCP entries, and WiFi interfaces
- [ ] Firewall rogues show a manual review warning instead of a removal command
- [ ] `--export-fixes FILE` writes all safe removal commands to a .rsc file
- [ ] The .rsc file has a comment header explaining what it does and warning to review before importing
