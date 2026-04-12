# Test Harness Design

## Purpose and scope

This document is the design reference for lankit's automated test harness. It covers
what each test layer tests, how `MockRouterConnection` works, how to capture fixtures,
and how to run the suite. It is intended for contributors adding tests or changing
tested code paths.

**Relationship to `TESTING.md`.** `TESTING.md` in the repo root is the manual
commissioning runbook — a phased checklist for first-time setup on real hardware.
That runbook stays relevant after this harness exists; automated tests cannot replace
the human judgment required when first applying config to a production router. This
harness automates regression coverage so code changes don't silently break things
between commissioning runs. `TESTING.md` is what you follow once per router; this
is what runs on every code change.

**Current state.** lankit has no automated tests. The `tests/` directory is empty.
The only test infrastructure today is `lankit test-failsafe` — a manual CLI command
that verifies the RouterOS scheduler-based auto-revert fires correctly on a live router.

## Getting started

### Install test dependencies

`pytest` is not in the main project dependencies (it's a dev tool, not a runtime
dependency). Install it alongside the package:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
pip install pytest pytest-cov pytest-mock
```

The runtime dependencies (`jinja2`, `pyyaml`, `jsonschema`, `paramiko`, `pytest-testinfra`,
etc.) come in via `pip install -e .`. Once `pyproject.toml` gains a
`[project.optional-dependencies]` dev group, this will become `pip install -e ".[dev]"`.

### Run the offline suite (no hardware)

From the repo root:

```bash
pytest tests/unit tests/integration
```

This is the default contributor workflow and the only suite that runs in CI.
All tests here must pass before a PR is merged.

### Deciding which layer to add a new test to

| I want to test... | Layer | Directory |
|---|---|---|
| Config parsing, schema validation, CHOOSE detection | 1 | `tests/unit/` |
| Jinja2 template rendering to correct `.rsc` output | 1 | `tests/unit/` |
| Snapshot save / load / rotate / index | 1 | `tests/unit/` |
| Full command flow (apply, audit, rollback, probe) | 2 | `tests/integration/` |
| Safety invariants (snapshot ordering, failsafe before import) | 2 | `tests/integration/` |
| Actual RouterOS `/import` behaviour or SFTP | 3 | `tests/hardware/` |
| Failsafe timer real-time firing | 3 | `tests/hardware/` |
| Ansible provisioning or Pi-hole DNS | 3 | `tests/hardware/` |

### A note on Layer 1 test setup

`lankit/core/config.load()` resolves the path from the `LANKIT_CONFIG` env var or
the current working directory. In unit tests, pass a `Path` to a fixture YAML file
directly rather than relying on cwd:

```python
from lankit.core.config import load
cfg = load(Path("tests/fixtures/minimal-network.yml"))
```

`lankit/core/generator.generate_all()` resolves its templates directory relative to
`__file__` (i.e. `ansible/roles/router/templates/`). It works from any cwd as long
as you're running from inside the repo. Pass a `tmp_path` fixture as the `output_dir`
to avoid writing into `ansible/generated/` during tests:

```python
def test_firewall_output(tmp_path):
    paths = generate_all(cfg, output_dir=tmp_path)
    assert (tmp_path / "03-firewall.rsc").exists()
```

## Hardware

- **Lab router**: older MikroTik model (not the production hAP ax³)
- **Lab Pi**: spare Raspberry Pi running Raspberry Pi OS
- **Isolation**: lab router runs standalone, physically separate from the production
  network — not connected to the same switch

The older router model is valuable specifically because it may have RouterOS quirks the
hAP ax³ doesn't. Differences in `/export` output format or clock output will surface
portability bugs (e.g. the clock-parsing failsafe gap) before users encounter them.

## Configuration isolation

The lab gets its own config file, never shared with production:

```
lab-network.yml          # lab-specific IPs, SSIDs, hostnames
```

All lankit commands targeting the lab use:

```bash
export LANKIT_CONFIG=lab-network.yml
```

This prevents accidentally targeting the production router.

## Lab network.yml

`lab-network.yml` is not committed — it contains real IPs, MAC addresses, and
credentials. Create it by copying `network.yml` and replacing the values:

```yaml
household_name: "Lab"
internal_domain: "internal"

