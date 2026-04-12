# ENH-021: No documented way to inspect generated .rsc scripts before applying

**Persona(s):** Sam
**Surface:** README.md, CLI — `lankit generate`
**Priority:** Low

## Problem

Sam, coming from pfSense, wants to understand exactly what RouterOS commands lankit is going to run before trusting the tool. The generated `.rsc` files are the correct place to look — they live in `ansible/generated/` after `lankit generate` runs — but this is never mentioned in README.md or in any command help text.

The README quick-start goes directly from "edit network.yml" to "lankit apply" with only `lankit diagram --view` as a visual sanity check. There is no step that says "inspect the generated RouterOS scripts in `ansible/generated/`."

Sam will find the files by reading the file structure in README.md, but only because he reads documentation thoroughly. The casual user (Alex) will never know the escape hatch exists.

## Proposed fix

Add a step in the README quick-start between `lankit generate` and `lankit apply`:

```bash
lankit generate                    # render RouterOS scripts
ls ansible/generated/              # see what will be applied
cat ansible/generated/03-firewall.rsc  # inspect a specific script
lankit rules                       # human-readable summary of firewall rules
```

Also add a line to the `lankit generate --help` output:

```
After generating, scripts are in ansible/generated/.
Inspect them before applying: cat ansible/generated/*.rsc
```

## Acceptance criteria

- [ ] README quick-start includes a step showing how to inspect generated scripts in `ansible/generated/`
- [ ] `lankit generate` help text mentions the output location and how to inspect it
- [ ] `lankit rules` is cross-referenced as the human-readable way to read firewall rules
