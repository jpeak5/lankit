# ENH-009: DESIGN.md audience framing explicitly excludes non-technical users

**Persona(s):** Morgan
**Surface:** DESIGN.md
**Priority:** Medium

## Problem

DESIGN.md opens with:

> "A distributable, self-documenting home network segmentation toolkit for MikroTik routers with Pi-hole DNS filtering. Aimed at technical people who appreciate craft and will invest up to 10 hours for agency over their home network."

The phrase "Aimed at technical people" immediately signals to Morgan that this tool isn't for her, even though the tool's design philosophy ("No network knowledge assumed — concepts explained inline, briefly") directly supports her use case.

More concretely, the "What You Need to Get Started" section of DESIGN.md leads with a full hardware table including a "Second Raspberry Pi (optional)" and PKI/TLS setup — none of which Morgan needs. She wants Pi-hole. The onboarding path for her simplest use case (one Pi, no VPN, no TLS) is not described anywhere as a coherent path.

## Proposed fix

Revise the opening sentence to be inclusive: "for people who want control over their home network" rather than "for technical people."

Add a "Simplest path" paragraph or callout box in DESIGN.md or README.md that describes the minimum viable setup:
- One router (MikroTik)
- One Raspberry Pi (Pi-hole + Unbound)
- Fill in `network.yml`, run `lankit provision`
- That's it — ad blocking works, DNS is private

This doesn't require changing any code; it's a documentation scoping issue.

## Acceptance criteria

- [ ] DESIGN.md opening description does not use "technical people" as the audience qualifier
- [ ] A "minimum viable" or "simplest setup" path is described in either DESIGN.md or README.md
- [ ] The minimum path makes clear that VPN, TLS, second Pi, and AWS backup are all optional
