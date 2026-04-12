# lankit — Design Document

A distributable, self-documenting home network segmentation toolkit for MikroTik routers with Pi-hole DNS filtering. Aimed at technical people who appreciate craft and will invest up to 10 hours for agency over their home network.

---

## Philosophy

- **One config file** (`network.yml`) is both the setup guide and the source of truth
- **No network knowledge assumed** — concepts explained inline, briefly
- **Privacy choices are explicit** — no defaults, must be decided
- **Templates generate everything** — RouterOS scripts, Pi-hole config, Unbound config, firewall tests, plain-English rules
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

The graphviz binary is only needed for `lankit diagram --png/--svg`.
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
| **AWS account** *(optional)* | Off-site backup of file shares | Free tier sufficient. Create a scoped IAM user — lankit can do this for you with `lankit aws-setup` |

### PKI / TLS *(optional but recommended)*

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

**Trust propagation:** the local CA certificate needs to be installed on
every device that will access `*.internal` services (phones, other laptops).
lankit generates a QR code and instructions for this:

```bash
lankit pki --share       # prints QR code + URL for CA cert download
                         # served from your app server at pki.internal
```

iOS and Android users visit `pki.internal` from their browser, tap to install
the CA profile. One-time per device.

**If you skip TLS:** everything works over HTTP. Browser warnings appear for
`*.internal` sites. Acceptable for a single-person setup; less so if household
members use the portals regularly.

---

## Config file: network.yml

The single file a user fills out. Organized in sections.

### Section 1: Household

```yaml
# ─── Your Network ─────────────────────────────────────────
# This name appears in your WiFi SSIDs and internal hostnames.
# Example: "Peak" → WiFi SSIDs "Peak", "Peak-IoT", "Peak-Guest"
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
  device_portal:
    # Shows each device its own info: IP, segment, DNS stats.
    # Accessible from any segment at device.{{ internal_domain }}
    hostname: "device"
    enabled: true

  network_dashboard:
    # Overview of all segments, connected devices, DNS stats.
    # Access controlled by privacy.dashboard_visibility above.
    hostname: "network"
    enabled: true

  registration_portal:
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
    hostname: "register"
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

### 3. Firewall Tests
Generated from `permissions` + `segments`:

#### a) Firewall test suite (`tests/`)

Tests are written with [**testinfra**](https://testinfra.readthedocs.io/),
a pytest plugin for infrastructure testing. testinfra runs on your workstation
and SSHes into target hosts to execute probes — nothing is installed on the
tested machines. If you've used pytest, you already know the workflow:
`pip install pytest-testinfra`, then `pytest tests/`.

- **Docs:** https://testinfra.readthedocs.io/en/latest/
- **PyPI:** https://pypi.org/project/pytest-testinfra/
- **Source:** https://github.com/pytest-dev/pytest-testinfra

Tests use the same SSH credentials and inventory as Ansible. testinfra
connects to a host, runs a command (dig, curl, nc), and asserts on the
result. When a test fails, pytest shows you exactly what happened —
the command, expected output, actual output — not just "Assertion failed."

**How segments are tested without a device in every VLAN:**

The MikroTik router has an interface on every segment. RouterOS
`/tool/fetch` and `/ping` can source traffic from any interface,
simulating a device in that segment. Combined with SSH-able hosts
in segments that have them (e.g., system76 in trusted, Pi in servers),
this gives full matrix coverage:

```python
# Run from your workstation, SSHes into the router
def test_iot_cannot_reach_trusted(router):
    """IoT devices must not initiate connections to trusted."""
    cmd = router.run(
        "/tool/fetch url=http://10.10.0.19:80 "
        "src-address=10.30.0.1 mode=http duration=3s"
    )
    assert cmd.rc != 0

# SSHes into system76 (trusted segment)
def test_trusted_can_reach_servers(system76):
    """Trusted devices can reach services in the servers segment."""
    cmd = system76.run("curl -sf --max-time 3 http://10.40.0.2:80")
    assert cmd.rc == 0
```

Tests are generated from `network.yml`: the `permissions` map and segment
definitions produce the full expected connectivity matrix. Adding or
removing a segment automatically updates the test suite.

**Ephemeral by design:** tests run read-only probes over SSH. No scripts
are copied to target hosts, no packages installed, no artifacts left behind.

#### b) Plain-English rules (`RULES.md`)
Auto-generated human-readable doc:

```markdown
## Network Rules

