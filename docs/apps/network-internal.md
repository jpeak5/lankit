# network.internal — Household Network Dashboard

## Purpose

Read-only view of network health for household members. Answers: is the internet working,
what devices are online, and are we getting what we pay for. One write action: trigger a
speed test.

## Routes

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Dashboard (served when Host: network.internal) |
| `GET` | `/devices` | HTML fragment: device list (HTMX target) |
| `POST` | `/speedtest` | Trigger on-demand speed test (rate-limited: 1 per 15 min) |
| `GET` | `/speedtest/status` | HTML fragment: running / last result (HTMX target) |

## Page sections

**Internet status** — WAN up/down, public IP, router uptime. Sourced via SSH to router.
Cached; "last checked X minutes ago" shown beside each indicator.

**Latency** — rolling average RTT to two public resolvers, collected by cron every 5 minutes,
stored in `latency_log`. Shows current average and a simple text trend (stable / improving /
degrading based on last 12 samples). No sparkline in v1.

**Speed test** — last result (down/up/ping), time since tested, "Run now" button.
Status updates via HTMX polling (`hx-get="/speedtest/status" hx-trigger="every 3s"`) while
a test is running; polling stops when result arrives.

**Devices** — all Pi-hole clients: hostname, IP, online/offline, first seen.
Post-VLAN: grouped by segment.

## Device online/offline

Online/offline is determined by polling the router's DHCP lease table via SSH, not by
DNS query recency. A device is online if it holds an active DHCP lease. This is reliable
for devices that make no DNS queries (IoT sensors, smart bulbs, etc.).

```
/ip dhcp-server lease print where status=bound
```

Cache this result and refresh every 60 seconds via the latency cron job (or its own cron).

## Traffic accounting

RouterOS `/ip accounting` maintains a running tally of byte counts per IP pair — a traffic
ledger of who sent how many bytes to whom since the last snapshot. It answers per-device
bandwidth questions, but requires external polling and accumulation: the router stores only
the current running totals, not history. If you don't poll it on a regular interval and
write results to local SQLite, the data is gone.

This feature is deferred from v1. It is not enabled by default on the router, and the
polling infrastructure (cron + SSH + SQLite accumulation) adds meaningful complexity for
uncertain payoff at household scale. Omit the bandwidth/usage section entirely from v1.

## Cron jobs (deployed by Ansible to app_server)

```
*/5 * * * *   /opt/lankit-portal/cron_latency.sh
0 */6 * * *   /opt/lankit-portal/cron_speedtest.sh
```

Both scripts append to `latency_log` and `speed_results` respectively. Each run writes a
`measured_at` timestamp; the dashboard shows this timestamp alongside cached data so a
stale cron is visible rather than silent.

## Access control

Enforced at the firewall:
- trusted, work: full access
- guest: internet status + speed test only; device list is suppressed. The guest view is
  a distinct page layout (not hidden sections) — a guest landing on network.internal sees
  a two-section page, not a broken-looking full page with gaps.
- iot, quarantine: no access

Public IP is suppressed in the guest view.
