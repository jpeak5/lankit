# ENH-005: `lankit apply` doesn't show what will change before applying

**Persona(s):** Sam, Riley, Jordan
**Surface:** CLI — `lankit apply`
**Priority:** High

## Problem

`lankit apply --dry-run` only prints the list of script filenames that would be uploaded:

```
Dry run: would apply 7 script(s) (all segments) to 192.168.88.1:
  01-vlans.rsc
  02-dhcp.rsc
  ...
```

It does not show what RouterOS commands are in those scripts, and it does not compare against the live router to show what is new, changed, or removed. Sam and Riley want to know "what exactly will change" before committing. Jordan needs to show a non-technical auditor "here's what changed" after the fact.

The `lankit audit` command detects drift but is a separate step and requires reading through a full table. There is no pre-apply diff.

## Proposed fix

Add a `--diff` flag to `lankit apply` (and make `--dry-run` imply it optionally):

- Connect to the router (read-only)
- Run `lankit audit` internally
- Display only the items that would change: missing rules that this apply would add, and any drifted items
- Show a count: "3 rules to add, 1 drifted rule to update"
- Prompt "Proceed with apply?" before uploading anything

`--dry-run --diff` should work without a router connection and fall back to "would apply N scripts" if no live router is available.

## Acceptance criteria

- [ ] `lankit apply --diff` connects to the router, audits, and shows a before/after summary
- [ ] Only changed items are shown (not the full audit table)
- [ ] User is prompted to confirm before upload begins
- [ ] `--diff` without `--dry-run` performs the apply after confirmation
- [ ] Works correctly in `--segment NAME` mode (scoped to that segment's rules)