### Trusted (VLAN 10 — "Peak")
- Can reach: Servers, Media, IoT, Admin
- Internet: full, unrestricted
- DNS: filtered through Pi-hole
- Hardcoded DNS override: allowed (force_dns is off)
- Devices can see each other

### IoT (VLAN 30 — "Peak-IoT")
- Cannot reach: any other segment
- Internet: outbound only (nothing inbound)
- DNS: filtered through Pi-hole
- Hardcoded DNS: intercepted and redirected to Pi-hole
- Bandwidth: 10 Mbps up / 10 Mbps down
- Devices can see each other

### Guest (VLAN 50 — "Peak-Guest")
- Cannot reach: any other segment
- Internet: full, unrestricted
- DNS: filtered through Pi-hole
- Hardcoded DNS: intercepted and redirected to Pi-hole
- Bandwidth: 50 Mbps up / 50 Mbps down
- Devices CANNOT see each other (client isolation)

### Quarantine (VLAN 70)
- Cannot reach: any other segment
- Internet: none
- DNS: none
- Devices CANNOT see each other (client isolation)
- Unknown devices land here until registered
```

#### c) Test matrix (`TEST-MATRIX.md`)
Every segment-to-segment pair with expected result:

```
             trusted  media  iot  servers  guest  quarantine  admin  internet
trusted         —       Y     Y      Y       —        —         Y       Y
media           —       —     —      —       —        —         —       Y
iot             —       —     —      —       —        —         —     Y(out)
servers         —       —     Y      —       —        —         —       Y
guest           —       —     —      —      iso       —         —       Y
quarantine      —       —     —      —       —       iso        —       —
admin           —       —     —      —       —        —         —       —
```

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

```bash
# Show everything on the flat/management network
lankit discover

# Only devices that appeared recently (default: last 10 minutes)
lankit discover --new
lankit discover --new --since 30m

# Filter by vendor
lankit discover --vendor "Raspberry Pi"
lankit discover --vendor apple

# Full detail on a specific device
lankit discover 192.168.88.47
lankit discover B8:27:EB:3C:AA:12

# Devices not yet in network.yml
lankit discover --unknown

# Devices in a specific segment (once VLANs are live)
lankit discover --segment iot
```

Sample output:

```
  IP               MAC                Vendor                  Name          Segment
  192.168.88.1     d4:01:c3:44:6b:96  Routerboard.com         router        (this router)
  192.168.88.47    b8:27:eb:3c:aa:12  Raspberry Pi Found.     —             ← new, 2m ago
  192.168.88.251   60:45:2e:74:22:dc  System76                system76      trusted
  192.168.88.69    4c:b9:ea:58:58:70  iRobot Corporation      roomba        iot
  192.168.88.14    4a:21:72:48:3a:20  (private/randomized)    jason-phone   trusted
  192.168.88.34    f6:ec:1c:2c:5f:93  (private/randomized)    —             ⚠ unknown
```

Private MACs are detected via the locally-administered bit (bit 1 of the
first octet) — no OUI lookup needed. The label `(private/randomized)`
tells the user why the vendor is unknown and links naturally to the
registration portal.

The guided "new Pi" flow:

```
$ lankit discover --new

  Found 1 new device:
  192.168.88.47    b8:27:eb:3c:aa:12  Raspberry Pi Foundation   appeared 2m ago

  Add to network.yml as dns_server? [y/N] y
  ✓  network.yml updated. Run: lankit provision dns-server