segments:
  trusted:
    vlan_id: 10
    comment: "Lab trusted segment"
    subnet: "10.10.0.0/24"
    ssid: "Lab-Trusted"
    wifi_bands: [2ghz, 5ghz]
    ssid_hidden: false
    bandwidth_up: null
    bandwidth_down: null
    dns: filtered
    force_dns: false
    internet: full
    client_isolation: false

  servers:
    vlan_id: 50
    comment: "Pi-hole and other services"
    subnet: "10.50.0.0/24"
    ssid: null
    wifi_bands: []
    ssid_hidden: false
    bandwidth_up: null
    bandwidth_down: null
    dns: none
    force_dns: false
    internet: full
    client_isolation: false

permissions:
  trusted:
    can_reach: [servers]
  servers:
    can_reach: []

privacy:
  query_logging: anonymous
  query_retention: "7d"
  dashboard_visibility: admin_only
  apple_private_relay: allow

wifi_password_source: prompt
vpn: none
dnssec: false
failsafe_seconds: 120
ssh_key: "~/.ssh/id_ed25519"

hosts:
  dns_server:
    hostname: dns
    segment: servers
    ip: 10.50.0.2
    mac: "aa:bb:cc:dd:ee:ff"     # replace with actual lab Pi MAC
    services: [pihole]
    ssh_user: pi
    enabled: true

router:
  ip: 192.168.1.1                # replace with lab router IP
  ssh_user: admin
  ssh_key: "~/.ssh/id_ed25519"
  wan_interface: ether1
  lan_interface: bridge
```

You don't need all 8 production segments in the lab. Two segments (`trusted` and
`servers`) are enough to exercise the full apply/audit/rollback flow.

## Test layers (3 layers + safety invariants)

### Layer 1 — Offline unit tests (no hardware)

Tests that exercise config loading, validation, and script generation without any
SSH connection.

Covers:
- `lankit/core/config.py` — load and validate `network.yml`, detect CHOOSE markers,
  schema enforcement
- `lankit/core/generator.py` — Jinja2 rendering produces correct `.rsc` output for
  known inputs
- `lankit/core/snapshots.py` — snapshot save/load/rotate/index
- CLI argument parsing and pre-flight guards (e.g. `lankit rules` with empty
  `ansible/generated/`)

No mocking required — these are pure-Python operations.

### Layer 2 — Integration tests using MockRouterConnection

Tests that exercise the full command flow (apply, audit, probe, rollback) using a
fake router that returns captured RouterOS output instead of making SSH calls.

#### MockRouterConnection design

`RouterConnection` in `lankit/core/router.py` is a context manager with 8 methods:

```python
run(command) -> str
run_tolerant(command) -> tuple[str, str]
upload(content, remote_path) -> None
add_failsafe_scheduler(name, revert_cmd, seconds) -> None
cancel_failsafe_scheduler(name) -> None
export_config() -> str
identity() -> str
version() -> str
```

The decision is a duck-typed class in `conftest.py` — not a subclass of
`RouterConnection` and not a formal `Protocol`. `RouterConnection` has no ABC or
interface definition; it's a plain class in `lankit/core/router.py` with no
inheritance hierarchy. Adding a formal `Protocol` is the right long-term move but
requires touching the real class, which is out of scope for the initial test setup.
For now, write a standalone class that mirrors the same 8 methods.

`MockRouterConnection`:
- Accepts a fixture dict mapping command strings to `(stdout, stderr)` response pairs
- Records all method calls in order (for assertion: "did apply call `export_config`
  before the first `/import`?")
- Raises `RouterError` on demand (to test error paths)
- Acts as a context manager (implements `__enter__` / `__exit__`)

#### Fixtures

RouterOS output is captured from the lab router into plain-text fixture files.
The `MockRouterConnection` loads these files and returns their contents when a
matching command is called.

```
tests/fixtures/
  export_verbose.txt              # /export verbose
  dhcp_leases.txt                 # /ip dhcp-server lease print
  dhcp_server.txt                 # /ip dhcp-server print detail
  dhcp_network.txt                # /ip dhcp-server network print detail
  interface_vlan.txt              # /interface vlan print detail
  interface_bridge_vlan.txt       # /interface bridge vlan print detail without-paging
  interface_bridge_port.txt       # /interface bridge port print detail without-paging
  interface_wifi.txt              # /interface wifi print detail
  interface_wifi_security.txt     # /interface wifi security print detail without-paging
  firewall_filter.txt             # /ip firewall filter print detail
  firewall_mangle.txt             # /ip firewall mangle print detail without-paging
  firewall_nat.txt                # /ip firewall nat print detail without-paging
  firewall_address_list.txt       # /ip firewall address-list print detail
  ip_address.txt                  # /ip address print detail
  ip_pool.txt                     # /ip pool print detail
  identity.txt                    # /system identity print
  version.txt                     # /system resource print
  clock.txt                       # /system clock print  (required by add_failsafe_scheduler)
