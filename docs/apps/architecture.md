# Portal Architecture

## Hosts

| Host | Role | IP |
|------|------|----|
| dns_server (janus) | Pi-hole + Unbound | 10.40.0.2 |
| app_server (apps) | Caddy + Flask portals | 10.40.0.3 |

Caddy on app_server routes by hostname:

```
me.internal       → Flask app (port 5000)
apps.internal     → Flask app (port 5000)
network.internal  → Flask app (port 5000)
register.internal → Flask app (port 5000)
```

All four are one Flask application, host-header routed.

## Prior art

`~/Documents/code/network/portal/app.py` (~960 lines) is a working implementation
of the device portal and network dashboard that ran on janus. The core logic —
Pi-hole stats queries, bypass group management, rename, speedtest, device enumeration
— is directly portable. The critical difference is data access.

## Data access: the two-Pi gap

The prior art ran on janus and made direct SQLite calls to:
- `/etc/pihole/pihole-FTL.db` — query stats (read-only)
- `/etc/pihole/gravity.db` — group management for bypass (read-write)
- `/etc/pihole/pihole.toml` — blocking status

The app_server does not have these files. **Resolution: Pi-hole v6 REST API.**

Pi-hole 6 exposes a documented local REST API at `http://<dns_server_ip>/api/`.
The app_server calls this API over the servers VLAN. An API token is required;
Ansible fetches it from janus during provision and writes it to a config file on
app_server.

### API endpoints in use

| Operation | Endpoint |
|-----------|----------|
| Summary stats | `GET /api/stats/summary` |
| Per-client stats | `GET /api/stats/clients` |
| Top blocked domains | `GET /api/stats/top_domains?blocked=true` |
| Blocking status | `GET /api/dns/blocking` |
| Enable/disable blocking for client | `POST /api/dns/blocking` (or group API) |
| Device list | `GET /api/network/devices` |
| Client list | `GET /api/clients` |
| Authenticate | `POST /api/auth` |

The bypass group management (moving a client to a no-adlist group) may require
direct gravity.db manipulation via SSH if the Pi-hole API does not expose group
assignment per client. The app_server already has SSH access to janus via
`~/.ssh/lankit`. This is an implementation detail, not a blocker.

## Token provisioning

During `lankit provision --host app_server`, Ansible:
1. SSHes to janus to read the Pi-hole API token from `/etc/pihole/pihole.toml`
   (field: `webserver.api.app_pwhash` or equivalent)
2. Writes a config file to app_server: `/etc/lankit-portal/config.toml`

```toml
[pihole]
url = "http://10.40.0.2"
token = "<api_token>"
```

The token config file is mode 0600, owned by the portal user.

## Stack

| Layer | Choice | Notes |
|-------|--------|-------|
| Language | Python 3 | Consistent with prior art and the rest of lankit |
| Framework | Flask | Direct port from prior art; single-file possible |
| Frontend | Plain HTML + vanilla JS | No build step; Chart.js from CDN for sparklines |
| Database | SQLite on app_server | Bypass log, rename log, speed test history |
| Process manager | systemd unit | Deployed by Ansible (`portal` role) |
| Scheduler | APScheduler (in-process) | Replaces `at` from prior art; no atd dependency |
| Reverse proxy | Caddy | Already installed; routes by Host header to Flask |

## Deployment

The `portal` Ansible role deploys:
- Flask app to `/opt/lankit-portal/`
- systemd unit: `lankit-portal.service` (runs as `www-data` or dedicated user)
- SQLite DB initialized on first start
- Config file at `/etc/lankit-portal/config.toml`

Caddy Caddyfile updated to proxy (not file_server) for each hostname.
