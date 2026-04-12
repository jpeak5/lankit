# ENH-023: Snapshot rotation limit (10 per router) is not communicated at apply time

**Persona(s):** Riley, Sam, Jordan
**Surface:** CLI — `lankit apply`, `lankit snapshots`
**Priority:** Low

## Problem

Snapshots are capped at 10 per router (mentioned only in `lankit snapshots --help`). Every `lankit apply` creates two snapshots (pre-apply and post-apply), meaning after 5 apply runs the oldest snapshots silently disappear.

Riley, returning after 6 months, runs `lankit rollback` expecting to get her 6-month-old config and instead gets the most recent pre-apply snapshot from 3 days ago. The old "known good" config is gone and she has no way to know it existed.

There is no warning when a snapshot is about to be rotated out, no way to "pin" a snapshot, and no mention of the rotation limit at apply time.

## Proposed fix

1. **At apply time**, when the snapshot count is at or near the limit (e.g., 9 or 10), print a warning:
   ```
   Note: You have 10 snapshots (the maximum). The oldest will be removed
   to make room. To keep it: lankit snapshots --capture --label before-big-change
   ```

2. **Add a `--pin` flag to `lankit snapshots`** that marks a snapshot as protected from rotation:
   ```
   lankit snapshots --pin 3    # pin snapshot #3
   lankit snapshots --pin latest
   ```

3. **In `lankit rollback`**, show the oldest available snapshot age so the user knows the rollback horizon:
   ```
   Oldest available snapshot: 3 days ago (10 snapshots kept)
   ```

## Acceptance criteria

- [ ] `lankit apply` warns when snapshot count is at or near the rotation limit
- [ ] Warning names the `--capture --label` workflow for preserving a snapshot before rotation
- [ ] `lankit rollback` shows the oldest available snapshot age
- [ ] Optional: `--pin` flag or equivalent to protect a snapshot from rotation
