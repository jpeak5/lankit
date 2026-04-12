# ENH-017: `lankit probe` forward-chain caveat is repeated twice and demotivating

**Persona(s):** Sam, Alex
**Surface:** CLI — `lankit probe`
**Priority:** Low

## Problem

`lankit probe` output ends with a multi-line dim-text block repeated from the help text:

```
─────────────────────────────────────────────────────
Forward-chain verification (inter-segment firewall)
requires a physical device in each segment. The
matrix above shows intended policy; probe confirmed
the rules are deployed, not that they work as intended.
Run: lankit matrix  to review the expected policy.
```

This important caveat is correct, but printing it on every run is noise for users who already understand it (Sam). For Alex, seeing "cannot confirm their effect on live traffic" after what looked like a successful probe is confusing — he doesn't know if his setup is broken or if this is always printed.

The same caveat also appears verbatim in the `--help` text. It's prominent in exactly the wrong place (after success) without a clear signal about whether it matters for his current situation.

## Proposed fix

Move the forward-chain note to a `--verbose` or `--explain` output mode. In default mode, replace it with a single line only when there are actually missing/failing rules:

- If all rules are present and all gateways reachable: print nothing (the table speaks for itself)
- If rules are missing: print "Run `lankit apply` to push missing rules."
- At the very bottom, always print one dim line: "Note: run `lankit matrix` to review inter-segment policy."

The full forward-chain explanation should remain in `--help` and in `DESIGN.md`.

## Acceptance criteria

- [ ] Default probe output does not print the multi-line forward-chain caveat block
- [ ] When all checks pass, output ends after the gateway table with no disclaimer paragraph
- [ ] `lankit probe --verbose` still shows the full explanation
- [ ] A single-line "see lankit matrix for inter-segment policy" appears at the bottom in all modes
