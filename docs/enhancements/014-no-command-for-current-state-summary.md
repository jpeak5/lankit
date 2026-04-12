# ENH-014: No single command shows "current live state" for a returning user

**Persona(s):** Riley, Jordan
**Surface:** CLI — missing command / `lankit overview`
**Priority:** Medium

## Problem

Riley returns after 6 months. She doesn't remember if the last apply succeeded, whether any segments were added, or whether anything drifted. She wants one command that answers: "what is the network doing right now?"

The available commands are:
- `lankit overview` — reads network.yml only (desired state, no live data)
- `lankit audit` — live router vs. network.yml, but outputs a long resource-by-resource table
- `lankit probe` — gateway reachability + config audit, but requires knowing to run it

None of these answer "is everything roughly OK?" in under 10 lines. `lankit audit --problems` is close but still outputs a large table with kit: tags as row labels (not human-friendly).

There is also no easy way to see "what has changed since the last apply" — the snapshots exist, but there's no `lankit diff` command to compare them.

## Proposed fix

Add a `lankit health` command (or promote `lankit probe` with a cleaner default output):

```
lankit health

  Six2ate — 192.168.88.1 — RouterOS 7.14.2

  Segments     8/8  all rules present
  Gateways     8/8  all reachable
  DNS          ✓    Pi-hole at 10.40.0.2 answering
  Snapshots    ✓    last apply: 3 months ago (2024-11-03)

  ✓ Everything looks good. Run 'lankit audit' for full detail.
```

If anything is wrong, list only the failures:
```
  ✗ Segments  6/8 rules present  (2 missing — run lankit apply)
  ✗ DNS       Pi-hole unreachable at 10.40.0.2
```

This becomes the "check in" command Riley runs first when she comes back to the system.

## Acceptance criteria

- [ ] `lankit health` produces a summary in ≤10 lines for a healthy network
- [ ] Output covers: rule presence, gateway reachability, DNS reachability, last apply timestamp
- [ ] Unhealthy items show a one-line action ("run lankit apply" / "check Pi at 10.40.0.2")
- [ ] Command completes in <15 seconds on a live network
