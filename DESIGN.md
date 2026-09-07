# lankit — Design Document

A distributable, self-documenting home network segmentation toolkit for MikroTik routers with Pi-hole DNS filtering. Setting it up means working at a terminal and editing a config file, and taking roughly ten hours to do it once — in exchange for real, reproducible control over your home network.

---

## Philosophy

- **One config file** (`network.yml`) is both the setup guide and the source of truth
- **No network knowledge assumed** — concepts explained inline, briefly
- **Privacy choices are explicit** — no defaults, must be decided
- **Templates generate everything** — RouterOS scripts, Pi-hole config, Unbound config, plain-English rules (`lankit explain`/`rules`/`matrix`)
- **Ansible manages all state** — no manual router/Pi changes

---

## What You Need to Get Started

### Hardware

| Item | Notes |
|------|-------|
| **MikroTik router** | Tested on hAP ax³ (RouterOS 7.x required). Other MikroTik models with dual-band WiFi and ≥4 ethernet ports should work. |
| **Raspberry Pi** (DNS server) | Pi 3B+ or newer. Pi 4 recommended if you plan to run additional services (portals, file shares). Needs a wired ethernet connection to the router. |
| **MicroSD card** | 16GB+ for the Pi. Class 10 / A1 or better. |
| **Ethernet cable** | To connect the Pi to the router. Also useful for your laptop during initial setup — gives you a management path that survives WiFi changes. |
| **Second Raspberry Pi** *(optional)* | Recommended for app server (portals, file shares). Keeps DNS isolated from application workloads. |

### Software — your machine

```bash
git clone https://github.com/[you]/lankit
cd lankit
pip install -e .
```

The `-e` (editable) install means `lankit` on your PATH reflects the cloned
source — pull an update and it takes effect immediately, no reinstall.

This installs everything: Ansible, pytest-testinfra, mac-vendor-lookup,
the graphviz Python wrapper, and all other dependencies. You do not install
them separately.

Two things `pip` cannot install for you:

| Item | How to get it | Why pip can't |
|------|--------------|---------------|
| **Python 3.11+** | python.org or your package manager | prerequisite to pip itself |
| **graphviz binary** *(optional)* | `brew install graphviz` / `apt install graphviz` | system binary; the `graphviz` pip package wraps it but can't install it |

The graphviz binary is only needed for `lankit diagram` (default output is
PNG; `--format svg`/`--format pdf` are also available).
All other lankit commands work without it.

**Distribution:** lankit is not on PyPI. Feedback and bug reports go to
GitHub Issues. This is intentional — the target audience is comfortable
with a `git clone`, and keeping it off PyPI means no accidental installs
by people who aren't ready for it.

### SSH key for Ansible

lankit uses SSH to provision the Pi and apply RouterOS config. It needs
a key pair — password auth is not supported.

```bash
# If you don't have one already:
ssh-keygen -t ed25519 -f ~/.ssh/lankit -C "lankit"

# Tell lankit where it is (network.yml):
ssh_key: ~/.ssh/lankit
```

During `lankit provision`, lankit copies the public key to the Pi
(`~/.ssh/authorized_keys`) and configures the router to accept it.
You only do this once.

### Software — the Pi

lankit provisions this for you. You only need:

- **Raspberry Pi OS Lite** (64-bit, Debian Bookworm) flashed to the SD card
- SSH enabled at first boot (create an empty file named `ssh` in the boot partition, or use Raspberry Pi Imager's advanced options)
- Pi connected to router via ethernet, powered on

lankit discovers it, configures it, and installs everything else.

### Accounts

| Account | Why | Notes |
|---------|-----|-------|
| **MikroTik router login** | lankit SSHes in to apply config | Default is `admin` / no password — change it before you start |
| **AWS account** *(optional)* | Off-site backup of file shares | Not yet implemented — see "Future / not built" below. For now, back up file shares by whatever means you already use. |

### PKI / TLS *(planned, not yet implemented — see "Future / not built")*

TLS/mkcert work is deliberately deferred. Everything below describes the
intended design, not current behavior — no `lankit pki` command exists yet.
Until it lands, portals are served over plain HTTP.

lankit can serve all `*.internal` services over HTTPS using locally-issued
certificates. This eliminates browser "not secure" warnings and enables
HTTP/2 for the portals.

**How it works:** [mkcert](https://github.com/FiloSottile/mkcert) creates a
local Certificate Authority (CA) that is trusted by your devices. Certificates
are issued for `*.internal` and served by Caddy on the app server. No external
CA, no Let's Encrypt, no public internet involvement.

**What you need:**

```bash
# Install mkcert (once, on your machine)
brew install mkcert        # macOS
apt install mkcert         # Linux

# Create your local CA and install it in your system trust store
mkcert -install

# lankit takes it from here — it generates the *.internal cert
# and deploys it to the app server as part of provisioning
lankit provision
```

**Trust propagation (planned):** the local CA certificate would need to be
installed on every device that will access `*.internal` services (phones,
other laptops). The intended interface is a `lankit pki --share` command
that prints a QR code + URL for CA cert download, served from the app server
at `pki.internal`. None of this exists yet.

**Today:** everything works over HTTP. Browser warnings appear for
`*.internal` sites. Acceptable for a single-person setup; less so if household
members use the portals regularly.

---

## Config file: network.yml

The single file a user fills out. Organized in sections.

### Section 1: Household

```yaml
# ─── Your Network ─────────────────────────────────────────
# This name appears in your WiFi SSIDs and internal hostnames.
# Example: "Maple" → WiFi SSIDs "Maple", "Maple-IoT", "Maple-Guest"
household_name: ""

# Internal domain for services on your network.
# This uses ".internal" — the only TLD officially reserved by
# IANA for private networks. Alternatives like .local (conflicts
# with mDNS/Bonjour), .home (never ratified), and .lan (could
# collide with a future public TLD) all have problems.
# You shouldn't need to change this.
internal_domain: "internal"
```

### Section 2: Network Segments (VLANs)

Each VLAN is defined with all its knobs:

**Design constraint:** All templates iterate `segments` generically —
no segment name is ever hardcoded in Jinja. Adding, removing, or renaming
a segment is a one-place change in `network.yml`. Templates must handle
any combination of options (null SSIDs, missing bandwidth, etc.) gracefully.

```yaml
# ─── Network Segments ─────────────────────────────────────
# Your network is divided into isolated segments. Devices in
# one segment cannot talk to devices in another unless you
# explicitly allow it below.
#
# Think of segments like rooms with locked doors between them.
# You decide which doors to unlock, and in which direction.

segments:
  trusted:
    vlan_id: 10
    comment: "Your personal devices — laptops, phones, tablets"
    subnet: "10.10.0.0/24"

    # ── WiFi ──
    # SSID: the WiFi network name your devices connect to.
    # Set to null for wired-only segments (no WiFi broadcast).
    ssid: "{{ household_name }}"
    wifi_bands: [2ghz, 5ghz]

    # Hidden SSID: when true, the network name won't appear in
    # WiFi scans. Devices must know the name to connect.
    # Slightly more private, slightly less convenient.
    ssid_hidden: false

    # ── Bandwidth ──
    # Max bandwidth for all devices in this segment combined.
    # Set to null for unlimited (full speed of your internet).
    # Format: "100M" (megabits), "1G" (gigabits)
    bandwidth_up: null
    bandwidth_down: null

    # ── DNS ──
    # Which DNS resolver devices in this segment use.
    #   "filtered"    → Pi-hole (ads blocked, queries logged per your privacy settings)
    #   "unfiltered"  → Public DNS directly (1.1.1.1, 8.8.8.8) — no filtering
    #   "none"        → No DNS at all (quarantine)
    dns: "filtered"

    # ── DNS Redirect ──
    # Force all port-53 traffic to your Pi-hole, even if a device
    # has hardcoded DNS (Google Home → 8.8.8.8, Roku → etc.)
    #
    # true:  all DNS queries are intercepted and answered by Pi-hole
    # false: devices that hardcode their own DNS server bypass Pi-hole
    #
    # Most people want true for IoT, and either way for trusted.
    force_dns: false

    # ── Internet ──
    #   "full"         → unrestricted internet access
    #   "egress_only"  → can reach the internet, but nothing on the
    #                     internet can initiate a connection inward
    #   "none"         → no internet access at all
    internet: "full"

    # ── Client Isolation ──
    # When true, devices in this segment cannot see each other.
    # Useful for guest WiFi (strangers shouldn't see each other)
    # or work devices (employer-managed, keep them apart).
    client_isolation: false

  media:
    vlan_id: 20
    comment: "TVs, streaming boxes, printers, cast targets"
    subnet: "10.20.0.0/24"
    ssid: null  # wired or MAC-based assignment
    dns: "filtered"
    force_dns: true
    internet: "full"
    client_isolation: false
    bandwidth_up: null
    bandwidth_down: null

  iot:
    vlan_id: 30
    comment: "Smart home devices — sensors, locks, appliances"
    subnet: "10.30.0.0/24"
    ssid: "{{ household_name }}-IoT"
    wifi_bands: [2ghz]
    ssid_hidden: false
    dns: "filtered"
    force_dns: true   # IoT devices love hardcoding DNS
    internet: "egress_only"
    client_isolation: false
    bandwidth_up: "10M"
    bandwidth_down: "10M"

  servers:
    vlan_id: 40
    comment: "Your servers — DNS, file shares, home automation"
    subnet: "10.40.0.0/24"
    ssid: null  # wired only
    dns: "filtered"
    force_dns: false
    internet: "full"
    client_isolation: false
    bandwidth_up: null
    bandwidth_down: null

  guest:
    vlan_id: 50
    comment: "Visitor WiFi — internet only, devices can't see each other"
    subnet: "10.50.0.0/24"
    ssid: "{{ household_name }}-Guest"
    wifi_bands: [2ghz, 5ghz]
    ssid_hidden: false
    dns: "filtered"
    force_dns: true
    internet: "full"
    client_isolation: true
    bandwidth_up: "50M"
    bandwidth_down: "50M"

  quarantine:
    vlan_id: 70
    comment: "Unknown devices land here — no internet, no local access"
    subnet: "10.70.0.0/24"
    ssid: null
    dns: "none"
    force_dns: true   # block even hardcoded DNS
    internet: "none"
    client_isolation: true
    bandwidth_up: null
    bandwidth_down: null

  admin:
    vlan_id: 99
    comment: "Router and AP management — locked down"
    subnet: "10.99.0.0/24"
    ssid: null  # wired or WireGuard only
    dns: "filtered"
    force_dns: false
    internet: "none"
    client_isolation: false
    bandwidth_up: null
    bandwidth_down: null
```

### Section 2b: Remote Access & DNS Security

```yaml
# ─── Remote Access VPN ────────────────────────────────────
# Connect to your home network from anywhere — your phone
# or laptop gets ad blocking and full access as if you
# were on your home WiFi.
#
#   "wireguard"  → Modern, fast, simple. Requires the free
#                  WireGuard app on each device.
#   "ipsec"      → Built into every OS (iOS, macOS, Windows,
#                  Android) — no app needed. More complex to
#                  configure, heavier on the router CPU.
#   "none"       → No remote access
#
# CHOOSE:
vpn:  # wireguard | ipsec | none

# ─── DNSSEC Validation ───────────────────────────────────
# DNSSEC verifies that DNS answers haven't been tampered
# with in transit. Your recursive resolver (Unbound) checks
# cryptographic signatures on DNS records.
#
# This protects against DNS spoofing attacks. There's no
# real downside — a small number of misconfigured domains
# may fail to resolve, but this is rare.
#
# Note: this is different from encrypted DNS (DoT/DoH).
# Because Unbound talks directly to root servers (full
# recursion), your queries are already as private as
# possible — there's no upstream forwarder to encrypt to.
dnssec: true
```

### Section 3: Permissions (Porosity)

```yaml
# ─── Permissions ──────────────────────────────────────────
# By default, no segment can talk to any other segment.
# Here you unlock specific doors, in one direction at a time.
#
# "can_reach" means devices in this segment can START a
# connection to devices in the listed segments. The other
# side can reply (that's how networking works), but cannot
# start a new connection back unless you also list it.
#
# Example: trusted can reach servers (to use file shares),
# but servers cannot reach trusted (a compromised server
# can't scan your laptop).

permissions:
  trusted:
    can_reach: [servers, media, iot, admin]

  servers:
    can_reach: [iot]  # e.g., Home Assistant controlling smart devices

  # media, iot, guest, quarantine, admin: cannot reach anything
  # (they have no "can_reach" entry, so all doors stay locked)
```

### Section 4: Privacy

```yaml
# ─── Privacy ──────────────────────────────────────────────
# These settings control how much your DNS server remembers
# about what devices on your network are doing. There are no
# defaults — you must make a choice for each one.
#
# Your DNS server sees every domain name every device looks up.
# That's powerful for blocking ads and threats, but it's also
# a detailed record of browsing behavior.

privacy:

  # ── Query Logging ──
  # Your DNS server (Pi-hole) can log every domain lookup.
  # This lets you see what devices are doing (and block things),
  # but it's also a record of everyone's browsing.
  #
  # Options:
  #   "full"        → Log everything: which device, which domain, when.
  #                   You can see that the Roku looked up netflix.com,
  #                   or that a phone looked up medical-site.com.
  #                   Most useful for debugging and auditing IoT.
  #
  #   "anonymous"   → Log which domains were looked up, but not which
  #                   device asked. You can see "someone looked up
  #                   netflix.com" but not who. Good middle ground.
  #
  #   "none"        → No query logging at all. Pi-hole still blocks
  #                   ads, but keeps no record of what was looked up.
  #                   Most private, but you lose visibility.
  #
  # CHOOSE: (delete the other two)
  query_logging:  # full | anonymous | none

  # ── Query History Retention ──
  # If logging is enabled, how long to keep the history.
  # Longer = more useful for spotting patterns, more exposure if
  # someone accesses the DNS server.
  #
  # CHOOSE: (examples: "24h", "7d", "30d", "365d")
  query_retention:  # e.g., "7d"

  # ── Dashboard Visibility ──
  # The network dashboard (if enabled) shows DNS activity.
  # Who should be able to see query logs in the dashboard?
  #
  #   "admin_only"  → Only accessible from the admin segment
  #                   or via password on the portal.
  #
  #   "household"   → Anyone on the trusted segment can view
  #                   the dashboard (they see all devices' queries).
  #
  #   "per_device"  → Each device can only see its own queries
  #                   on the portal. Nobody sees the full picture
  #                   without admin access.
  #
  # CHOOSE:
  dashboard_visibility:  # admin_only | household | per_device

  # ── Apple Private Relay / iCloud ──
  # Apple devices can bypass your DNS entirely using iCloud
  # Private Relay (masks browsing from everyone, including you).
  #
  #   "allow"       → Let Apple Private Relay work. Those devices
  #                   won't appear in your DNS logs and won't get
  #                   ad blocking. This respects the device user's
  #                   privacy choice.
  #
  #   "block"       → Block the relay endpoints so all Apple
  #                   devices use your Pi-hole. Ensures consistent
  #                   ad blocking and full visibility, but overrides
  #                   the device user's preference.
  #
  # Note: even "block" can't prevent a determined user with a VPN.
  # This is about defaults and nudges, not enforcement.
  #
  # CHOOSE:
  apple_private_relay:  # allow | block
```

### Section 5: WiFi Passwords

```yaml
# ─── WiFi Passwords ───────────────────────────────────────
# How passwords are provided. Choose one method:
#
#   "vault"    → Stored in ansible-vault encrypted file (wifi-vault.yml)
#                Run: ansible-vault create wifi-vault.yml
#                Best for: keeping passwords in version control safely
#
#   "env"      → Read from environment variables at deploy time
#                Set: WIFI_TRUSTED_PASS, WIFI_IOT_PASS, WIFI_GUEST_PASS
#                Best for: CI/CD or shared playbooks where each
#                household sets their own passwords
#
#   "prompt"   → Ansible asks you at deploy time
#                Best for: one-off setups, nothing stored
#
# CHOOSE:
wifi_password_source:  # vault | env | prompt
```

### Section 6: Hosts & Services

```yaml
# ─── Hosts ────────────────────────────────────────────────
# Physical machines on your network with fixed roles.

hosts:
  dns_server:
    # Your Pi-hole + Unbound box. This is the most critical
    # host — every device depends on it for DNS.
    hostname: "dns"
    segment: servers
    ip: "10.40.0.2"
    mac: ""  # fill in from the Pi's label or "ip link show"
    services: [pihole, unbound]

  app_server:
    # Optional second host for apps, file shares, dashboards.
    # Can be the same physical box as dns_server if needed,
    # but separating them means an app crash won't kill DNS.
    hostname: "apps"
    segment: servers
    ip: "10.40.0.3"
    mac: ""
    services: [caddy, portal]
    enabled: false  # set true when you have the hardware

# ─── Portals (experimental) ───────────────────────────────
# Web interfaces for managing your network. These are works
# in progress — functional but evolving.

portals:
  me:
    # Shows each device its own info: IP, segment, DNS stats.
    # Accessible from any segment at me.{{ internal_domain }}
    enabled: true

  network:
    # Overview of all segments, connected devices, DNS stats.
    # Access controlled by privacy.dashboard_visibility above.
    enabled: true

  registration:
    # Apple (and some Android) devices use random MAC addresses
    # for privacy. This means your network sees a "new" device
    # every time the random address rotates.
    #
    # The registration portal lets household members voluntarily
    # register their device's real MAC address. This lets the
    # network assign a stable IP, track the device consistently,
    # and apply the right segment rules.
    #
    # This is opt-in: unregistered devices still work, they just
    # may land in quarantine or get a new IP periodically.
    enabled: true
```

---

## Generated Outputs

From `network.yml`, the kit generates:

### 1. RouterOS Scripts (`.rsc`)
Driven by generic segment names (no hardcoded names in templates):
- `01-vlans.rsc` — VLAN interfaces, bridge config
- `02-dhcp.rsc` — IP pools, DHCP servers, DNS assignment per segment
- `03-firewall.rsc` — Inter-segment rules derived from `permissions` map
- `04-wifi.rsc` — SSIDs, security profiles, band assignments, hidden flag
- `05-dns.rsc` — Static DNS entries for `*.internal`
- `06-dns-redirect.rsc` — NAT rules for segments with `force_dns: true`
- `07-bandwidth.rsc` — Queue trees for segments with bandwidth limits

### 2. Pi-hole + Unbound Config
Single-interface design (bulletproof):
- Pi-hole `pihole.toml` — upstream set to local Unbound, privacy level from config
- Unbound `unbound.conf` — recursive resolver, listens on 127.0.0.1:5335 only
- Pi-hole custom DNS entries for all `*.internal` hostnames
- Apple Private Relay block/allow list
- No VLAN sub-interfaces on the DNS box — router handles routing

### 3. Verification, not test generation

Earlier drafts of this document described a generated testinfra firewall
suite plus generated `RULES.md` / `TEST-MATRIX.md` output files. None of
that was built. Their *function* — a plain-English rendering of the rules
and a segment-to-segment expected-connectivity view — was absorbed into
three live commands instead, all reading `network.yml` directly (no
generation step, no separate output files):

- `lankit explain` — human-readable per-segment rule summary (what
  `RULES.md` would have been); `--output` writes it to a file if you want one
- `lankit matrix` — the segment × segment connectivity grid (what
  `TEST-MATRIX.md` would have been)
- `lankit rules` — parses the *actual* generated `.rsc` files in
  `ansible/generated/` and shows the `lankit:`-tagged rules that resulted,
  filterable by `--segment`/`--unit`

`lankit probe` and `lankit audit` are the closest things to the described
test suite: `probe` pings each segment's gateway from the router and
diffs the router's live `lankit:`-tagged config against what `network.yml`
expects; `audit` does a fuller live inventory pass and classifies every
lankit-managed resource as ok/missing/drifted/rogue. Neither generates
SSH-based traffic tests from a device in every VLAN — see "Future / not
built" for that idea's actual status.

The `tests/` directory that exists today is a Playwright suite for the
portal UI (`ansible/roles/portal/files/app/`), unrelated to firewall
verification.

---

## DNS Architecture

Unbound serves as a full recursive resolver — it talks directly to root
nameservers rather than forwarding to a third party. Pi-hole sits in front,
handling ad blocking and query logging. The key design choice: **Unbound
never binds to VLAN interfaces.** This keeps the DNS stack simple and
reliable regardless of how many segments you define.

```
Device (any VLAN)
  → Router (DHCP says "DNS is 10.40.0.2")
  → Router routes packet to servers VLAN
  → Pi-hole (10.40.0.2:53)
  → Unbound (127.0.0.1:5335, localhost only)
  → Root servers (via router's default route)
```

Unbound binds to `127.0.0.1:5335` **only**. It never sees VLAN interfaces. Pi-hole is the only thing listening on port 53, on a single IP. The router handles all the VLAN routing.

The DNS redirect (`force_dns`) is a RouterOS NAT rule per segment:
```
/ip firewall nat
add chain=dstnat src-address=10.30.0.0/24 dst-port=53 protocol=udp \
    action=dst-nat to-addresses=10.40.0.2 to-ports=53 \
    comment="iot: redirect all DNS to Pi-hole"
```

---

## Discovery

lankit already SSHes into the router to provision config. That same
connection gives full visibility into what's on the network: ARP table,
DHCP leases, bridge hosts, WiFi registrations. lankit exposes this as
a first-class discovery interface.

The primary use case: **"I just plugged in a Pi — what is it, and how do
I get its details into network.yml?"**

The actual command has two flags:

```bash
# Scan the router for connected devices (DHCP leases + ARP table)
lankit discover

# First-run wizard — writes a starter network.yml interactively
lankit discover --new

# Point at a router directly, bypassing network.yml (e.g. before it exists)
lankit discover --router 192.168.88.1
```

There is no `--since`, `--vendor`, `--unknown`, `--segment`,
`--update-vendors`, or positional IP/MAC argument, and no separate
`lankit identify` command — see "Future / not built." `discover` always
shows the full merged lease + ARP table in one table; vendor lookup and
private-MAC detection (below) run on every row unconditionally.

Sample output:

```
  IP               MAC                Hostname   Vendor                  DHCP Server   Status
  192.168.88.1     d4:01:c3:00:00:01  router                Routerboard.com         —             —
  192.168.88.47    b8:27:eb:00:00:02  —          Raspberry Pi Foundation             dhcp1        bound
  192.168.88.251   60:45:2e:00:00:03  laptop                                         dhcp1        bound
  192.168.88.69    4c:b9:ea:00:00:04  vacuum     iRobot Corporation                  dhcp1        bound
  192.168.88.14    4a:00:00:00:00:05  my-phone   private MAC                         dhcp1        bound
```

Private MACs are detected via the locally-administered bit (bit 1 of the
first octet) — no OUI lookup needed.

The guided setup wizard (`--new`) does not scan the router at all — it's a
plain prompt sequence (household name, domain, router IP/user, WAN/LAN
interface names, DNS server IP/MAC) that writes a starter `network.yml`
with `trusted`/`iot`/`guest` segments. It doesn't inspect the network or
propose devices to add; that guided "found a new Pi, add it as dns_server?"
flow described in earlier drafts of this document was never built.

### Vendor lookup: mac-vendor-lookup

MAC vendor identification uses the
[`mac-vendor-lookup`](https://pypi.org/project/mac-vendor-lookup/) package
(ships with `pip install lankit`). It maintains a local copy of the IEEE
OUI database and resolves vendor names offline — no network request per
lookup.

```python
from mac_vendor_lookup import MacLookup
m = MacLookup()
m.lookup('B8:27:EB:00:00:02')   # → 'Raspberry Pi Foundation'
m.lookup('4C:B9:EA:00:00:04')   # → 'iRobot Corporation'
m.lookup('D4:01:C3:00:00:01')   # → 'Routerboard.com'
```

Note: `oui` on PyPI is an unrelated audio processing package — do not
use it.

The OUI database ships with the `mac-vendor-lookup` package and updates
whenever that dependency is upgraded. There is no `lankit discover
--update-vendors` flag to refresh it independently.

---

## Comment Convention

Every resource lankit creates on the router is tagged with a structured
comment. This is the API between the config templates, the CLI, and the
router itself. The convention went through one rename early on (`kit:` →
`lankit:`, closed 2026-05-17) — `lankit:` is the only live prefix; nothing
in the codebase emits or reads `kit:` anymore.

**Format:** `lankit:<unit>:<scope>:<resource>`

Examples:
```
lankit:vlan:iot:interface           VLAN interface
lankit:dhcp:iot:pool                DHCP pool
lankit:dhcp:iot:server              DHCP server
lankit:dhcp:iot:network             DHCP network config
lankit:fw:iot:egress                Internet egress rule
lankit:fw:trusted>iot:permit        Permission rule (directional)
lankit:fw:iot:dns-redirect          force_dns NAT rule
lankit:fw:iot:isolation             Client isolation rule
lankit:wifi:iot:ap-2g               WiFi virtual AP
lankit:bw:iot:up                    Bandwidth queue
lankit:dns:roomba:static            Static DNS entry
lankit:dhcp:roomba:lease            Static DHCP lease
lankit:fw:netflix-ban:custom        User-defined custom rule
```

This enables surgical operations:
- `find where comment~"lankit:fw:iot"` → all IoT firewall rules
- `find where comment~"lankit:fw:"` → all firewall rules
- `find where comment~"lankit:.*:iot"` → everything IoT
- `find where comment~"lankit:fw:.*:custom"` → all user-defined rules

Re-provisioning a single segment: remove all resources matching
`lankit:*:<segment>:*`, then re-add from templates. The comment is the
contract — if it has a `lankit:` prefix, lankit owns it. (The exact
tag-to-resource mapping used by the revert mechanism is out of scope for
this pass — see the Rollback & Recovery section.)

---

## CLI

lankit ships a Click-based Python CLI (`lankit/cli/commands/`, one file per
subcommand, all registered in `lankit/cli/__main__.py`). This is the real
command set — every command below exists in the code today. Each command's
`--help` text is the authoritative reference for its flags; what follows is
a map of what each one is for and how they chain together.

### Setup and inspection (no router connection)

```bash
lankit discover --new       # interactive wizard — writes a starter network.yml
lankit overview              # segments, hosts, permissions, privacy — from network.yml
lankit generate               # render network.yml → ansible/generated/*.rsc
lankit explain                 # plain-English per-segment rule summary
lankit matrix                   # segment × segment connectivity grid
lankit rules                     # parses ansible/generated/*.rsc, shows lankit:-tagged rules
lankit diagram                    # GraphViz topology diagram (--format png|svg|pdf, --view)
lankit secrets                     # SSH key + WiFi password setup, guided by wifi_password_source
lankit password-card                # printable WiFi password reference card
lankit rollback-card                 # printable emergency recovery card
```

`explain` and `rules` look similar but read different things: `explain`
renders directly from `network.yml` (no generate step needed); `rules`
parses the actual rendered `.rsc` files, so it reflects whatever was last
generated, not just what `network.yml` currently says.

### Talking to the router

```bash
lankit discover               # scan for devices (DHCP leases + ARP), no --new
lankit discover --router IP   # point at a router directly, bypassing network.yml
lankit apply                   # generate (unless --no-generate) + upload + import all scripts
lankit apply --segment iot      # only that segment's scripts; global rules omitted
lankit apply --script NAME        # apply one script by filename
lankit apply --dry-run              # print what would be applied, no connection
lankit provision                     # run Ansible: Pi-hole/Unbound (+ app server if enabled)
lankit provision --host dns_server    # provision one host
lankit provision --check               # ansible-playbook --check
lankit reset-provision                  # factory-reset + omnibus self-provision (--dry-run to preview)
lankit probe                             # compare live router config to network.yml + gateway pings
lankit audit                              # full live inventory: ok/missing/drifted/rogue per resource
```

`discover` (no flags) and `discover --new` are different modes of the same
command, not separate commands — `--new` skips the router scan entirely
and runs the wizard.

### Safety: snapshots, rollback, restore, failsafe

The safety-mechanism *behavior* — what `rollback`/`restore` actually do to
the router, and the failsafe's revert path — is covered in the Rollback &
Recovery section below. Every one of these commands runs on lankit's
tag-scoped revert mechanism (`docs/design/tag-scoped-revert.md`): recovery
removes everything lankit owns on the router (by `lankit:` tag) and
rebuilds it from the last known-good archive, verified by reading tags
back afterward. It is not a raw config-snapshot replay.

```bash
lankit commit                # save current router config as a named snapshot
lankit snapshots              # list local snapshots
lankit snapshots --capture     # take one on demand
lankit snapshots --delete N     # delete by index or 'latest'
lankit rollback                  # restore the pre-apply snapshot
lankit restore                    # interactively restore any snapshot (--list, or pass a path)
lankit test-failsafe                # rehearse the failsafe scheduler (-s N seconds)
```

There is no `lankit extend` for "reset the failsafe countdown" — `extend`
is a different command entirely (see below). There is no `rollback --list`
or `rollback --to N`; snapshot browsing by index lives on `restore --list`
instead.

### Growing the network

```bash
lankit extend    # interactive wizard: add a new segment to network.yml
                  # (validates VLAN ID / subnet conflicts, writes via ruamel
                  # so existing YAML comments survive). Does not apply —
                  # run 'lankit generate' / 'lankit apply' after.
```

`extend` has nothing to do with the failsafe timer, despite the name
suggesting otherwise in earlier drafts of this document.

### Not real commands

`lankit add`, `lankit move`, `lankit identify`, `lankit rule`,
`lankit status` (renamed `overview`), `lankit test` (there is no testinfra
wrapper), `lankit pki`, and `lankit aws-setup` do not exist. See
"Future / not built."

---

## Rollback & Recovery

The rollback procedure must work with no internet, no Claude, possibly
no laptop — just physical access to the router, SSH or Winbox, and a
printed card.

### The revert mechanism, in short

Every `lankit apply` builds and uploads a single self-contained file,
`lankit-revert.rsc`, to the router *before* touching live config: a
universal remove-pass (strips every RouterOS row tagged `lankit:`, plus a
handful of known untagged mutations) followed by the last known-good
script set, archived at the previous successful apply. Importing the
bundle **converges** the router's lankit-owned state to that known-good
state — it is not a byte-faithful rewind of everything that happened
since, only of what lankit owns. Manual changes made directly on the
router, outside lankit, are left alone.

The wired `192.168.88.0/24` network (any LAN port, defconf addressing) is
the guaranteed fallback for the whole operation — nothing the remove-pass
touches can take it down. WiFi is the one thing that can drop briefly
partway through, while old lankit SSIDs are removed and new ones rebuilt.
Full mechanism, ordering, and accepted costs: `docs/design/
tag-scoped-revert.md`.

A second, unrelated file, `lankit-restore.rsc`, is a raw `/system export`
snapshot — additive-only, and only reliable when imported onto a
near-empty config. It exists solely for the post-factory-reset path
below; the two filenames are kept deliberately distinct so they're never
confused on the router's file listing.

### The rollback card

```bash
lankit rollback-card           # generate the printable text card
lankit password-card           # generate the matching WiFi password card
```

**This command works at any time** — before, during, or after
provisioning. It reads `network.yml` for router credentials, segment
gateways, and SSID names (no passwords — those are on the separate
`lankit password-card`, which is why the two cards are meant to be kept
together and regenerated together). No router connection required. Run
it before you start, print it, keep it near the router.

The card lays out three recovery paths in order of preference:

1. **`lankit rollback`** from a laptop, whenever the router is reachable —
   the preferred path. Verified convergence: a pass/fail you can trust,
   never a silent "done" over a failure.
2. **Manual on-router revert** — `/import file=lankit-revert.rsc` over
   SSH or Winbox, for when a laptop with lankit installed isn't
   available. Same mechanism as (1), same file, safe to re-run if it
   doesn't finish.
3. **Factory reset**, last resort — loses all config. Reconnect on the
   flat `192.168.88.0/24` network afterward, then either re-run `lankit
   apply` for a fresh start, or additive-import the last snapshot with
   `lankit-restore.rsc` (the one case where that kind of import works,
   because the post-reset config is empty).

See `lankit/cli/commands/rollback_card.py` for the generated text.

### lankit rollback / lankit restore

```bash
lankit rollback           # revert to the state before the last apply
lankit restore             # interactively restore an earlier snapshot's state
lankit restore --list       # browse available snapshots
```

Under the tag-scoped revert mechanism, **"restore snapshot X" means:
converge lankit-owned router state to the lankit state that was live when
X was captured** — not replay X. Every snapshot in `~/.lankit/snapshots/`
is paired, at save time, with the known-good archive that was current
then (a copy of the rendered `.rsc` scripts, including WiFi passphrases —
raw exports omit those, and can't be filtered into a rebuild reliably).
`rollback`/`restore` resolve the target snapshot to its paired archive,
build a fresh `lankit-revert.rsc` from [remove-pass] + [that archive],
upload and verify it landed, import it, then read the router's tags back
and compare against what the archive expects. A green success mark is
only ever printed after that verification passes — never after the
import alone.

If the chosen snapshot has no paired archive (it predates this
mechanism, or was saved outside lankit's index), the command says so
plainly and offers two honest options instead of pretending a tag-scoped
restore happened: a **remove-only revert** (strip everything lankit
owns; the router converges to defconf plus whatever's there manually —
the wired flat network stays up, lankit's segments/WiFi/DHCP go away),
or the **factory-reset + additive-import** path.

### Scheduler-based failsafe

`lankit apply` protects every push with a **scheduler-based failsafe**.
This is not RouterOS safe mode (the CTRL+X / F4 kernel session
mechanism) — it's a normal RouterOS `/system scheduler` job, installed
before any script is imported, that survives even if the SSH session
drops.

How it works:

1. Before touching anything, lankit captures the current master-WiFi
   state, builds `lankit-revert.rsc` from the last known-good archive (a
   remove-pass-only bundle on the very first apply, since nothing
   lankit-owned exists yet to rebuild), uploads it to the router, and
   verifies the upload landed at the expected size. Nothing else
   proceeds until that verification passes — no scheduler, no script
   imports, on an unconfirmed file.
2. lankit installs a self-deleting one-shot scheduler job named
   `lankit-failsafe` with a countdown of `failsafe_seconds`. Firing
   removes itself first (so it can't fire twice), imports
   `lankit-revert.rsc`, and leaves behind a durable marker
   (`lankit-failsafe-fired`) recording success or failure — RouterOS's
   own `/log` buffer is small and rotates, too unreliable to be the only
   signal a returning user sees weeks later.
3. lankit uploads and imports each generated `.rsc` script in sequence.
4. Once all scripts are in, the failsafe window is reset so the full
   countdown is available for the decision (not whatever survived the
   upload phase), and lankit asks: **"Keep these changes?"** Answering
   yes disarms the scheduler and the changes stay. **`lankit commit` is
   a separate command** — it only saves a named snapshot and has nothing
   to do with disarming the failsafe. Answering no (or letting the
   countdown expire) imports `lankit-revert.rsc` immediately and
   verifies convergence, printing a loud, unmissable failure — never a
   false success — if it didn't converge.
5. If the SSH session drops mid-apply, the scheduler fires on its own
   countdown and runs the same import + verification, with no laptop
   involved.

This is categorically different from RouterOS safe mode:

| | RouterOS safe mode | lankit failsafe |
|---|---|---|
| Activation | CTRL+X in terminal / F4 in Winbox | Scheduler job installed via SSH |
| Scope | Session-level kernel buffer | RouterOS `/system scheduler` |
| Revert trigger | Session ends or keep-alive timeout | Scheduler fires after `failsafe_seconds` |
| Revert mechanism | Router discards buffered changes | Imports `lankit-revert.rsc` (remove-pass + rebuild), then verifies |
| Survives reconnect | No (tied to the session) | Yes (scheduler persists until cancelled) |

The timeout is configurable in `network.yml`:

```yaml
# lankit apply protects every push with a countdown scheduler on the
# router — not RouterOS "safe mode"; a normal /system scheduler job
# that survives even if your SSH session drops. If it fires, it removes
# everything lankit added and rebuilds the last known-good state.
# Answering "yes" at the apply confirmation prompt is what disarms it —
# lankit commit is a separate command and does not touch the failsafe.
failsafe_seconds: 300
```

Known limitation: if the script set takes longer to import than
`failsafe_seconds`, the scheduler can fire mid-apply. `lankit apply`
resets the countdown after all scripts finish importing and before
asking you to decide, so in practice this only bites during the
upload/import phase itself, not the decision window.

Residual limitation, not fixed by this design: if the router **reboots**
during the failsafe window, the scheduler's start time passes while it's
down and the one-shot never fires on boot. Recovery is then manual —
`lankit rollback`, or on-router `/import file=lankit-revert.rsc` (which
this design at least makes possible and re-runnable).

The only path that bypasses the failsafe entirely is `lankit generate` +
manual `.rsc` apply — the power-user path, where the user has explicitly
chosen to manage changes themselves.

After any failsafe fire — whether you were there to see it or not — run
`lankit probe`. It reads live router tags and reports exactly what's
missing or unexpected against what should be there. lankit also checks
for the durable fired-marker on every subsequent connect and warns
loudly before doing anything else if one is found uncleared.

### Failsafe drill — lankit test-failsafe

Before touching real config, rehearse the failsafe scheduler under low
stakes:

```bash
$ lankit test-failsafe -s 30
```

Today this drills the scheduler *mechanics*, not a full revert:
pre-flight cleanup of any leftover sentinel/scheduler from a prior run,
install the real `lankit-failsafe` scheduler plus a disabled,
`lankit:`-tagged sentinel firewall rule, verify the sentinel is present,
reschedule the timer in place and verify the start-time actually moved,
then wait for the (rescheduled) timer to fire and verify the sentinel is
gone — five phases in total. The on-event shape mirrors production
(self-delete → import → log markers) and the timer/reschedule primitives
are the exact ones `apply.py` uses, but the "import" is a one-line
removal of the sentinel rule, not the production `lankit-revert.rsc` —
so this proves the mechanical scaffolding (scheduler self-delete,
on-event-triggered import, reschedule-in-place) rather than a genuine
remove-and-rebuild of lankit state. No segment, VLAN, WiFi, or DHCP
config is touched by the default drill; it's safe on a live household
network.

A `--full` mode that builds and imports the actual `lankit-revert.rsc`
end-to-end — real churn, brief WiFi/DHCP disruption, meant for the lab
router or a user who wants genuine remove-and-rebuild certification — is
designed (execution plan G1.9, `docs/design/tag-scoped-revert.md` §12)
but not yet implemented. Until it lands, the default drill above is the
only rehearsal available.

---

## Zero-Disruption Adoption

Home networks affect multiple people. lankit is designed so that
the household never experiences a "before and after" moment.

**Phase 1 — Invisible.** Deploy all VLANs, but put everything in
the trusted segment initially. Same SSID, same password. The family
notices nothing. A segmented-but-flat network is a valid, useful state.

**Phase 2 — Quiet migration.** Move devices in the background, one
at a time. IoT devices move silently — the Roomba doesn't know or care
which VLAN it's on. The household notices nothing.

**Phase 3 — Visible additions.** New things appear as additions, not
replacements: a guest network, a device portal, a registration flow
that says "welcome, name your device" rather than "you've been blocked."

**Quarantine and `force_dns`** are explicitly called out in the docs
as Phase 3 features — enable them once you're confident devices work,
not on day one.

---

## Registration Portal

Addresses the Apple Private MAC problem, but more modestly than earlier
drafts of this document claimed. What `register.internal` actually does
today (`ansible/roles/portal/files/app/app.py`):

1. A household member visits `register.internal` from their device.
2. The portal looks up their MAC via the Pi-hole client list for their
   current IP, and shows any existing name.
3. They submit a name (validated: letters/digits/hyphens, not a reserved
   prefix like `register` or `dns`).
4. The portal writes a Pi-hole custom DNS entry (`<name>.internal` →
   their current IP) and updates the Pi-hole client's comment.

There is no static DHCP lease assignment, no segment reassignment, and
nothing is written back to `network.yml` or the router — this is a Pi-hole
DNS naming convenience, not device management. If the device's IP changes
(e.g. a rotating private MAC gets a new DHCP lease), the DNS entry stops
matching until the device re-registers. Segment placement and static
leases are still a manual `network.yml` + `lankit apply` job — see
"Future / not built."

---

## Future / not built

Earlier drafts of this document described several subsystems as if they
existed. They don't. This section is the single place that tracks them, so
the sections above can stay honest about current behavior. Where a listed
idea's *function* was absorbed by something real, that's noted.

- **Device management commands** — `lankit add`, `lankit move`,
  `lankit identify`, `lankit rule` (custom per-device firewall rules) and
  the SQLite-backed device registry they'd write to. Today, adding or
  moving a device means editing `network.yml`'s `hosts:` and segment
  definitions by hand and re-running `lankit apply`. There is no
  persistent device registry anywhere in the codebase.
- **`lankit status`** — renamed to `lankit overview` (already done); the
  old name is dead, not a synonym.
- **`lankit test`** — a testinfra wrapper for a generated firewall test
  suite. Never built. `lankit probe` and `lankit audit` (real, live-router
  checks) and `lankit rules`/`explain`/`matrix` (real, config-derived
  checks) cover most of the intended use — see "Verification, not test
  generation" above. A true SSH-into-every-segment traffic test is still
  open (tracked as post-1.0 hardware-breadth work).
- **`RULES.md` / `TEST-MATRIX.md` generated files** — absorbed by
  `lankit explain` and `lankit matrix`, which render on demand instead of
  writing files (unless you pass `--output`).
- **`lankit discover --vendor / --segment / --unknown / --update-vendors
  / --since`, a positional device argument** — `discover` only has
  `--new` and `--router` today. Filtering, per-device lookup, and
  "unregistered device" detection don't exist.
- **`lankit pki --share`** and locally-issued `*.internal` TLS via mkcert —
  deliberately deferred (see the PKI/TLS section above). Portals are HTTP-only
  today.
- **`lankit aws-setup`** — no AWS integration of any kind exists.
- **The `vpn:` config field** — `network.yml` has a `vpn:` CHOOSE field
  (wireguard/ipsec/none), but nothing reads it except to write `none`
  through the setup wizard. No WireGuard or IPsec template exists. Choose
  `none` until this lands.
- **Registration portal assigning static leases / segments** — the
  portal (`register.internal`) exists and works, but only writes a
  Pi-hole custom DNS entry + client comment for the requesting IP. It
  does not touch `network.yml`, static DHCP leases, or segment
  assignment — see the Registration Portal section above.

---

## File Structure

This reflects the actual layout, checked against the repo — not an
aspirational one.

```
lankit/
├── DESIGN.md                ← this file
├── README.md, TESTING.md
├── network.yml.dist         ← template — copy to network.yml and fill in CHOOSE fields
├── network.yml               ← user's private config (gitignored)
├── network.schema.json         ← JSON Schema for validation
│
├── lankit/
│   ├── core/                    ← shared Python library
│   │   ├── config.py            ← reads + validates network.yml
│   │   ├── generator.py         ← Jinja2 template rendering → .rsc scripts
│   │   ├── router.py            ← RouterOS SSH connection (RouterConnection)
│   │   ├── snapshots.py         ← local config snapshot management
│   │   └── passwords.py         ← WiFi password sourcing (vault/env/prompt)
│   │
│   └── cli/
│       ├── __main__.py          ← Click group; registers every command below
│       └── commands/            ← one file per subcommand (see CLI section)
│           ├── discover.py, status.py (registers as "overview")
│           ├── generate.py, apply.py, provision.py, reset_provision.py
│           ├── explain.py, matrix.py, rules.py, diagram.py, audit.py, probe.py
│           ├── commit.py, snapshots_cmd.py, rollback.py, restore.py
│           ├── test_failsafe.py, rollback_card.py, password_card.py
│           └── secrets.py, extend.py
│
├── ansible/
│   ├── site.yml              ← main playbook (run by lankit provision)
│   ├── roles/
│   │   ├── router/           ← MikroTik .rsc templates
│   │   │   └── templates/
│   │   │       ├── 01-vlans.rsc.j2
│   │   │       ├── 02-dhcp.rsc.j2
│   │   │       ├── 03-firewall.rsc.j2
│   │   │       ├── 04-wifi.rsc.j2
│   │   │       ├── 05-dns.rsc.j2
│   │   │       ├── 06-dns-redirect.rsc.j2
│   │   │       └── 07-bandwidth.rsc.j2
│   │   ├── dns-server/       ← Pi-hole + Unbound (single role)
│   │   ├── caddy/            ← Caddy web server for app_server
│   │   └── portal/           ← the Flask app (files/app/app.py + templates),
│   │                            deployed by Ansible, served by Caddy. Beta,
│   │                            optional, off by default (hosts.app_server.enabled)
│   └── generated/            ← output: rendered .rsc scripts only (gitignored)
│
├── tests/                    ← Playwright suite for the portal UI
│   ├── conftest.py
│   ├── interfaces/, mixins/
│   └── test_me.py, test_network.py, test_register.py, test_apps.py
│
└── docs/
    ├── design/               ← design docs for shipped behaviour
    ├── apps/                 ← portal specs (architecture.md, me/network/register/apps-internal.md)
    └── screenshots/

(Planning material — mission, personas, the execution plan, and the frozen
 ENH-001..025 archive — lives in the separate lankit-workshop repo.)
```

There is no `lankit/web/` FastAPI package, no `core/routeros.py` /
`pihole.py` / `registry.py`, and no top-level `core/` — the real core
library lives under `lankit/core/` with the five files listed above.
Anything resembling a device registry (SQLite or otherwise) does not
exist; see "Future / not built."

---

## README / GitHub Presence

The README is the first-time experience for anyone discovering lankit.
Someone on GitHub decides in 30 seconds whether to read further.

**Structure:**

```markdown
# lankit

> A home network you actually understand.

[diagram here — see below]

One config file. Segmented VLANs. Pi-hole DNS filtering.
Plain-English rules. Automated firewall tests.

Built for MikroTik + Raspberry Pi. ~10 hours to a network
you'd be happy to share.
```

The diagram is the hook — not documentation. It shows a stranger exactly
what they'd get: their devices, organized, with trust relationships visible.
A Rich tree in the README says "CLI tool." The GraphViz diagram says
"this person thought about my network."

**The diagram ships with lankit** — generated from the sample `network.yml`
that comes with the repo (a fictional household, not the user's real data).
Device names are generic but believable: a laptop, a phone, a Roomba, a TV.
The quarantine node showing an unresolved/unknown device is deliberately
included — it shows the system is *alive*, not just configured. (There is
no `lankit identify` command to point at in the label — see "Future / not
built.")

**GraphViz is required for `lankit diagram` specifically** — every other
lankit command works without it. If the `graphviz` Python package isn't
installed, or the `dot` binary isn't on PATH, `lankit diagram` prints a
clear error and exits; it does not currently fall back to an ASCII tree
view (an earlier draft of this document described one — it was never
built).

**Real diagram flags:**

```bash
lankit diagram                          # network.png in the current directory
lankit diagram --format svg             # or png / pdf
lankit diagram --output mynet.png       # choose the output path
lankit diagram --view                   # open it after generating
```

There is no `--dot` or `--ascii` flag, and nothing writes a `.dot` source
file automatically on `lankit provision`.