```

### Vendor lookup: mac-vendor-lookup

MAC vendor identification uses the
[`mac-vendor-lookup`](https://pypi.org/project/mac-vendor-lookup/) package
(ships with `pip install lankit`). It maintains a local copy of the IEEE
OUI database and resolves vendor names offline — no network request per
lookup.

```python
from mac_vendor_lookup import MacLookup
m = MacLookup()
m.lookup('B8:27:EB:3C:AA:12')   # → 'Raspberry Pi Foundation'
m.lookup('4C:B9:EA:58:58:70')   # → 'iRobot Corporation'
m.lookup('D4:01:C3:44:6B:96')   # → 'Routerboard.com'
```

Note: `oui` on PyPI is an unrelated audio processing package — do not
use it.

The OUI database is bundled with the package but can be refreshed:

```bash
lankit discover --update-vendors   # pulls latest IEEE OUI list
```

---

## Comment Convention

Every resource the kit creates on the router is tagged with a structured
comment. This is the API between the config templates, the CLI, the test
suite, and the router itself.

**Format:** `kit:<unit>:<scope>:<resource>`

Examples:
```
kit:vlan:iot:interface           VLAN interface
kit:dhcp:iot:pool                DHCP pool
kit:dhcp:iot:server              DHCP server
kit:dhcp:iot:network             DHCP network config
kit:fw:iot:egress                Internet egress rule
kit:fw:trusted>iot:permit        Permission rule (directional)
kit:fw:iot:dns-redirect          force_dns NAT rule
kit:fw:iot:isolation             Client isolation rule
kit:wifi:iot:ap-2g               WiFi virtual AP
kit:bw:iot:up                    Bandwidth queue
kit:dns:roomba:static            Static DNS entry
kit:dhcp:roomba:lease            Static DHCP lease
kit:fw:netflix-ban:custom        User-defined custom rule
```

This enables surgical operations:
- `find where comment~"kit:fw:iot"` → all IoT firewall rules
- `find where comment~"kit:fw:"` → all firewall rules
- `find where comment~"kit:.*:iot"` → everything IoT
- `find where comment~"kit:fw:.*:custom"` → all user-defined rules

Re-provisioning a single segment: remove all resources matching
`kit:*:<segment>:*`, then re-add from templates. The comment is the
contract — if it has a `kit:` prefix, the kit owns it.

---

## CLI

The kit ships a Python CLI for managing the network without editing
config files or SSHing into the router manually.

### Device management

```bash
# Add a device to a segment
lankit add iot roomba --mac 4C:B9:EA:58:58:70
  → static DHCP lease, DNS entry (roomba.internal), device registry

# Move between segments
lankit move roomba trusted
  → removes old lease/DNS, creates new in trusted, updates registry

# Identify a device
lankit identify 4C:B9:EA:58:58:70
  → "roomba, IoT segment, 10.30.0.12, last seen 2m ago"

# Promote from quarantine
lankit add iot --from-quarantine "the new smart plug"
  → lists quarantine devices if ambiguous, moves to IoT with a name
```

### Firewall rules

```bash
# Add a custom rule
lankit rule add netflix-ban --segment iot --action drop --dst-fqdn netflix.com
  → Pi-hole DNS block + firewall rule, tagged kit:fw:netflix-ban:custom

# Manage custom rules
lankit rule disable netflix-ban
lankit rule list
lankit rule list --segment iot
```

### Provisioning

```bash
# Single segment
lankit provision iot

# Full network
lankit provision --all

# Status overview
lankit status

# Run test suite
lankit test
lankit test --segment iot

# Print rules in plain English
lankit rules

# Restore full network from network.yml (no Ansible syntax required)
lankit restore
lankit restore --dry-run

# Safe mode — commit or let the router auto-revert
lankit commit               # make changes permanent after provision/restore
lankit extend               # reset the failsafe countdown (more time to verify)

# Commit history and rollback
lankit rollback             # undo the last committed change
lankit rollback --list      # show commit history
lankit rollback --to 3      # roll back to 3 commits ago

# Failsafe drill — rehearse the full workflow before touching real config
lankit test-failsafe

# Generate rollback card (works any time, no router connection needed)
lankit rollback-card
lankit rollback-card --html
```

### Raw .rsc mode

Power users who prefer working with RouterOS scripts directly can
generate and apply `.rsc` files without the CLI abstraction:

```bash
# Generate .rsc for a single segment
lankit generate --segment iot --output generated/

# Generate all .rsc files
lankit generate --all --output generated/

# Apply a specific .rsc file to the router
lankit apply generated/03-firewall.rsc

