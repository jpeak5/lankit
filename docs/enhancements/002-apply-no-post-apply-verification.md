# ENH-002: `lankit apply` has no post-apply health check

**Persona(s):** Alex, Morgan, Jordan, Riley
**Surface:** CLI — `lankit apply`
**Priority:** High

## Problem

After `lankit apply` succeeds and the user answers "Keep these changes?", the output is:

```
✓ Changes applied. Failsafe disarmed.
  Post-apply snapshot: ~/.lankit/snapshots/...
  Run lankit rollback to undo if needed.
```

There is no indication of whether the router is actually functioning correctly. The user has no signal that VLANs are up, DHCP is serving, or DNS is reachable. Alex and Morgan are left wondering "did this work?" and are likely to re-read docs or google rather than run the obvious follow-up. Riley, returning after months, wants confirmation that nothing broke.

The failsafe window (120s by default) was the only verification window — once the user answers "yes", it's gone.

## Proposed fix

After the user confirms "Keep these changes?", run an automatic lightweight post-apply probe before disarming the failsafe:

1. Ping each segment gateway from the router (already implemented in `lankit probe` Phase 2)
2. Report a summary: "8/8 gateways reachable"
3. If any gateway is unreachable, warn and re-prompt: "Some segments may not be fully up. Keep anyway?"

This keeps the failsafe as a safety net during the verification window rather than before it. Add a `--skip-probe` flag for users who want the old behavior.

## Acceptance criteria

- [ ] After "Keep these changes?", gateway pings run automatically before disarming the failsafe
- [ ] Output shows a per-segment reachability summary
- [ ] If any gateway fails, the user is warned and asked to confirm again
- [ ] `--skip-probe` flag skips the check and disarms immediately
- [ ] `--dry-run` skips the probe (no router connection)
