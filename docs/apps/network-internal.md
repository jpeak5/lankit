# network.internal — Household Network Dashboard

## Purpose

Read-only view of network health for trusted household members. Answers:
is the internet working, what devices are online, and are we getting what
we pay for. Not an admin tool — no controls except "run a speed test."

## Prior art

`~/Documents/code/network/portal/app.py` + `templates/network.html` implements
this. The port is the same data access translation as `me.internal`:

- Direct SQLite → Pi-hole API for device list and blocking status
- Router SSH calls → kept as-is (`ssh_router()` function in prior art works)
- Speed test cron → kept as cron on app_server; results stored in local SQLite
- Latency monitoring cron → kept as cron on app_server
- `threading.Thread` for async speed test → kept as-is or replace with APScheduler

Estimated delta: light. The network dashboard is mostly read-only data
aggregation; the only write path is the speed test trigger.

## Routes

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Dashboard (served when Host: network.internal) |
| `GET` | `/devices` | JSON: all devices with online status |
| `POST` | `/speedtest` | Trigger on-demand speed test (rate-limited) |
| `GET` | `/speedtest/status` | JSON: running / done / error |

## Page sections

**Internet status** — WAN up/down, public IP, router uptime (via SSH to router).

**Latency** — rolling average to 1.1.1.1 and 8.8.8.8, collected by cron every
5 minutes, stored in local SQLite.

**Speed test** — last result, time since tested, 7-day sparkline. "Run now"
button rate-limited to 1 per 15 minutes.

**Devices** — all Pi-hole clients: hostname, IP, online/offline (last query
within 15 min), first seen. Post-VLAN: grouped by segment.

## Cron jobs (deployed by Ansible to app_server)

```
*/5 * * * *   /opt/lankit-portal/cron_latency.sh
0 */6 * * *   /opt/lankit-portal/cron_speedtest.sh
```

Prior art has working versions of both scripts in
`~/Documents/code/network/portal/`. Port directly; update paths.

## Rollback button

Prior art includes a `/rollback` route on `network.internal` that triggers
`rollback.sh` via subprocess. In lankit, `lankit rollback` handles this from
the admin machine. **Omit the rollback button from the lankit port.** It adds
attack surface and duplicates a CLI command. If you lose WiFi and need to
rollback from a phone, that's what the printed rollback card is for.

## Access control

Pre-VLAN: any device can reach network.internal. No sensitive data (no query
logs, no browsing history).
Post-VLAN: guest segment sees internet status + speed test only, not device
list or bandwidth. IoT and quarantine: no access.

## Open questions

- MikroTik IP accounting (`/ip/accounting`) for per-device bandwidth: is it
  currently enabled on the router? Check with `ssh admin@192.168.88.1 "/ip accounting print"`.
  If not enabled, bandwidth data is unavailable for v1. Acceptable — omit the
  usage page from v1 scope.
