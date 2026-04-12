# ENH-024: `lankit discover --new` wizard is mentioned in README but not in the quick-start path

**Persona(s):** Morgan, Alex
**Surface:** README.md
**Priority:** Low

## Problem

The README quick-start instructs:

```bash
cp network.yml my-network.yml      # or: lankit discover --new
```

The `# or: lankit discover --new` alternative is a comment on a `cp` line, easily missed. For Morgan, `lankit discover --new` (the guided wizard) is actually the more appropriate onboarding path — she doesn't want to manually edit a YAML file full of networking options.

But the README never explains what `lankit discover --new` actually does, what questions it asks, or what you get at the end. The Commands table says "Scan for connected devices; `--new` runs the setup wizard" which gives slightly more context, but the wizard's existence is not prominently positioned.

Meanwhile, the DESIGN.md "Software — the Pi" section says "lankit discovers it, configures it, and installs everything else" — referring to a different `discover` usage (scanning, not setup).

## Proposed fix

Give the wizard its own quick-start bullet:

```
## Quick start

### Option A — Guided setup (recommended for first-time users)
  lankit discover --new     # walks you through creating network.yml

### Option B — Edit the template directly
  cp network.yml my-network.yml
  # open my-network.yml and fill in all CHOOSE fields
```

Add a one-sentence description of what the wizard does: "Scans your network for connected devices, then asks questions to configure each segment. Takes about 10 minutes."

## Acceptance criteria

- [ ] The README quick-start section presents `discover --new` as an equivalent first-class path to copying the template
- [ ] A brief description of what the wizard does appears near the quick-start
- [ ] The discover entry in the Commands table includes the approximate time/effort for the wizard path
