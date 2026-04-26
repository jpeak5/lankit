# Portal Architecture

## Hosts

| Host | Role | IP |
|------|------|----|
| dns_server | Pi-hole + Unbound | `<dns-server-ip>` |
| app_server | Caddy + Flask portals | `<app-server-ip>` |

Both hosts occupy the `servers` VLAN. Traffic between them is permitted without exception —
servers on this segment mutually trust each other. Traffic from other segments to app_server
is permitted only by specific firewall rules (see below).

Caddy on app_server routes by hostname:

```
me.internal       → Flask app (port 5000)
network.internal  → Flask app (port 5000)
register.internal → Flask app (port 5000)
apps.internal     → static HTML (v1) / Flask app (v2)
```

Three portals are one Flask application, host-header routed. apps.internal is static HTML in v1.

## Prior art

A previous single-file Flask implementation covering device stats, bypass group management,
rename, speedtest, and device enumeration provides a near-complete port target. The core
logic is directly portable. The critical difference is data access.

## Data access

The prior art ran on dns_server and made direct SQLite calls to Pi-hole's databases.
app_server does not have those files. **Resolution: Pi-hole v6 REST API.**

Pi-hole 6 exposes a documented local REST API at `http://<dns-server-ip>/api/`. app_server
calls this API over the servers VLAN. Because servers mutually trust each other, no
additional firewall rule is needed for this path.

### Authentication

Pi-hole v6 uses password-based session tokens. The approach:

1. Ansible writes the Pi-hole admin password (from vault) to app_server at provision time.
2. On Flask startup, `POST /api/auth {"password": "..."}` → `{"session": {"token": "...", "validity": 1800}}`.
3. Token is stored in memory only. Attached as `X-FTL-SID` header on all subsequent API calls.
4. On 401 response: re-authenticate and retry.

The admin password lives in `/etc/lankit-portal/config.toml` (mode 0600, portal user).
Session tokens are never written to disk — they expire, re-authenticating on startup
is simpler than rotation.

### API endpoints in use

| Operation | Endpoint |
|-----------|----------|
| Summary stats | `GET /api/stats/summary` |
| Per-client stats | `GET /api/stats/clients` |
| Top blocked domains | `GET /api/stats/top_blocked_domains?blocked=true` |
| Global blocking status | `GET /api/dns/blocking` |
| Device list (with MACs) | `GET /api/network/devices` |
| Client list | `GET /api/clients` |
| Update client hostname | `PATCH /api/clients/<client>` |
| Authenticate | `POST /api/auth` |

Per-client ad blocking is not a first-class Pi-hole v6 API operation. If per-client
group-based bypass is needed, fall back to SSH → gravity.db manipulation on dns_server.
The app_server already has SSH access to dns_server. Prefer the API path wherever it exists.

### MAC address retrieval

`GET /api/network/devices` returns device objects with `hwaddr` (MAC) and `ip` fields.
To look up a client MAC from its IP:

```python
devices = get("/api/network/devices")
mac = next((d["hwaddr"] for d in devices if d["ip"] == client_ip), None)
```

Cache this response (it changes rarely). Invalidate on rename or registration.

## Token provisioning

During `lankit provision --host app_server`, Ansible:
1. Reads the Pi-hole admin password from Ansible vault
2. Writes `/etc/lankit-portal/config.toml` to app_server (mode 0600)

```toml
[pihole]
url = "http://<dns-server-ip>"
password = "<admin-password>"

[portal]
household_name = "Home"
```

`household_name` is the only household-specific string that appears in portal UI.
All other configuration is derived from the network at runtime.

## Stack

| Layer | Choice | Notes |
|-------|--------|-------|
| Language | Python 3 | Consistent with lankit |
| Framework | Flask | Direct port from prior art; single-file possible |
| Frontend | HTML + HTMX | Server-driven partials; no build step; no JS files |
| Database | SQLite on app_server | Single file; see data models below |
| Process manager | systemd unit | Deployed by Ansible (`portal` role) |
| Scheduler | APScheduler | SQLite job store — survives restarts |
| Reverse proxy | Caddy | Routes by Host header to Flask |

### On HTMX

