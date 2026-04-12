# ENH-008: `lankit rollback` shows snapshot filename but not what it contains

**Persona(s):** Riley, Jordan, Alex
**Surface:** CLI — `lankit rollback`
**Priority:** Medium

## Problem

`lankit rollback` prompts:

```
Will restore: 192.168.88.1_2024-11-03T14:22:11_pre-apply.rsc
Proceed with rollback? [y/N]:
```

The only information shown is the snapshot filename. Riley, returning after months, has no idea whether this snapshot is from before or after her last major change, how old it is, or what configuration it contains. She has to make a high-stakes decision based on a filename timestamp.

The snapshot label (`pre-apply`) is there but its meaning ("captured before the apply on this date") isn't explained inline.

## Proposed fix

Before the confirmation prompt, display a brief snapshot summary:

```
Will restore snapshot:
  File:     192.168.88.1_2024-11-03T14:22:11_pre-apply.rsc
  Captured: 5 months ago  (2024-11-03 14:22 UTC)
  Label:    pre-apply  (captured automatically before lankit apply)
  Size:     24 KB

  Current config will be saved first (you can undo this rollback with lankit restore).

Proceed with rollback? [y/N]:
```

The "captured automatically before lankit apply" annotation should come from the label. Known automatic labels (`pre-apply`, `post-apply`, `pre-rollback`) should map to human explanations.

## Acceptance criteria

- [ ] Rollback prompt shows snapshot age in human-readable form (e.g. "3 days ago")
- [ ] Known label values produce a plain-English explanation of when the snapshot was taken
- [ ] Size of the snapshot is shown
- [ ] The prompt reminds the user that the current config is saved first (undo is possible)
