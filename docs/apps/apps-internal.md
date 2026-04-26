# apps.internal — Portal Hub

## Purpose

Landing page. Links to the other portals with a one-line plain-language description of each.
Answers "is anything wrong right now" at a glance in v2.

## v1: static hub

A static HTML page (no Flask backend) with:

- Links: me.internal, network.internal, register.internal
- One-line description of each, written for a non-technical reader:
  - me.internal: "See your device's ad-blocking stats, or pause it for a while."
  - network.internal: "Check if the internet is working and see what devices are online."
  - register.internal: "Name a new device so the network remembers it."
- `{{ household_name }}` in the header — rendered by Ansible at provision time from
  `config.toml:portal.household_name`

apps.internal v1 has no Flask route. Caddy serves it as a static `file_server`.

## v2: status hub

Upgrade to a Flask route that reads cached data (written by the latency cron) and renders
three status indicators:

| Indicator | Source | Display |
|-----------|--------|---------|
| Pi-hole reachable | Last successful API call timestamp | ✓ / ✗ |
| Router reachable | Last successful SSH call timestamp | ✓ / ✗ |
| Internet up | Last latency sample from `latency_log` | ✓ / ✗ |

All indicators read from SQLite — no live queries on page load. Status refreshes via HTMX
every 30 seconds.

v2 status indicators are only meaningful once the latency cron is running. Do not add them
before the cron exists.

## Access control

All segments that can reach app_server:80 can reach apps.internal. No segment-specific
content differences — the hub is the same for everyone.