# Dry-run: show what would change (diff against router state)
lankit apply generated/03-firewall.rsc --dry-run
```

This mode is useful for reviewing exact RouterOS commands before
they run, version-controlling the generated scripts, or integrating
with existing workflows. The `.rsc` files use the same comment
convention, so they interoperate cleanly with CLI-managed resources.

---

## Interface Contract

The CLI, the web portals, and raw `.rsc` mode are three interfaces
to the same system. Maintaining them independently would be
unsustainable. Instead, all three derive from a shared contract:

```
network.yml (source of truth)
      │
      ├── jsonschema (validates config)
      │
      ├── Python core library
      │     ├── config.py     ← reads/validates network.yml
      │     ├── routeros.py   ← SSH + comment convention CRUD
      │     ├── pihole.py     ← Pi-hole API/config
      │     ├── registry.py   ← SQLite device registry
      │     └── generator.py  ← Jinja2 template rendering
      │
      ├── CLI (click or typer)
      │     └── thin wrappers around core library
      │
      ├── Web portals (FastAPI + htmx)
      │     └── thin wrappers around core library
      │
      └── .rsc generation (Jinja2 templates)
            └── uses generator.py from core library
```

Every operation — adding a device, changing a rule, provisioning a
segment — is a function in the core library. The CLI calls it. The
portal calls it. The Ansible playbook calls it. There is one
implementation, multiple entry points.

The FastAPI endpoints map 1:1 to CLI commands. The htmx portal is
a browser-friendly skin over the same endpoints. If a CLI command
exists, the portal can expose it — and vice versa — without
duplicating logic.

---

## Rollback & Recovery

The rollback procedure must work with no internet, no Claude, possibly
no laptop — just physical access to the router and a printed page.

### The rollback card

```bash
lankit rollback-card           # generate and open PDF
lankit rollback-card --html    # printable HTML alternative
```

**This command works at any time** — before, during, or after provisioning.
It reads `network.yml` (for WiFi names) and nothing else. No router
connection required. Run it before you start, print it, tape it somewhere.

Generated content:

```
NETWORK ROLLBACK CARD
Household: Maple          Generated: 2026-04-07     Router: MikroTik hAP ax³

━━━ IF THE INTERNET STOPS WORKING ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

OPTION A — Restore lankit config (keeps your segments)
  1. Connect laptop to router via ethernet cable
  2. Open terminal in your lankit directory
  3. Run: lankit restore

OPTION B — Full factory reset (flat network, always works)
  1. Unplug router power, wait 10 seconds
  2. Hold the reset button on the router
  3. Plug power back in — keep holding until LED flashes, then release
  4. Connect laptop to router via ethernet cable
  5. Open browser → http://192.168.88.1
  6. Login: admin / (no password)
     ✓ You now have a working flat network.
  7. When ready to restore lankit: run lankit restore

━━━ WIFI PASSWORDS ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Maple:            ______________________________
  Maple-IoT:        ______________________________
  Maple-Guest:      ______________________________

━━━ KEY ADDRESSES ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Router:     192.168.88.1
  DNS server: 10.40.0.2
  Fallback:   http://192.168.88.1  (router admin, always reachable via ethernet)
