# ENH-006: `lankit provision` gives no confirmation that Pi-hole and Unbound are actually working

**Persona(s):** Morgan, Alex, Jordan
**Surface:** CLI — `lankit provision`
**Priority:** High

## Problem

`lankit provision` runs the Ansible playbook and exits with the Ansible summary. For a user who isn't fluent in Ansible output, it's unclear whether Pi-hole is actually serving DNS, whether Unbound is recursing, and whether ad blocking is active. Morgan's primary goal is ad blocking — she has no way to verify it's working without searching for "how to test Pi-hole."

The provision command has a `--check` (dry-run) flag but no post-provision verification step.

## Proposed fix

After a successful Ansible run, automatically run a lightweight DNS smoke test:

1. Query a known ad-tracking domain (e.g. `doubleclick.net`) against the Pi-hole IP — expect it to return `0.0.0.0` (blocked)
2. Query a legitimate domain (e.g. `example.com`) against Pi-hole — expect a real answer
3. Query a DNSSEC-signed domain (e.g. `dnssec.works`) against Unbound — expect a valid response

Print a summary:
```
Post-provision checks:
  ✓ Pi-hole reachable at 10.40.0.2
  ✓ Ad domain blocked (doubleclick.net → 0.0.0.0)
  ✓ DNS resolving (example.com → 93.184.216.34)
  ✓ DNSSEC validating
```

Add `--skip-checks` to bypass this for users who just want raw Ansible output.

## Acceptance criteria

- [ ] After successful Ansible run, DNS smoke tests run automatically against the provisioned Pi-hole
- [ ] Output clearly states: Pi-hole reachable, ad blocking working, DNS resolving
- [ ] DNSSEC validation is confirmed if `dnssec: true` in network.yml
- [ ] Any failing check prints a specific remediation step (not just "check failed")
- [ ] `--skip-checks` skips all post-provision tests