HTMX is a 14 KB JavaScript library (CDN, no build step). It adds dynamic behavior via HTML
attributes:

```html
<div hx-get="/status" hx-trigger="every 5s" hx-target="this" hx-swap="outerHTML">
  ...current content...
</div>
```

Flask returns HTML fragments for these requests — the same Jinja2 templates, just rendered
to a smaller scope. No JavaScript files are needed for polling or partial updates.

## Data models

All SQLite tables live in a single database at `/var/lib/lankit-portal/portal.db`. This is
the canonical location for the schema — no table is defined anywhere else.

```sql
CREATE TABLE bypass_log (
    id          INTEGER PRIMARY KEY,
    ip          TEXT NOT NULL,
    mac         TEXT,
    hostname    TEXT,
    duration_m  INTEGER NOT NULL,
    started_at  TEXT NOT NULL,
    ended_at    TEXT,
    cancelled   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE rename_log (
    id          INTEGER PRIMARY KEY,
    ip          TEXT NOT NULL,
    mac         TEXT,
    old_name    TEXT,
    new_name    TEXT NOT NULL,
    renamed_at  TEXT NOT NULL
);

CREATE TABLE speed_results (
    id           INTEGER PRIMARY KEY,
    measured_at  TEXT NOT NULL,
    download_mbps REAL,
    upload_mbps  REAL,
    ping_ms      REAL,
    error        TEXT
);

CREATE TABLE latency_log (
    id          INTEGER PRIMARY KEY,
    measured_at TEXT NOT NULL,
    target      TEXT NOT NULL,
    rtt_ms      REAL
);

-- v2 addition (register.internal approval queue):
CREATE TABLE registrations (
    id               INTEGER PRIMARY KEY,
    mac              TEXT NOT NULL,
    ip               TEXT NOT NULL,
    requested_name   TEXT NOT NULL,
    requested_segment TEXT,
    status           TEXT NOT NULL DEFAULT 'pending',
    submitted_at     TEXT NOT NULL,
    reviewed_at      TEXT,
    reviewed_by      TEXT
);
```

Tables are created via `db.create_all()` on first start — safe to re-run.

## Idempotence

All write operations must be safe to repeat:

- **Rename:** query current DNS record; skip write if unchanged.
- **Bypass:** if a job for this client already exists in APScheduler, replace it (use `job_id=mac`); return current state.
- **Registration:** if MAC already has a pending/approved entry, return existing state rather than inserting.
- **DHCP static reservation (via SSH to router):** check `[find mac-address=...]` before add; use `set` if the entry exists, `add` only if absent.
- **DB init:** `CREATE TABLE IF NOT EXISTS` on all tables.

## Firewall rules

These rules must be present in the lankit network model. They are additions to the existing
segment permissions and should be expressed in `network.yml`.

| Source | Destination | Port | Purpose |
|--------|-------------|------|---------|
| servers | servers | any | Intra-servers mutual trust (app_server ↔ dns_server) |
| trusted | app_server | 80 | Portal access for trusted devices |
| work | app_server | 80 | Portal access for work devices (me.internal only) |
| guest | app_server | 80 | Portal access — limited view |
| iot | app_server | 80 | Portal access — status only, no bypass |
| quarantine | app_server | 80 | register.internal — this is the only exit from quarantine |

The quarantine rule is intentionally narrow: quarantine devices reach app_server:80 only.
It does not grant internet access or cross-segment visibility. The rule fires before the
quarantine default-deny egress block.

The `work` segment access is limited to me.internal. Work devices should not see the full
device list on network.internal (enforced in Flask by segment lookup, not by firewall).

## Deployment

The `portal` Ansible role deploys:
- Flask app to `/opt/lankit-portal/`
- systemd unit: `lankit-portal.service` (runs as `www-data` or dedicated `portal` user)
- SQLite DB initialized on first start
- Config at `/etc/lankit-portal/config.toml`
- Cron jobs for latency sampling and periodic speed tests

Caddy Caddyfile updated to:
- `proxy` (not `file_server`) for me.internal, network.internal, register.internal
- `file_server` for apps.internal v1; `proxy` for v2