```

Note: `clock.txt` is required. `add_failsafe_scheduler()` in `router.py` calls
`/system clock print` and parses the `time: HH:MM:SS` line to calculate the
failsafe fire time. A mock that omits this fixture will raise `RouterError`.

Multiple fixture sets cover different router states:
- `fixtures/clean/` — factory-reset router with no lankit config
- `fixtures/provisioned/` — fully provisioned router (all 8 segments)
- `fixtures/drifted/` — router with one manually-added rogue rule

**Capturing fixtures from the lab router:**

SSH into the lab router and run each command, redirecting output to a local file.
With paramiko you can script this, or use the router's SSH interface directly:

```bash
ROUTER=192.168.1.1
USER=admin
KEY=~/.ssh/id_ed25519
DIR=tests/fixtures/provisioned

mkdir -p $DIR
ssh -i $KEY $USER@$ROUTER "/export verbose"                                        > $DIR/export_verbose.txt
ssh -i $KEY $USER@$ROUTER "/ip dhcp-server lease print"                            > $DIR/dhcp_leases.txt
ssh -i $KEY $USER@$ROUTER "/ip dhcp-server print detail"                           > $DIR/dhcp_server.txt
ssh -i $KEY $USER@$ROUTER "/ip dhcp-server network print detail"                   > $DIR/dhcp_network.txt
ssh -i $KEY $USER@$ROUTER "/interface vlan print detail"                           > $DIR/interface_vlan.txt
ssh -i $KEY $USER@$ROUTER "/interface bridge vlan print detail without-paging"     > $DIR/interface_bridge_vlan.txt
ssh -i $KEY $USER@$ROUTER "/interface bridge port print detail without-paging"     > $DIR/interface_bridge_port.txt
ssh -i $KEY $USER@$ROUTER "/interface wifi print detail"                           > $DIR/interface_wifi.txt
ssh -i $KEY $USER@$ROUTER "/interface wifi security print detail without-paging"   > $DIR/interface_wifi_security.txt
ssh -i $KEY $USER@$ROUTER "/ip firewall filter print detail"                       > $DIR/firewall_filter.txt
ssh -i $KEY $USER@$ROUTER "/ip firewall mangle print detail without-paging"        > $DIR/firewall_mangle.txt
ssh -i $KEY $USER@$ROUTER "/ip firewall nat print detail without-paging"           > $DIR/firewall_nat.txt
ssh -i $KEY $USER@$ROUTER "/ip firewall address-list print detail"                 > $DIR/firewall_address_list.txt
ssh -i $KEY $USER@$ROUTER "/ip address print detail"                               > $DIR/ip_address.txt
ssh -i $KEY $USER@$ROUTER "/ip pool print detail"                                  > $DIR/ip_pool.txt
ssh -i $KEY $USER@$ROUTER "/system identity print"                                 > $DIR/identity.txt
ssh -i $KEY $USER@$ROUTER "/system resource print"                                 > $DIR/version.txt
ssh -i $KEY $USER@$ROUTER "/system clock print"                                    > $DIR/clock.txt
```

Capture `fixtures/clean/` from a factory-reset router before running any
`lankit apply`, and `fixtures/drifted/` after manually adding a test firewall
rule. Commit the fixture files — they are static reference data, not secrets.

#### Dependency injection

Commands that currently hard-wire `RouterConnection(ip, user, key)` will accept an
optional `connection` parameter:

```python
def run_apply(config, connection=None):
    if connection is None:
        connection = RouterConnection(config.router.ip, ...)
    with connection as conn:
        ...
