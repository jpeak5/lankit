# ENH-020: `lankit rules` silently shows nothing if `lankit generate` hasn't been run

**Persona(s):** Sam, Alex
**Surface:** CLI — `lankit rules`
**Priority:** Medium

## Problem

`lankit rules` parses generated `.rsc` files in `ansible/generated/`. If that directory doesn't exist or is empty (first-time setup, or after cloning), the command produces no output or a misleading message, with no explanation.

The help text says "Run 'lankit generate' first to produce the scripts" — but this is only visible if the user reads `--help`. Running `lankit rules` cold, as Sam likely does when exploring the tool, produces nothing actionable.

The same applies to `lankit diagram`, which also depends on generated files.

## Proposed fix

When `ansible/generated/` does not exist or contains no `.rsc` files, print a clear pre-flight message:

```
No generated scripts found in ansible/generated/.
Run 'lankit generate' first to produce the scripts from network.yml.
```

Additionally, consider making `lankit rules` auto-generate if the directory is empty (with a `--no-generate` flag to skip this). This matches the behavior of `lankit apply`, which regenerates by default.

For `lankit diagram`, the same logic applies — it should either auto-generate or clearly tell the user to run `lankit generate` first.

## Acceptance criteria

- [ ] `lankit rules` with an empty or absent `ansible/generated/` prints a clear message naming `lankit generate` as the prerequisite
- [ ] The message is printed before any table or empty output
- [ ] `lankit diagram` has the same guard
- [ ] Optionally: `--auto-generate` flag (or default behavior) runs generate automatically if scripts are absent