```

WiFi passwords are blank — fill in by hand after printing. Never stored
in plain text. The card lives on the fridge or taped inside a cabinet.

### lankit restore

`lankit restore` re-provisions the full network from `network.yml`. It is
the only restore command a user ever needs to know. No Ansible syntax,
no playbook paths, no flags.

```bash
lankit restore           # re-provision everything from network.yml
lankit restore --dry-run # show what would change without applying
```

Under the hood it runs the Ansible playbook — that's an implementation
detail users never need to know.

### Scheduler-based failsafe

`lankit apply` protects every push with a **scheduler-based failsafe**. This
is not RouterOS safe mode (the CTRL+X / F4 kernel mechanism) — it is a
RouterOS scheduler job that lankit installs before uploading any scripts.

How it works:

1. Before touching anything, lankit exports the current router config and
   saves it locally as a snapshot. It also uploads the snapshot to the router
   as `lankit-restore.rsc`.

2. lankit installs a RouterOS scheduler job named `lankit-failsafe` with a
   countdown of `failsafe_seconds`. The job's on-event is:
   ```
   /import file=lankit-restore.rsc
   ```

3. lankit uploads and imports each `.rsc` script in sequence.

4. After all scripts are applied, lankit prompts you to confirm. If you
   confirm, the scheduler is cancelled and the changes are permanent. If you
   decline, lankit cancels the scheduler and immediately runs the restore
   itself.

5. If the SSH session drops mid-apply — because a script broke routing, the
   laptop lid closed, etc. — the scheduler fires automatically when the
   countdown expires and imports the pre-apply snapshot, reverting the router
   to its last known-good state.

This is categorically different from RouterOS safe mode:

| | RouterOS safe mode | lankit failsafe |
|---|---|---|
| Activation | CTRL+X in terminal / F4 in Winbox | Scheduler job installed via SSH |
| Scope | Session-level kernel buffer | RouterOS `/system scheduler` |
| Revert trigger | Session ends or keep-alive timeout | Scheduler fires after `failsafe_seconds` |
| Revert mechanism | Router discards buffered changes | `/import file=lankit-restore.rsc` |
| Survives reconnect | No (tied to the session) | Yes (scheduler persists until cancelled) |

The timeout is configurable in `network.yml`:

```yaml
# How long before the failsafe scheduler fires if not cancelled.
# Longer gives more time to verify; shorter limits exposure if something
# goes wrong. Must be long enough that the apply itself completes before
# the window expires — if the apply takes longer than failsafe_seconds,
# the scheduler fires during the apply and reverts a partial state.
# The default of 120 seconds is generous for most home-network script sets.
failsafe_seconds: 120
```

Known limitation: if your script set takes longer to import than
`failsafe_seconds`, the scheduler fires mid-apply and the router reverts to
the pre-apply snapshot mid-run. Increase `failsafe_seconds` if you have a
large or slow-executing script set. The `lankit test-failsafe` command
verifies the scheduler fires and reverts correctly before you trust it on a
live apply.

The only path that bypasses the failsafe entirely is `lankit generate` +
manual `.rsc` apply — the power-user path, where the user has explicitly
chosen to manage changes themselves.

### Commit history and rollback

Every `lankit commit` saves a RouterOS config snapshot (via `/system export`)
and pushes it onto a local stack. `lankit rollback` restores the previous
snapshot — the same command whether you're undoing a test, a bad provision,
or a mistaken rule change.

```bash
lankit rollback          # undo the last committed change
lankit rollback --list   # show commit history
lankit rollback --to 3   # roll back to 3 commits ago
```

The stack:
```
lankit provision → commit  [1] initial segments
lankit provision → commit  [2] added guest bandwidth limit
lankit provision → commit  [3] added netflix-ban rule   ← lankit rollback undoes this
```

### Failsafe drill — lankit test-failsafe

Before touching real config, users should rehearse the full safe mode
workflow under low stakes. `lankit test-failsafe` is that rehearsal.

```bash
$ lankit test-failsafe

  Applying test change in safe mode:
    failsafe-test.internal → 10.99.99.99

  Verify it's live:
    dig +short failsafe-test.internal @10.40.0.2
    → should return 10.99.99.99

  Now choose a path to test:

  PATH A — test auto-revert:
    Let the 120-second window expire without running 'lankit commit'.
    Then verify the entry is gone:
    dig +short failsafe-test.internal @10.40.0.2
    → should return nothing

  PATH B — test commit + rollback:
    lankit commit
    dig +short failsafe-test.internal @10.40.0.2   → 10.99.99.99
    lankit rollback
    dig +short failsafe-test.internal @10.40.0.2   → nothing
