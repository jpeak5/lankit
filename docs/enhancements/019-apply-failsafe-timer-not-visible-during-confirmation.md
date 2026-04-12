# ENH-019: Failsafe timer is not visible while the "Keep changes?" prompt is active

**Persona(s):** Alex, Morgan, Jordan
**Surface:** CLI — `lankit apply`
**Priority:** Medium

## Problem

After scripts are applied, lankit prints:

```
Failsafe active — router will auto-revert in 120s if this session ends.
Keep these changes? (router will retain them; 'no' rolls back to snapshot) [y/N]:
```

The timer starts when the failsafe is installed (before uploads), not when the prompt appears. By the time the user sees this prompt, some unknown portion of the 120 seconds has already elapsed — potentially most of it, if there were many scripts and the router was slow.

A user who pauses to think, checks another window, or reads the message carefully may find the router has already auto-reverted before they answer "y." This is especially confusing for Morgan and Jordan who don't understand why their changes disappeared.

There is also no indication in the prompt of how many seconds remain.

## Proposed fix

Display the remaining seconds in the prompt using a live countdown:

Option A (simple): Print the elapsed time at prompt:
```
Failsafe active — 87s remaining (120s total).
Keep these changes? [y/N]:
```

Option B (better, if terminal supports it): Use a Rich live display showing a countdown bar while waiting for input. Requires capturing keypress separately from the countdown thread.

Option C (simplest, most compatible): Restart the failsafe timer immediately before showing the prompt, ensuring the user has the full `failsafe_seconds` to decide.

Option C is the safest implementation. It means: install failsafe before uploads (protects during upload), then reset the timer after all uploads complete (protects during the decision window).

## Acceptance criteria

- [ ] The user has the full `failsafe_seconds` to respond to the "Keep changes?" prompt
- [ ] The prompt displays the number of seconds available
- [ ] If the timer expires before the user answers (session disconnect), the behavior is documented
