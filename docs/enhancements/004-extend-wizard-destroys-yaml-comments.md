# ENH-004: `lankit extend` writes YAML that strips all comments from network.yml

**Persona(s):** Alex, Sam, Riley
**Surface:** CLI — `lankit extend`
**Priority:** High

## Problem

`lankit extend` adds a new segment by doing `yaml.safe_load()` → mutate dict → `yaml.dump()`. PyYAML's `dump()` does not preserve comments. Running `lankit extend` once wipes every comment from `network.yml` — all the inline documentation that explains each field, the CHOOSE markers, the section headers.

For Alex and Riley, the result is a file that looks nothing like the documented template and is much harder to edit by hand. For Sam, it's a signal that the tool doesn't respect the config file as a human document.

This also silently removes all CHOOSE markers. If the user later adds a field manually and forgets to fill in a CHOOSE, the validation guidance is gone.

## Proposed fix

Use `ruamel.yaml` (round-trip mode) instead of PyYAML for all writes to `network.yml`. `ruamel.yaml` preserves comments, blank lines, and key ordering on round-trip load/dump.

The new segment block added by `extend` won't have comments (it was generated), but existing content is preserved verbatim.

Alternatively, instead of rewriting the entire file, `extend` could append the new segment YAML block as a text snippet at the end of the `segments:` section, using a clearly marked template comment as the insertion point. This is simpler but more fragile.

## Acceptance criteria

- [ ] Running `lankit extend` does not remove any comment lines from `network.yml`
- [ ] Section headers (`# ─── Network Segments`) are preserved
- [ ] CHOOSE markers in existing fields are preserved
- [ ] The new segment block is appended in the correct location in `segments:`