```

Tests pass in a `MockRouterConnection`. Production code path is unchanged.

### Layer 3 — Hardware integration tests (lab router + Pi required)

End-to-end tests that run against the real lab router and Pi. These are slower and
require the lab to be powered on and reachable, but they catch things the mock cannot:

- Actual RouterOS `/import` behavior (script syntax errors, idempotency)
- Failsafe timer fires at the correct time (timing-dependent)
- SFTP upload succeeds and files land in `/tmp/` correctly
- `lankit provision` runs Ansible against the lab Pi and services come up
- Pi-hole is reachable at the expected IP
- Ad domain blocked (doubleclick.net resolves to 0.0.0.0)
- DNS resolving (example.com returns a valid answer)
- DNSSEC validating

These tests are marked `@pytest.mark.hardware` and skipped by default:

```bash
pytest                          # runs layers 1 + 2 only
pytest -m hardware              # runs layer 3 (requires lab hardware)
```

The existing `lankit test-failsafe` command remains as the manual commissioning check
for both lab and production routers.

### Safety invariants (cross-cutting, layers 1 + 2)

lankit's mission treats rollback-first safety as a non-negotiable. The test harness
must verify these invariants explicitly, not just as side effects of other tests:

- **Snapshot before mutate.** `apply` must create a snapshot before uploading or
  importing any script. A test should assert that `MockRouterConnection` sees an
  `export_config()` call before the first `run()` call that modifies state.
- **Failsafe scheduler before import.** The failsafe revert scheduler must be
  registered before any `/import` command. Assert call ordering.
- **Rollback restores snapshot.** After a failed apply, `rollback` must restore the
  exact snapshot taken at the start — not a stale one from a previous run.
- **No apply without validation.** `apply` must reject a config that fails schema
  validation. It should never reach the router.
- **CHOOSE markers block apply.** If `network.yml` contains unresolved CHOOSE
  placeholders, `apply` must refuse to run. This is the safety net for first-time
  setup.

These are the most important tests in the harness. If only ten tests exist, five of
them should be these.

## Directory structure

```
tests/
├── conftest.py                  # shared fixtures, MockRouterConnection
├── unit/
│   ├── test_config.py           # config loading and validation
│   ├── test_generator.py        # Jinja2 rendering
│   ├── test_snapshots.py        # snapshot lifecycle
│   └── test_safety.py           # safety invariant tests (see above)
├── integration/
│   ├── test_apply.py            # apply flow with mock router
│   ├── test_audit.py            # audit parsing with fixture output
│   ├── test_probe.py            # probe with mock router
│   └── test_rollback.py         # rollback flow
├── hardware/
│   ├── test_apply_hw.py         # end-to-end apply on lab router
│   ├── test_failsafe_hw.py      # failsafe timer fires correctly
│   └── test_provision_hw.py     # Ansible + Pi-hole + DNS smoke tests
└── fixtures/
    ├── clean/                   # factory-reset router output
    ├── provisioned/             # fully provisioned router output
    └── drifted/                 # router with rogue entries
```

## Running tests

```bash
# Offline only — layers 1 + 2, no hardware required (default; runs in CI)
pytest tests/unit tests/integration

# Coverage report (offline layers only)
pytest tests/unit tests/integration --cov=lankit --cov-report=term-missing

# With lab router — adds layer 3 hardware tests
export LANKIT_CONFIG=lab-network.yml
pytest -m hardware

# Everything (requires full lab)
pytest
```

Running bare `pytest` without the lab present will error on hardware-marked tests
unless `conftest.py` registers the custom marks and skips them when the lab is
unreachable. Wire that up before running the full suite in any automated context.

## Resolved decisions

- **MockRouterConnection approach:** Duck-typed class in `conftest.py`. See the
  design note in the Layer 2 section above.
- **CI:** GitHub Actions runs layers 1 + 2 (offline tests) on push. Hardware tests
  stay manual. A self-hosted runner with SSH access to the lab router is a security
  surface that doesn't match a single-maintainer project.
- **Fixture capture:** One-time manual process using the SSH commands documented in
  the Fixtures section above. A dedicated `lankit capture-fixtures` command would be
  over-tooling for something done once per router state.

## Open questions

- **Lab router RouterOS version.** The older lab MikroTik model's RouterOS version is
  unknown. Before writing hardware tests or capturing fixtures, connect to the lab router
  and run `/system resource print` to get the version. All commands lankit uses
  (`/export verbose`, `/system clock print`, `/system scheduler add`, `/import file=...`)
  exist in both RouterOS 6.x and 7.x, but `/export` output format changed between major
  versions. Layer 2 fixture comparisons may need version-specific fixture sets if the
  formats differ meaningfully between the lab router and the production hAP ax³.

- **pytest as a dev dependency.** `pyproject.toml` has no `[project.optional-dependencies]`
  group yet. Add one before the harness is wired to CI, so `pip install -e ".[dev]"`
  installs pytest and pytest-cov automatically.

- **Safety invariant hook points.** The exact call order within `apply` — where
  `export_config()`, `add_failsafe_scheduler()`, and `/import` appear relative to each
  other — determines how `test_safety.py` assertions are structured. Review
  `lankit/cli/commands/apply.py` before writing those tests.
