# ENH-001: README quick-start references `lankit status` which doesn't exist

**Persona(s):** Alex, Morgan, Riley
**Surface:** README.md, CLI
**Priority:** High

## Problem

The README Quick Start section (line 47) instructs the user to run `lankit status` as the first command after editing `network.yml`. That command does not exist — the actual command is `lankit overview`. A first-time user who follows the README exactly gets:

```
Error: No such command 'status'.
```

with no suggestion for what the correct command is. This is the very first CLI interaction the README prescribes, so it immediately breaks trust for Alex and Morgan before they've done anything useful.

## Proposed fix

Option A (preferred): Add `status` as an alias for `overview` in the CLI so the README example works as written and muscle memory is preserved for returning users.

Option B: Update the README quick-start block to use `lankit overview`, and add a note in the Commands table that `status` was renamed to `overview`.

The Commands table in README.md also lists `lankit status` — this must be updated to `lankit overview` regardless of which option is chosen.

## Acceptance criteria

- [ ] `lankit status` either works (alias) or the README quick-start no longer references it
- [ ] The Commands table in README.md is consistent with the actual command name
- [ ] Running the quick-start sequence top-to-bottom produces no "No such command" errors
