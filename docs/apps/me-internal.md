# me.internal — Device Self-Service Portal

## Purpose

Lets any device on the network see its own DNS stats, temporarily disable
ad blocking, and set its own hostname. No login. Your IP is your identity.
The answer to "my internet is weird" before anyone has to get involved.

## Prior art

`~/Documents/code/network/portal/app.py` + `templates/device.html` implements
this almost completely. The port is:

- Direct SQLite → Pi-hole API calls (see `architecture.md`)
- `at` scheduled revert → APScheduler in-process job
- `pihole reloaddns` via subprocess → Pi-hole API call
- DHCP lease comment update via SSH to router → kept as-is (SSH already works)

Estimated delta from prior art to working lankit port: moderate. Most logic
translates directly; the data access layer needs replacing.

## Routes

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Device status page (served when Host: me.internal) |
| `POST` | `/bypass` | Enable bypass for this device |
| `POST` | `/bypass/cancel` | Re-enable blocking for this device |
| `POST` | `/rename` | Set hostname for this device |
| `GET` | `/status` | JSON: bypass state + blocking status (polled by frontend) |

## Page: GET /

Header: hostname (editable inline), IP address.

**Stats (last 24h)** — from Pi-hole API scoped to requesting client IP:
- Total queries, blocked queries, percent filtered
- Top 5 blocked domains

**Ad blocking section:**
- If blocking active: duration selector (15m/30m/1h/2h) + "Disable blocking" button
- If bypassed: countdown to re-enable + "Re-enable now" button
- If blocking globally off: informational message, no button

**Bypass history:** last 10 entries (collapsible), bypass count this week.

## Bypass implementation

Prior art used Pi-hole groups (gravity.db): moved client to a group with no
adlists ("jotunheim"). Revert was scheduled with `at`.

Lankit port:
1. On `POST /bypass`: call Pi-hole API to disable filtering for client; schedule
   revert with APScheduler (in-process, persistent across restarts via SQLite job store)
2. On revert (timer fires or `POST /bypass/cancel`): call Pi-hole API to re-enable

If the Pi-hole API does not support per-client filtering directly, fall back to
SSH → gravity.db manipulation (same logic as prior art, different transport).

**Orphan recovery:** on Flask startup, query Pi-hole for clients in bypass state
that have no corresponding scheduler job; revert them immediately.

## Rename implementation

1. Validate name: `^[a-zA-Z0-9][a-zA-Z0-9\-]{0,29}$`
2. Call Pi-hole API to update local DNS record: `<name>.internal` → client IP
3. Call Pi-hole API to reload DNS
4. Log rename in SQLite
5. Optionally: SSH to router to update DHCP lease comment (nice-to-have, not blocking)

Reserved names: `router`, `dns`, `apps`, `me`, `network`, `register`.

## Access control

Pre-VLAN: any device can reach me.internal. All actions are self-scoped (IP-gated).
Post-VLAN: IoT segment gets status-only (no bypass button); quarantine gets nothing.
Enforcement is at the firewall, not in the app.

## Open questions

- Does Pi-hole v6 API support per-client bypass directly, or does it require
  gravity.db manipulation? Check `/api/groups` and `/api/clients` endpoints.
- APScheduler job store: SQLite (persistent across restarts) vs. in-memory
  (simpler, bypass reverts on service restart). Recommend SQLite.
