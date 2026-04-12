# ENH-007: `lankit overview` silently accepts a partially-filled network.yml

**Persona(s):** Alex, Morgan
**Surface:** CLI — `lankit overview` (status.py), `network.yml`
**Priority:** Medium

## Problem

The README quick-start says to run `lankit overview` (listed as `lankit status`) immediately after filling in `network.yml`. However, `lankit overview` loads and displays the config even when CHOOSE markers have been left in place or required fields are empty. The user gets a formatted table output that looks successful.

Only when they run `lankit apply` do they learn that the config is incomplete — at which point they're already connected and expecting to push changes.

The `network.yml` comment says "lankit will refuse to apply changes until all CHOOSE markers are replaced," but `overview` doesn't enforce this. The first feedback about an incomplete config comes at the most disruptive moment.

## Proposed fix

Add a `--validate` flag (or make it the default) for `lankit overview` that:
- Checks for any remaining literal string `CHOOSE` in the loaded YAML values
- Reports unfilled fields before showing the summary table: "2 fields still need a decision"
- Points to the specific fields: `privacy.query_logging`, `vpn`

Alternatively, add a dedicated `lankit check` command (or expand the existing `ConfigError` detection) that users can run as an explicit validation step before `apply`.

The schema validation in `config.py` already detects null CHOOSE fields — this logic just needs to be surfaced in `overview` as a warning, not silently ignored.

## Acceptance criteria

- [ ] `lankit overview` prints a warning section if any field still contains a CHOOSE marker or null value for a required field
- [ ] The warning names each unfilled field explicitly
- [ ] The config summary table still renders (so the user can see what IS filled in)
- [ ] `lankit apply` still refuses to run with unfilled CHOOSE fields (existing behavior preserved)