```

This is a genuine rehearsal of the real workflow — `lankit rollback` here
is the same command used for real rollbacks. No special flags, no cleanup
command. Users learn the real tools under safe conditions.

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

Solves the Apple Private MAC problem:

1. New device joins WiFi → lands in quarantine (or assigned segment if MAC is known)
2. User opens browser → captive portal redirect to `register.internal`
3. Portal shows: "Welcome to [household]. Register this device to get internet access."
4. User enters: device name, who it belongs to
5. Portal records: real MAC (from ARP), display name, owner, timestamp
6. Ansible can then: assign static lease, place in correct segment, give it a `*.internal` name

For Apple devices: the portal explains what Private MAC is, and offers a link to the iOS Settings path to disable it for this network. Registration works either way — it just means the device may get a new IP when the MAC rotates.

---

## File Structure

```
lankit/
├── DESIGN.md                ← this file
├── network.yml              ← user fills this out
├── network.schema.json      ← jsonschema for validation
│
├── core/                    ← shared Python library
│   ├── config.py            ← reads + validates network.yml
│   ├── routeros.py          ← SSH + comment convention CRUD
│   ├── pihole.py            ← Pi-hole API/config
│   ├── registry.py          ← SQLite device registry
│   └── generator.py         ← Jinja2 template rendering
│
├── cli/                     ← CLI entry point (python -m lankit)
│   ├── __main__.py
│   └── commands/
│       ├── discover.py      ← network discovery, vendor lookup, guided Pi setup
│       ├── add.py           ← device add/move/identify
│       ├── rule.py          ← custom firewall rules
│       ├── provision.py     ← segment/full provisioning
│       ├── generate.py      ← .rsc file generation
│       ├── apply.py         ← .rsc application + dry-run
│       ├── status.py        ← network status
│       ├── test.py          ← testinfra wrapper
│       ├── restore.py       ← full re-provision from network.yml
│       ├── commit.py        ← confirm safe mode, push to commit stack
│       ├── extend.py        ← reset failsafe countdown
│       ├── rollback.py      ← restore previous commit from stack
│       ├── test_failsafe.py ← rehearse safe mode workflow with DNS entry
│       └── rollback_card.py ← generate printable PDF/HTML rollback card
│
├── web/                     ← FastAPI + htmx portals
│   ├── app.py               ← FastAPI application
│   ├── templates/           ← Jinja2 + htmx templates
│   └── static/              ← htmx.min.js, CSS
│
├── ansible/
│   ├── site.yml             ← main playbook
│   ├── inventory.yml        ← generated from hosts section
│   ├── roles/
│   │   ├── router/          ← MikroTik .rsc templates
│   │   │   └── templates/
│   │   │       ├── 01-vlans.rsc.j2
│   │   │       ├── 02-dhcp.rsc.j2
│   │   │       ├── 03-firewall.rsc.j2
│   │   │       ├── 04-wifi.rsc.j2
│   │   │       ├── 05-dns.rsc.j2
│   │   │       ├── 06-dns-redirect.rsc.j2
│   │   │       └── 07-bandwidth.rsc.j2
│   │   ├── dns-server/      ← Pi-hole + Unbound (single role)
│   │   │   └── templates/
│   │   │       ├── pihole.toml.j2
│   │   │       └── unbound.conf.j2
│   │   ├── portal/          ← deploys FastAPI app to app_server
│   │   └── caddy/           ← reverse proxy for *.internal
│   └── generated/           ← output: .rsc scripts, RULES.md, TEST-MATRIX.md
│
├── tests/                   ← testinfra suite
│   ├── conftest.py          ← fixtures: router, hosts per segment
│   ├── test_firewall.py     ← inter-segment connectivity matrix
│   ├── test_dns.py          ← resolution + redirect + filtering
│   └── test_bandwidth.py    ← queue enforcement
│
└── docs/
    └── privacy-choices.md   ← standalone deep-dive on privacy options
```

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
The quarantine node showing `⚠ unknown device — run: lankit identify` is
deliberately included — it shows the system is *alive*, not just configured.

**GraphViz is optional, never required.** If `dot` is not on PATH, every
`lankit` command works normally. `lankit diagram` gracefully falls back:

```
$ lankit diagram
  graphviz not found — install it to generate network diagrams.
  brew install graphviz  /  apt install graphviz

  Showing tree view instead:

  Maple (MikroTik hAP ax³)
  ├── trusted   10.10.0.0/24  ● 8 devices
  ├── iot       10.30.0.0/24  ● 4 devices
  ├── servers   10.40.0.0/24  ● 2 devices
  ├── guest     10.50.0.0/24  ● 1 device
  └── quarantine 10.70.0.0/24  ⚠ 1 unknown
```

The README diagram is the reason to install GraphViz — it shows what you'd
be missing. The tree is always available and sufficient for day-to-day use.

**Generated diagram formats:**

```bash
lankit diagram              # PNG (default, opens in viewer)
lankit diagram --svg        # SVG (for web embedding)
lankit diagram --dot        # raw .dot source (for editors/CI)
lankit diagram --ascii      # ASCII via graph-easy if installed
```

The `.dot` source is also written to `generated/network.dot` on every
`lankit provision` run, so the diagram stays current automatically.
