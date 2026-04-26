# me.internal — Device Self-Service Portal

## Purpose

Lets any device on the network see its own DNS stats, temporarily disable ad blocking,
and set its own hostname. No login — your IP is your identity.

The answer to "my internet feels broken" that doesn't require involving the network admin.

## Routes

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Device status page (served when Host: me.internal) |
| `POST` | `/bypass` | Enable bypass for this device |
| `POST` | `/bypass/cancel` | Re-enable blocking for this device |
| `POST` | `/rename` | Set hostname for this device |
| `GET` | `/status` | HTML fragment: bypass state + blocking status (HTMX target) |

## Page: GET /

Header: hostname (editable inline), IP address. One sentence explaining that this page is
specific to the device currently viewing it.

**Stats (last 24h)** — from Pi-hole API scoped to requesting client IP:
- Total queries, blocked queries, percent filtered
- Top 5 blocked domains (collapsible)

**Ad blocking section:**
- If blocking active: duration selector (15m/30m/1h/2h) + "Pause ad blocking" button
- If bypassed: time remaining + "Resume now" button
- If blocking globally off: informational message only

The status section polls every 10 seconds via HTMX (`hx-get="/status" hx-trigger="every 10s"`).
No page reload required when bypass state changes.

**Rename section:** text input, validated inline.

**History:** last 10 bypass events (default collapsed).

## Bypass implementation

On `POST /bypass`:
1. Check APScheduler for an existing job with `job_id=<mac>`. If found, replace it.
2. Call Pi-hole API to disable filtering for client.
   - If the Pi-hole v6 API supports per-client disabling, use it.
   - Otherwise: SSH to dns_server → update gravity.db group membership.
3. Schedule revert job: `job_id=<mac>`, `run_date=now+duration`, SQLite job store.
4. Log to `bypass_log`.

On revert (timer fires or `POST /bypass/cancel`):
1. Re-enable filtering via Pi-hole API (or SSH fallback).
2. Update `bypass_log.ended_at`, set `cancelled=1` if manual.
3. Remove APScheduler job.

**On startup:** query Pi-hole for clients currently in bypass state that have no corresponding
scheduler job — revert them immediately. This recovers any bypass that outlasted a service restart.

**Idempotence:** re-POSTing `/bypass` while already bypassed replaces the existing job and
resets the timer. It does not create a second entry.

## Rename implementation

1. Validate: `^[a-zA-Z0-9][a-zA-Z0-9\-]{0,29}$`
2. Check current DNS record — skip write if name is unchanged.
3. Reject reserved names: `router`, `dns`, `apps`, `me`, `network`, `register`.
4. Check for collision with existing DNS records.
   - If taken: return a specific error naming the conflict ("'workshop-pc' is already in use").
5. Call Pi-hole API to write DNS record: `<name>.internal → client IP`.
6. Call Pi-hole API to reload DNS.
7. Log to `rename_log`.
8. Optionally: SSH to router to update DHCP lease comment (`/ip dhcp-server lease set [find mac-address=...] comment=<name>`).

## Access control

Enforced at the firewall, not in the app:
- trusted, work, guest: full access
- iot: access permitted; bypass button suppressed (detected via Pi-hole client segment data)
- quarantine: no access (no firewall rule)

When the bypass button is suppressed due to segment, show: "Ad blocking controls are not
available for this device type." Do not show a generic error or empty space.

## IP-as-identity

This page identifies the requesting device by IP address — no login is required or possible.
One line near the page header should make this explicit: "Showing stats for this device
([IP address])."

Limitations (document in spec, handle defensively in code):
- IPv6: use the IPv4 address if available; fall back to IPv6.
- Shared IP: two devices behind NAT appear as one. Not expected on a LAN.
- IP change: if a device's address changes between sessions, prior history is not linked.
  Acceptable for v1.
