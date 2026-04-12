# ENH-013: `--segment` recommended order buried in help text, not enforced or prompted

**Persona(s):** Alex, Sam
**Surface:** CLI — `lankit apply --segment`
**Priority:** Medium

## Problem

The `lankit apply --help` text includes:

> "Recommended order: admin → trusted → servers → iot → others → lankit apply."

This guidance is buried in the middle of a long help paragraph. A user running `lankit apply --segment iot` before `lankit apply --segment trusted` will produce a working IoT VLAN but may have missing global rules (NAT, default-deny) that only appear in a full `lankit apply` run.

There is no warning at runtime that the user is running `--segment` mode when global rules haven't been applied yet, and no prompt to run the full apply after segment-by-segment provisioning.

## Proposed fix

1. When `--segment` is used, check whether the `kit:fw:all:default-deny` tag is present on the live router. If not, print a warning before proceeding:

```
  Note: --segment mode applies only this segment's rules.
  Global rules (NAT, default-deny, VLAN filtering) have not been applied yet.
  Recommended: apply all segments first with 'lankit apply', then use
  '--segment' for incremental changes.
  Continue? [y/N]:
```

2. After a successful `--segment` apply, print:
```
  Next: run 'lankit apply' (full run) to apply global rules, or continue
  with the next segment: lankit apply --segment <name>
```

3. Move the recommended order from the middle of the help text to a prominent `\b` block at the top.

## Acceptance criteria

- [ ] `lankit apply --segment X` warns when global rules (kit:fw:all:default-deny) are absent on the router
- [ ] Warning prompts for confirmation (skippable with `--yes`)
- [ ] Post-apply output includes a "next steps" line reminding about full apply
- [ ] Help text has the recommended order in a clearly separated block near the top
