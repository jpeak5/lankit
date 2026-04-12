# ENH-022: Privacy settings explain mechanics but not real-world consequences

**Persona(s):** Morgan, Jordan
**Surface:** network.yml
**Priority:** Medium

## Problem

The `privacy` section of `network.yml` has good inline comments that explain what each option does technically. For example, `query_logging: full` is described as "logs which device looked up which domain, and when." However, the comments don't explain the privacy consequences in terms a non-technical user cares about:

- Does Pi-hole send this data anywhere? (No — but Morgan doesn't know that.)
- Who can access the logs? (Controlled by `dashboard_visibility` — but the link between these two settings is not explained.)
- What does "anonymous" mean practically — can it be de-anonymized? (No — but it's not stated.)

Morgan's core motivation is ISP privacy. The comments focus on "what the DNS server records" without connecting it to "what your ISP sees." She doesn't know that Unbound's full recursive resolution means her ISP never sees individual queries — that's in DESIGN.md but not surfaced at the decision point in network.yml.

## Proposed fix

Expand each privacy option comment to include a one-line consequence statement and connect related settings:

```yaml
  # Query logging: what your DNS server records about lookups.
  # Note: these logs stay on your Pi — nothing is sent to any third party.
  #
  #   "full"      → logs device + domain + time. Most useful for debugging.
  #                 Privacy impact: anyone with Pi-hole access can see
  #                 every device's browsing history.
  #
  #   "anonymous" → logs domains but not which device asked.
  #                 Cannot be de-anonymized. Good middle ground.
  #
  #   "none"      → no logs. Pi-hole still blocks ads.
  #                 Privacy impact: you lose the ability to audit IoT devices.
  #
  # Also controls who can see these logs: see dashboard_visibility below.
```

Add a note at the top of the privacy section:
```yaml
# Your ISP never sees individual DNS queries when using Unbound (full recursive
# mode). These privacy settings control what is recorded locally on your Pi.
```

## Acceptance criteria

- [ ] Each query_logging option includes a "Privacy impact:" consequence statement
- [ ] The privacy section opening comment explains that logs stay local (not sent externally)
- [ ] A note explains that Unbound prevents ISP visibility of individual queries
- [ ] The relationship between query_logging and dashboard_visibility is cross-referenced
