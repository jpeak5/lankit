# ENH-011: `{{ household_name }}` template syntax in network.yml has no explanation

**Persona(s):** Morgan, Alex
**Surface:** network.yml
**Priority:** Medium

## Problem

Several segments in the default `network.yml` use Jinja2 template syntax:

```yaml
ssid: "{{ household_name }}-IoT"
ssid: "{{ household_name }}-visitors"
```

There is no comment anywhere in `network.yml` explaining what `{{ household_name }}` means or that it will be substituted at generation time. A user who edits SSIDs might:

1. Not understand it's a variable and leave the literal `{{ household_name }}-IoT` as their WiFi name
2. Delete it and type the name out manually (which works, but loses the connection to `household_name`)
3. Break it accidentally (e.g., `{ household_name }-IoT` without the double braces) with no validation error

Morgan in particular is likely to leave it as-is, not realizing her IoT network will broadcast as `{{ household_name }}-IoT` literally.

## Proposed fix

Add a comment above the first occurrence of `{{ household_name }}` in `network.yml` explaining the template syntax:

```yaml
# ─── WiFi ─────────────────────────────────────────────────────────────────────
# SSID: the name devices see when scanning for WiFi.
# {{ household_name }} is replaced with your household_name setting above.
# Example: if household_name is "Peak", the IoT SSID becomes "Peak-IoT".
# You can use it in any string, or type a plain name directly.
ssid: "{{ household_name }}-IoT"
```

The comment only needs to appear once (at the first `{{ household_name }}` usage in a non-trusted segment) since it's introduced in the trusted segment's SSID explanation.

## Acceptance criteria

- [ ] A comment in network.yml explains that `{{ household_name }}` is a placeholder that expands to the value above
- [ ] The comment includes a concrete example of the expansion
- [ ] The comment appears before the first SSID field that uses the template variable
