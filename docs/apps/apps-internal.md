# apps.internal — Portal Hub

## Purpose

Landing page. Links to the other portals. Shows enough status to answer
"is anything wrong right now" at a glance.

## No significant prior art

The prior art has no hub page. `apps.internal` is new but trivial.

## v1: static hub

A static HTML page (no Flask backend needed) with:

- Links: me.internal, network.internal, register.internal
- Brief one-line description of each
- Household name in the header (rendered by Caddy/Jinja at provision time)

Already deployed as a placeholder by the `portal` Ansible role.
The placeholder just needs real content and links.

## v2: status hub

Upgrade to a lightweight Flask route that checks:

| Indicator | Source | Display |
|-----------|--------|---------|
| Pi-hole reachable | HTTP to `http://10.40.0.2/api/stats/summary` | ✓ / ✗ |
| Router reachable | SSH or HTTP | ✓ / ✗ |
| Internet up | Cached latency result from local SQLite | ✓ / ✗ |

No live queries on page load — all indicators read from cached results
(written by the latency cron job). Page load stays fast.

## Routes (v2 only)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Hub page with status indicators (Host: apps.internal) |

## Notes

- v1 is already partially deployed (placeholder HTML). Upgrade to real links
  immediately — no backend work required.
- v2 status indicators are only meaningful once the cron jobs from
  `network.internal` are running. Don't add them until those exist.
