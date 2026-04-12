# ENH-015: Permissions section comment doesn't explain what to do for a new segment

**Persona(s):** Alex, Morgan
**Surface:** network.yml
**Priority:** Medium

## Problem

The permissions section in `network.yml` has good explanatory comments about how `can_reach` works (the "locked doors" analogy is clear). However, it only shows examples for the two segments that have entries (`trusted` and `servers`). There is no example or guidance for what to do when you add a new segment.

Concretely: when Alex runs `lankit extend` and adds a `cameras` segment, he gets no prompt about permissions during the wizard, and no hint in `network.yml` about whether/how to add permission entries for the new segment. He may not realize his `trusted` devices can't reach the new `cameras` VLAN until he's puzzled about why his home automation isn't working.

The wizard (`extend`) also never asks about permissions — it asks about DNS, internet access, and bandwidth, but never "should any existing segment be able to reach this one?"

## Proposed fix

Option A — network.yml comment: Add a comment block after the existing permission entries:

```yaml
  # To allow a segment to reach your new segment, add it here.
  # Example: to let trusted devices reach a "cameras" segment:
  # trusted:
  #   can_reach: [servers, media, iot, work, admin, cameras]
  #
  # Segments not listed here cannot initiate connections anywhere.
```

Option B — extend wizard: After completing the segment wizard, ask:

```
Which existing segments should be able to reach 'cameras'?
(space-separated, e.g. trusted servers, or press Enter for none)
> trusted
```

Both options should be implemented. The comment helps direct editors; the wizard prompt helps users who don't read comments.

## Acceptance criteria

- [ ] network.yml permissions section has a commented example showing how to add a new segment's permissions
- [ ] `lankit extend` wizard asks which existing segments should be able to reach the new segment
- [ ] The wizard validates that entered segment names exist
- [ ] If permissions are specified in the wizard, they are written to the `permissions:` block in network.yml
